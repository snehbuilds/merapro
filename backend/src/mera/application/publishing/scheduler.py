"""Placing approved accounts on the calendar (M8.4, M8.6; ADR-0014 §2: the scheduler always
places the approved variants).

Called inside the decision's transaction, under the request lock, for every account the
decision approved. One ``scheduled_post`` per account; its idempotency key is unique, so
approving twice can never schedule twice.

The slot is ``base + stagger offset`` moved forward past the account's quiet hours, its
daily cap and the minimum spacing from its other posts (``domain.policies.scheduling``).
``base`` is the time the requester asked for, or "now" (plus a short lead) when they did
not. It is computed **once** and stored.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application.events import emit
from mera.application.settings_model import level_values
from mera.domain.policies.scheduling import (
    PlacementImpossible,
    idempotency_key,
    next_posting_time,
    place,
)
from mera.domain.policies.settings_resolution import Level, resolve
from mera.domain.state_machines.approval import VariantApproval
from mera.domain.value_objects.ids import uuid7
from mera.domain.value_objects.posts import PostSetState, VariantState
from mera.domain.value_objects.publishing import MIN_ACCOUNT_SPACING_SECONDS, ScheduledState
from mera.infrastructure.db.models import (
    AccountGroup,
    ApprovalRequest,
    ApprovalVariantState,
    PostSet,
    PostVariant,
    PublishedPost,
    ScheduledPost,
    SocialAccount,
    Workspace,
)

LEAD = timedelta(seconds=60)


async def effective_rules(
    s: AsyncSession, variant: PostVariant
) -> tuple[str, dict[str, Any] | None, int | None]:
    """The account's timezone (the workspace's), quiet hours and daily cap, resolved
    strictest across workspace, the routing group and the account (ADR-0014 §4)."""
    ws = await s.get(Workspace, variant.workspace_id)
    account = await s.get(SocialAccount, variant.social_account_id)
    group = (
        await s.get(AccountGroup, variant.via_account_group_id)
        if variant.via_account_group_id
        else None
    )
    levels: dict[Level, dict[str, Any] | None] = {
        Level.WORKSPACE: level_values(ws.settings if ws else None),
        Level.GROUP: level_values(group.settings) if group is not None else None,
        Level.ACCOUNT: level_values(account.settings) if account is not None else None,
    }
    quiet = resolve("quiet_hours", levels).value
    cap = resolve("daily_post_cap", levels).value
    return (ws.timezone if ws else "UTC"), quiet, (int(cap) if cap is not None else None)


async def posting_times_for(s: AsyncSession, variant: PostVariant) -> list[dict[str, Any]] | None:
    """The account's posting times, most specific first: the account's own, else its
    routing group's, else the workspace's (docs/03 §4, P2-M1.6)."""
    ws = await s.get(Workspace, variant.workspace_id)
    account = await s.get(SocialAccount, variant.social_account_id)
    group = (
        await s.get(AccountGroup, variant.via_account_group_id)
        if variant.via_account_group_id
        else None
    )
    return resolved_posting_times(ws, group, account)


def resolved_posting_times(
    ws: Workspace | None, group: AccountGroup | None, account: SocialAccount | None
) -> list[dict[str, Any]] | None:
    levels: dict[Level, dict[str, Any] | None] = {
        Level.WORKSPACE: level_values(ws.settings if ws else None),
        Level.GROUP: level_values(group.settings, posting_times=group.posting_schedule)
        if group is not None
        else None,
        Level.ACCOUNT: level_values(account.settings, posting_times=account.default_posting_times)
        if account is not None
        else None,
    }
    value = resolve("posting_times", levels).value
    return list(value) if value else None


async def slot_base(
    s: AsyncSession, req: ApprovalRequest, variant: PostVariant, now: datetime
) -> datetime:
    """A requested time is honoured as asked. Without one, the account goes out at its next
    posting time when it has any, else from the set's first approval."""
    return (await slot_base_with_reason(s, req, variant, now))[0]


async def slot_base_with_reason(
    s: AsyncSession, req: ApprovalRequest, variant: PostVariant, now: datetime
) -> tuple[datetime, dict[str, Any] | None]:
    """As ``slot_base``, plus the Scheduler agent (P2-M7): with no requested time and no
    posting times, its best-scoring slot from the account's own history, if it has one.
    Either way the result is only a starting point for ``place``."""
    base = base_time(req, now)
    if req.scheduled_for is not None and req.scheduled_for > now:
        return base, None
    times = await posting_times_for(s, variant)
    tz, _quiet, _cap = await effective_rules(s, variant)
    if times:
        return next_posting_time(base, times, tz) or base, None
    from mera.application.scheduling import as_placement, proposal_for

    proposal = await proposal_for(s, variant.social_account_id, after=base, tz_name=tz)
    if proposal is None:
        return base, None
    return proposal.at, as_placement(proposal)


async def account_slots(
    s: AsyncSession, account_id: uuid.UUID, around: datetime, *, exclude: uuid.UUID | None = None
) -> list[datetime]:
    lo, hi = around - timedelta(days=3), around + timedelta(days=14)
    queued = (
        await s.execute(
            select(ScheduledPost.scheduled_for).where(
                ScheduledPost.social_account_id == account_id,
                ScheduledPost.state.in_(
                    [
                        ScheduledState.QUEUED.value,
                        ScheduledState.FAILED.value,
                        ScheduledState.PUBLISHING.value,
                    ]
                ),
                ScheduledPost.scheduled_for.between(lo, hi),
                *([ScheduledPost.id != exclude] if exclude else []),
            )
        )
    ).scalars()
    published = (
        await s.execute(
            select(PublishedPost.published_at).where(
                PublishedPost.social_account_id == account_id,
                PublishedPost.published_at.between(lo, hi),
                PublishedPost.deleted_at.is_(None),
            )
        )
    ).scalars()
    return [*queued, *published]


async def place_variant(
    s: AsyncSession,
    variant: PostVariant,
    *,
    base: datetime,
    now: datetime,
    exclude: uuid.UUID | None = None,
    offset: int | None = None,
) -> tuple[datetime, list[str]]:
    tz, quiet, cap = await effective_rules(s, variant)
    existing = await account_slots(s, variant.social_account_id, base, exclude=exclude)
    try:
        p = place(
            requested=base,
            offset_seconds=variant.stagger_offset_seconds if offset is None else offset,
            existing=existing,
            tz_name=tz,
            quiet_hours=quiet,
            daily_cap=cap,
            min_spacing_seconds=MIN_ACCOUNT_SPACING_SECONDS,
            not_before=now + timedelta(seconds=30),
        )
    except PlacementImpossible:
        # A 24-hour quiet window cannot be honoured; place at the requested time and say so.
        return base + timedelta(seconds=variant.stagger_offset_seconds), ["no slot found"]
    return p.at, p.moved_by


def base_time(req: ApprovalRequest, now: datetime) -> datetime:
    if req.scheduled_for is not None and req.scheduled_for > now:
        return req.scheduled_for
    # No requested time: the set goes out from its first approval, so every account keeps
    # its stagger offset relative to the same instant.
    start = req.approved_at or now
    return max(start + LEAD, now + LEAD // 2)


async def schedule_approved(
    s: AsyncSession,
    req: ApprovalRequest,
    rows: Iterable[ApprovalVariantState],
    *,
    now: datetime | None = None,
    user_id: uuid.UUID | None = None,
) -> list[ScheduledPost]:
    now = now or datetime.now(UTC)
    out: list[ScheduledPost] = []
    for row in rows:
        if row.state != VariantApproval.APPROVED.value:
            continue
        variant = await s.get(PostVariant, row.post_variant_id)
        if (
            variant is None
            or variant.current_version_id != row.post_version_id
            or not variant.enabled
        ):
            continue
        key = idempotency_key(row.social_account_id, row.post_version_id)
        if await s.scalar(select(ScheduledPost.id).where(ScheduledPost.idempotency_key == key)):
            continue
        base, proposal = await slot_base_with_reason(s, req, variant, now)
        at, moved = await place_variant(s, variant, base=base, now=now)
        sp = ScheduledPost(
            id=uuid7(),
            workspace_id=req.workspace_id,
            post_set_id=variant.post_set_id,
            post_variant_id=variant.id,
            post_version_id=row.post_version_id,
            social_account_id=row.social_account_id,
            account_group_id=variant.via_account_group_id,
            approval_request_id=req.id,
            scheduled_for=at,
            stagger_offset_seconds=variant.stagger_offset_seconds,
            # approved_slot anchors the approval's validity (ADR-0016); later moves keep it.
            placement={
                "moved_by": moved,
                "requested": base.isoformat(),
                "approved_slot": at.isoformat(),
                # The Scheduler agent's proposal, when one was used, with its reason.
                **({"proposal": proposal} if proposal else {}),
            },
            state=ScheduledState.QUEUED.value,
            idempotency_key=key,
            next_attempt_at=at,
            created_by_user_id=user_id,
        )
        s.add(sp)
        variant.state = VariantState.SCHEDULED.value
        variant.scheduled_for = at
        out.append(sp)
        await emit(
            s,
            req.workspace_id,
            "post.scheduled",
            subject_type="scheduled_post",
            subject_id=sp.id,
            social_account_id=sp.social_account_id,
            account_group_id=sp.account_group_id,
            payload={"post_set_id": str(sp.post_set_id), "scheduled_for": at.isoformat()},
        )
    if out:
        await s.flush()
        ps = await s.get(PostSet, req.post_set_id)
        if ps is not None and ps.state in (
            PostSetState.APPROVED.value,
            PostSetState.PARTIALLY_APPROVED.value,
            PostSetState.NEEDS_APPROVAL.value,
        ):
            await rollup_set(s, ps)
    return out


async def rollup_set(s: AsyncSession, ps: PostSet) -> str:
    """The set's state from its accounts (docs/03 §8: partial failure is the normal case).
    Only the publishing half is decided here; approval states come from ``settle``."""
    if ps.state == PostSetState.ARCHIVED.value:
        return ps.state
    variants = list(
        (
            await s.execute(
                select(PostVariant).where(
                    PostVariant.post_set_id == ps.id, PostVariant.enabled.is_(True)
                )
            )
        ).scalars()
    )
    states = [v.state for v in variants]
    pub = VariantState.PUBLISHED.value
    in_flight = {VariantState.SCHEDULED.value, VariantState.PUBLISHING.value}
    waiting = {VariantState.NEEDS_APPROVAL.value, VariantState.DRAFTING.value}
    if not states:
        return ps.state
    if any(st in in_flight for st in states):
        ps.state = PostSetState.SCHEDULED.value
    elif all(st == pub for st in states):
        ps.state = PostSetState.PUBLISHED.value
    elif any(st == pub for st in states) and not any(st in waiting for st in states):
        failed = any(st == VariantState.FAILED.value for st in states)
        ps.state = (
            PostSetState.PARTIALLY_PUBLISHED.value if failed else PostSetState.PUBLISHED.value
        )
    elif any(st == pub for st in states):
        ps.state = PostSetState.PARTIALLY_PUBLISHED.value
    return ps.state
