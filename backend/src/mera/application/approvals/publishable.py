"""``assert_publishable(post_variant_id)``: the one gate before any platform write
(docs/01 §3.1, M6.6). Publishing (M8) calls it per account, immediately before the adapter,
and dead-letters on failure: none of these conditions is fixed by retrying.

It takes a **variant**, never a set, so there is no call shape in which an approval for one
account could be applied to another.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application.approvals.core import kill_switch_scope, snapshot_groups
from mera.application.drafting import core as drafting
from mera.application.hashtags import banned_tags_in, registry_tags
from mera.domain.policies import text_checks
from mera.domain.state_machines.approval import Quorum, VariantApproval
from mera.domain.value_objects.platform import AccountStatus
from mera.infrastructure.db.models import (
    ApprovalRequest,
    ApprovalVariantState,
    PostSet,
    PostVariant,
    PostVersion,
    SocialAccount,
)


class NotPublishable(Exception):
    def __init__(self, check: int, code: str, message: str) -> None:
        super().__init__(f"check {check} ({code}): {message}")
        self.check = check
        self.code = code
        self.message = message


async def assert_publishable(
    s: AsyncSession,
    post_variant_id: uuid.UUID,
    *,
    now: datetime | None = None,
    approved_slot: datetime | None = None,
) -> ApprovalVariantState:
    """``approved_slot`` is the publish time the scheduler gave the slot when the approval
    placed it (ADR-0016): validity runs from that time when it is later than the decision,
    so a post approved for next week is not stale next week."""
    now = now or datetime.now(UTC)
    # 1. The variant and its current version.
    variant = await s.get(PostVariant, post_variant_id)
    if variant is None or variant.current_version_id is None:
        raise NotPublishable(1, "no_version", "The variant has no current version.")
    if not variant.enabled:
        raise NotPublishable(1, "disabled", "The variant is turned off for this post.")
    post_set = await s.get(PostSet, variant.post_set_id)
    if post_set is None or post_set.state == "archived":
        raise NotPublishable(1, "archived", "The post was archived.")
    version = await s.get(PostVersion, variant.current_version_id)
    if version is None:  # pragma: no cover - the foreign key keeps the pointer valid
        raise NotPublishable(1, "no_version", "The current version is missing.")
    # 2. Tamper check: the stored hash is what the stored content hashes to now.
    recomputed = drafting.content_hash_for(
        variant,
        drafting.ContentInput(
            version.body,
            list(version.thread_parts) if version.thread_parts else None,
            list(version.hashtags),
            version.link_url,
            list(version.media),
            version.first_comment,
        ),
    )
    if recomputed != version.content_hash:
        raise NotPublishable(2, "tampered", "Stored content does not match its hash.")
    # 3. The per-account approval anchor for this exact version.
    state = (
        await s.execute(
            select(ApprovalVariantState)
            .where(
                ApprovalVariantState.post_variant_id == variant.id,
                ApprovalVariantState.post_version_id == version.id,
            )
            .order_by(ApprovalVariantState.decided_at.desc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()
    if state is None:
        # 11. Autopilot would be the alternative; Phase 1 is Manual only (docs/11 §5).
        raise NotPublishable(3, "not_approved", "No approval covers this version.")
    # 4. Approved.
    if state.state != VariantApproval.APPROVED.value:
        raise NotPublishable(4, "not_approved", f"The approval is {state.state}.")
    # 5. Edit invalidation: the approval's hash is this version's hash.
    if state.content_hash != version.content_hash:
        raise NotPublishable(5, "content_mismatch", "The post changed after it was approved.")
    # 6. No retargeted approval: the anchor names the variant's current version.
    # Defence in depth: the anchor was selected by this version, so this holds unless the
    # query above changes. It stays, because a retargeted approval is the core failure.
    if state.post_version_id != variant.current_version_id:  # pragma: no cover
        raise NotPublishable(6, "version_mismatch", "The approval is for another version.")
    # 11. Content policy as of now: a word banned or a hashtag banned after the approval
    # still stops the post (P2-M1.2). Checked here, with the content, before timing.
    text = drafting.published_text(version)
    voice = await drafting.voice_for_variant(s, variant)
    if text_checks.find_banned(text, voice.banned_words):
        raise NotPublishable(
            11, "banned_content", "The post contains a word or phrase banned since approval."
        )
    banned_registry, _preferred = await registry_tags(s)
    if banned_tags_in(text, list(version.hashtags), banned_registry):
        raise NotPublishable(11, "banned_content", "The post uses a hashtag banned since approval.")
    req = await s.get(ApprovalRequest, state.approval_request_id)
    assert req is not None
    # 7. Staleness: the approval is used within its validity window, measured from the
    # decision or, when the approval placed the post later than that, from the time it was
    # placed for (ADR-0016). Moving a post later never extends it.
    validity = int(req.policy_snapshot.get("validity_minutes", 1440))
    decided = state.decided_at or req.approved_at
    anchor = decided
    if decided is not None and approved_slot is not None and approved_slot > decided:
        anchor = approved_slot
    if anchor is None or anchor + timedelta(minutes=validity) < now:
        raise NotPublishable(7, "approval_stale", "The approval is too old to publish on.")
    # 8. Quorum per the request's own snapshot (policy edits never rewrite history).
    snap = req.policy_snapshot.get("variants", {}).get(str(variant.id), {})
    needed = int(snap.get("required", state.required_count))
    if Quorum(snap.get("quorum", state.quorum_mode)) is Quorum.ALL_OF:
        needed = max(needed, len(snap.get("approvers") or []))
    needed = max(needed, state.required_count)  # the immutable anchor is the floor
    if state.approve_count < needed:
        raise NotPublishable(8, "quorum", "The approval did not reach its quorum.")
    # 9. Kill switch at workspace, group or account level.
    extra = [g for g in [variant.via_account_group_id] if g] + snapshot_groups(req, variant.id)
    if await kill_switch_scope(s, [variant.social_account_id], extra):
        raise NotPublishable(9, "kill_switch_engaged", "Automation is stopped for this account.")
    # 10. The account can post.
    account = await s.get(SocialAccount, variant.social_account_id)
    if account is None or account.status != AccountStatus.ACTIVE.value:
        raise NotPublishable(10, "account_unavailable", "The account is not active.")
    # An expired access token is refreshed by the adapter (M8); no credentials at all is not.
    if account.credentials_ct is None:
        raise NotPublishable(10, "credentials_missing", "The account has no stored sign-in.")
    # 12. Autopilot (P3-M3): an unattended approval holds only while its authorization is
    # live and the account's mode still allows it. Turning autopilot down stops the queue.
    from mera.application import autopilot

    auth = await autopilot.live_authorization(s, state)
    if auth is not None:
        why = await autopilot.still_permitted(s, auth)
        if why is not None or auth.content_hash != version.content_hash:
            raise NotPublishable(12, "autopilot_withdrawn", why or "The post changed.")
    return state
