"""Versions, guardrails and set state (M5.4-5.6, 5.9, 5.12; docs/03 §6, docs/06 §5).

``create_version`` is the only way content changes, for agents and people alike: it
computes the content hash, links the parent, moves the variant's pointer and evaluates the
variant's guardrails against the new version. ``refresh_set`` then re-evaluates the
set-scoped cross-account guardrail and recomputes the composite hash and states.

Guardrail outcomes are rows, never silent: a ``block`` stops that variant (or, for the
set-scoped check, the set) with a reason a person can read.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application.agents.contracts import NEUTRAL_VOICE, PlatformSpec, VoiceSummary
from mera.application.errors import ConflictError, InvalidInput
from mera.application.hashtags import banned_tags_in, registry_tags
from mera.domain.policies import text_checks
from mera.domain.policies.content_hash import (
    MediaRef,
    VariantHash,
    compute_composite_hash,
    compute_content_hash,
)
from mera.domain.value_objects.ids import uuid7
from mera.domain.value_objects.platform import Platform
from mera.domain.value_objects.posts import (
    CROSS_ACCOUNT_BLOCK,
    CROSS_ACCOUNT_WARN,
    DUPLICATE_POST_BLOCK,
    DUPLICATE_POST_WARN,
    GuardrailOutcome,
    PostSetState,
    VariantState,
    VoiceScope,
)
from mera.infrastructure.db.models import (
    AccountGroup,
    BrandVoice,
    GuardrailResult,
    PostSet,
    PostVariant,
    PostVersion,
    SocialAccount,
)
from mera.infrastructure.platforms.capabilities import capabilities_for

DUPLICATE_WINDOW = timedelta(days=90)
# States the drafting machinery (``refresh_set``) manages.
DRAFT_STATES = frozenset(
    {VariantState.DRAFTING.value, VariantState.BLOCKED.value, VariantState.SKIPPED.value}
)
# States in which content may still change. Editing a variant that is in approval, approved
# or rejected makes a new version, which supersedes the pending approval or leaves the old
# approval behind (approvals.core.on_new_version). Scheduled and later states are M8's.
EDITABLE_STATES = DRAFT_STATES | frozenset(
    {
        VariantState.NEEDS_APPROVAL.value,
        VariantState.APPROVED.value,
        VariantState.REJECTED.value,
        # Editing a scheduled (not yet publishing) account cancels its slot: the approval
        # covered the old version, which can no longer go out (M8; docs/11 DoD 9).
        VariantState.SCHEDULED.value,
    }
)


@dataclass(frozen=True, slots=True)
class ContentInput:
    body: str
    thread_parts: list[str] | None
    hashtags: list[str]
    link_url: str | None
    media: list[dict[str, Any]]


def platform_spec(platform: str) -> PlatformSpec:
    caps = capabilities_for(Platform(platform))
    return PlatformSpec(
        max_chars=caps.max_chars,
        counts_graphemes=caps.counts_graphemes,
        supports_threads=caps.supports_threads,
        conventional_hashtags=caps.conventional_hashtags,
        prohibits_duplicate_across_accounts=caps.prohibits_duplicate_across_accounts,
        link_placement=caps.link_placement,
        hook_chars=caps.hook_chars,
        target_chars=caps.target_chars,
        max_hashtags=caps.max_hashtags,
    )


def platform_name(platform: str) -> str:
    return capabilities_for(Platform(platform)).display_name


# ---------------------------------------------------------------------- brand voices


async def resolve_voice(
    s: AsyncSession, account: SocialAccount, via_group_id: uuid.UUID | None
) -> tuple[BrandVoice | None, str]:
    """Account, then the routing group, then the workspace (docs/03 §2.2: most specific)."""
    if account.default_brand_voice_id and (
        v := await s.get(BrandVoice, account.default_brand_voice_id)
    ):
        return v, "account"
    v = await _default_voice(s, VoiceScope.SOCIAL_ACCOUNT, account.id)
    if v:
        return v, "account"
    if via_group_id:
        group = await s.get(AccountGroup, via_group_id)
        if (
            group
            and group.default_brand_voice_id
            and (v := await s.get(BrandVoice, group.default_brand_voice_id))
        ):
            return v, "group"
        v = await _default_voice(s, VoiceScope.ACCOUNT_GROUP, via_group_id)
        if v:
            return v, "group"
    v = await _default_voice(s, VoiceScope.WORKSPACE, None)
    return (v, "workspace") if v else (None, "built-in")


async def _default_voice(
    s: AsyncSession, scope: VoiceScope, scope_id: uuid.UUID | None
) -> BrandVoice | None:
    q = select(BrandVoice).where(
        BrandVoice.scope_type == scope.value, BrandVoice.is_default.is_(True)
    )
    q = (
        q.where(BrandVoice.scope_id.is_(None))
        if scope_id is None
        else q.where(BrandVoice.scope_id == scope_id)
    )
    return (await s.execute(q.limit(1))).scalar_one_or_none()


def voice_summary(v: BrandVoice | None) -> VoiceSummary:
    if v is None:
        return NEUTRAL_VOICE
    return VoiceSummary(
        name=v.name,
        tone=v.tone,
        target_audience=v.target_audience,
        cta_style=v.cta_style,
        emoji_policy=v.emoji_policy,
        banned_words=tuple(v.banned_words),
        example_posts=tuple(str(e) for e in v.example_posts),
        learned_preferences=tuple(
            str(p.get("summary", p)) if isinstance(p, dict) else str(p)
            for p in v.learned_preferences
        ),
        about=v.about,
        story_bank=tuple(str(x) for x in v.story_bank),
    )


# ------------------------------------------------------------------------- versions


def content_hash_for(variant: PostVariant, content: ContentInput) -> bytes:
    media = [MediaRef(bytes.fromhex(m["checksum"]), m.get("alt_text")) for m in content.media]
    return compute_content_hash(
        platform=variant.platform,
        social_account_id=variant.social_account_id,
        body=content.body,
        thread_parts=content.thread_parts,
        hashtags=content.hashtags,
        link_url=content.link_url,
        media=media,
    )


def normalize_content(content: ContentInput) -> ContentInput:
    tags: list[str] = []
    for t in content.hashtags:
        n = text_checks.normalize_hashtag(t)
        if n and n not in tags:
            tags.append(n)
    parts = (
        [p.replace("\x00", "").strip() for p in content.thread_parts if p.strip()]
        if content.thread_parts
        else None
    )
    for m in content.media:
        checksum = str(m.get("checksum") or "")
        if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
            # Media is hashed by checksum; an entry without one would change content
            # without changing the hash (review M6).
            raise InvalidInput(
                "Every media item needs its checksum.", code="invalid_media", field="media"
            )
    return ContentInput(
        body=content.body.replace("\x00", "").strip(),
        thread_parts=parts or None,
        hashtags=tags,
        link_url=(content.link_url or "").replace("\x00", "").strip() or None,
        media=content.media,
    )


async def create_version(
    s: AsyncSession,
    variant: PostVariant,
    content: ContentInput,
    *,
    source: str,
    embedding: list[float] | None,
    voice: VoiceSummary,
    run_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    revision_instruction: str | None = None,
    confidence: float | None = None,
    compliance_flags: list[dict[str, Any]] | None = None,
    agent_notes: dict[str, Any] | None = None,
    hashtag_suggestions: list[dict[str, Any]] | None = None,
) -> PostVersion:
    if variant.state not in EDITABLE_STATES:
        # Approved, scheduled or published content moves only through M6/M8 flows, which
        # invalidate the approval; a stray edit must not move the pointer underneath it.
        raise ConflictError(
            "This account's post is past drafting and cannot be edited here.", code="variant_locked"
        )
    content = normalize_content(content)
    new_hash = content_hash_for(variant, content)
    if variant.current_version_id is not None:
        current = await s.get(PostVersion, variant.current_version_id)
        if current is not None and current.content_hash == new_hash:
            # docs/03 §6: re-saving unchanged content must not create a version (or
            # invalidate an approval).
            return current
    latest = await s.scalar(
        select(func.max(PostVersion.version_no)).where(PostVersion.post_variant_id == variant.id)
    )
    caps = capabilities_for(Platform(variant.platform))
    issues = text_checks.validate(
        platform=variant.platform,
        body=content.body,
        thread_parts=content.thread_parts,
        hashtags=content.hashtags,
        max_chars=caps.max_chars,
        counts_graphemes=caps.counts_graphemes,
        supports_threads=caps.supports_threads,
        conventional_hashtags=caps.conventional_hashtags,
        max_hashtags=caps.max_hashtags,
        link_placement=caps.link_placement,
        media_count=len(content.media),
        media_without_alt=sum(1 for m in content.media if not str(m.get("alt_text") or "").strip()),
        requires_media=caps.requires_media,
        max_media=caps.max_media,
    )
    version = PostVersion(
        id=uuid7(),
        workspace_id=variant.workspace_id,
        post_variant_id=variant.id,
        version_no=(latest or 0) + 1,
        content_hash=new_hash,
        parent_version_id=variant.current_version_id,
        revision_instruction=revision_instruction,
        body=content.body,
        thread_parts=content.thread_parts,
        hashtags=content.hashtags,
        link_url=content.link_url,
        media=content.media,
        confidence_score=confidence,
        compliance_flags=[_mask_flag(f) for f in compliance_flags or []],
        validation=[{"code": i.code, "severity": i.severity, "message": i.message} for i in issues],
        agent_notes=agent_notes or {},
        hashtag_suggestions=hashtag_suggestions or [],
        embedding=embedding,
        source=source,
        created_by_agent_run_id=run_id,
        created_by_user_id=user_id,
    )
    s.add(version)
    await s.flush()
    variant.current_version_id = version.id
    await s.flush()
    # Content changed: a pending approval for this account no longer covers it.
    from mera.application.approvals.core import on_new_version

    await on_new_version(s, variant)
    await _evaluate_variant(s, variant, version, voice, issues, run_id)
    return version


async def _evaluate_variant(
    s: AsyncSession,
    variant: PostVariant,
    version: PostVersion,
    voice: VoiceSummary,
    validation: Sequence[text_checks.ValidationIssue],
    run_id: uuid.UUID | None,
) -> None:
    text = published_text(version)
    results: list[tuple[str, str, float | None, dict[str, Any]]] = []

    banned = text_checks.find_banned(text, voice.banned_words)
    results.append(
        (
            "banned_words",
            GuardrailOutcome.BLOCK if banned else GuardrailOutcome.PASS,
            None,
            {"found": [f.excerpt for f in banned]},
        )
    )
    banned_registry, _preferred = await registry_tags(s)
    banned_tags = banned_tags_in(text, list(version.hashtags), banned_registry)
    results.append(
        (
            "banned_hashtags",
            GuardrailOutcome.BLOCK if banned_tags else GuardrailOutcome.PASS,
            None,
            {"found": banned_tags},
        )
    )
    pii = text_checks.find_pii(text)
    results.append(
        (
            "pii",
            GuardrailOutcome.BLOCK if pii else GuardrailOutcome.PASS,
            None,
            {"found": [{"kind": f.kind, "excerpt": _mask(f.excerpt)} for f in pii]},
        )
    )
    worst = max((i.severity for i in validation), key=lambda x: x == "block", default=None)
    results.append(
        (
            "platform_policy",
            GuardrailOutcome.BLOCK
            if worst == "block"
            else GuardrailOutcome.WARN
            if worst
            else GuardrailOutcome.PASS,
            None,
            {"issues": [i.message for i in validation]},
        )
    )
    dup = await _duplicate_of_own_posts(s, variant, version)
    if version.embedding is None:
        # Fail visible, not open (review H2): no embedding means the check did not run.
        results.append(
            (
                "duplicate_post",
                GuardrailOutcome.WARN,
                None,
                {"checked": False, "reason": "not checked"},
            )
        )
    elif dup is None:
        results.append(("duplicate_post", GuardrailOutcome.PASS, None, {"checked": True}))
    else:
        sim, other = dup
        outcome = (
            GuardrailOutcome.BLOCK
            if sim >= DUPLICATE_POST_BLOCK
            else GuardrailOutcome.WARN
            if sim >= DUPLICATE_POST_WARN
            else GuardrailOutcome.PASS
        )
        results.append(
            ("duplicate_post", outcome, round(sim, 4), {"similar_variant_id": str(other)})
        )
    editor_flags = version.compliance_flags or []
    if editor_flags:
        sev = {f.get("severity") for f in editor_flags}
        outcome = (
            GuardrailOutcome.BLOCK
            if "block" in sev
            else GuardrailOutcome.WARN
            if "warning" in sev
            else GuardrailOutcome.PASS
        )
        results.append(
            ("editor_review", outcome, version.confidence_score, {"issues": len(editor_flags)})
        )

    for key, result_outcome, score, details in results:
        s.add(
            GuardrailResult(
                id=uuid7(),
                workspace_id=variant.workspace_id,
                agent_run_id=run_id,
                subject_type="post_variant",
                subject_id=variant.id,
                post_set_id=variant.post_set_id,
                post_variant_id=variant.id,
                post_version_id=version.id,
                guardrail_key=key,
                outcome=str(result_outcome),
                score=score,
                details=details,
            )
        )
    await s.flush()


def published_text(version: PostVersion) -> str:
    """Everything that goes out: thread, body, tags, the link and media alt text."""
    parts = [*(version.thread_parts or []), text_checks.compose(version.body, version.hashtags)]
    if version.link_url:
        parts.append(version.link_url)
    parts += [str(m.get("alt_text") or "") for m in version.media]
    return "\n".join(p for p in parts if p)


def embedding_text(body: str, thread_parts: Sequence[str] | None) -> str:
    """What the duplicate guardrails compare: the prose of the whole post, thread included."""
    return text_checks.prose("\n".join([*(thread_parts or []), body]))


def _mask_flag(flag: dict[str, Any]) -> dict[str, Any]:
    excerpt = str(flag.get("excerpt") or "")
    for f in text_checks.find_pii(excerpt):
        excerpt = excerpt.replace(f.excerpt, _mask(f.excerpt))
    return {**flag, "excerpt": excerpt}


async def voice_for_variant(s: AsyncSession, variant: PostVariant) -> VoiceSummary:
    """The voice every evaluation uses: the variant's pinned voice, else the resolved one
    (review H1: a cleared or deleted pin must not drop the banned-word list)."""
    if variant.brand_voice_id and (v := await s.get(BrandVoice, variant.brand_voice_id)):
        return voice_summary(v)
    account = await s.get(SocialAccount, variant.social_account_id)
    assert account is not None
    resolved, _ = await resolve_voice(s, account, variant.via_account_group_id)
    return voice_summary(resolved)


def _mask(excerpt: str) -> str:
    # The audit trail should not become a second copy of the PII it caught.
    return excerpt[:2] + "…" + excerpt[-2:] if len(excerpt) > 6 else "…"


async def _duplicate_of_own_posts(
    s: AsyncSession, variant: PostVariant, version: PostVersion
) -> tuple[float, uuid.UUID] | None:
    """This account's other current drafts and posts from the last 90 days (published
    history joins in M8)."""
    if version.embedding is None:
        return None
    since = datetime.now(UTC) - DUPLICATE_WINDOW
    distance = PostVersion.embedding.cosine_distance(version.embedding)
    row = (
        await s.execute(
            select(PostVariant.id, distance.label("d"))
            .join(PostVersion, PostVersion.id == PostVariant.current_version_id)
            .join(PostSet, PostSet.id == PostVariant.post_set_id)
            .where(
                PostVariant.social_account_id == variant.social_account_id,
                PostVariant.post_set_id != variant.post_set_id,
                PostSet.state != PostSetState.ARCHIVED.value,
                PostVersion.embedding.is_not(None),
                PostVersion.created_at >= since,
            )
            .order_by(distance)
            .limit(1)
        )
    ).first()
    return (1.0 - float(row.d), row.id) if row else None


# ------------------------------------------------------------------------ set state


def shingle_overlap(a: str, b: str, n: int = 5) -> float:
    """Jaccard overlap of word n-grams (0..1)."""

    def grams(t: str) -> set[tuple[str, ...]]:
        words = t.lower().split()
        return {tuple(words[i : i + n]) for i in range(max(len(words) - n + 1, 0))}

    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def refresh_set(
    s: AsyncSession, post_set: PostSet, *, run_id: uuid.UUID | None = None
) -> None:
    """Re-evaluate the set-scoped guardrail, then the states and the composite hash."""
    variants = list(
        (
            await s.execute(
                select(PostVariant)
                .where(PostVariant.post_set_id == post_set.id)
                .order_by(PostVariant.position)
            )
        ).scalars()
    )
    versions = {
        v.id: v
        for v in (
            await s.execute(
                select(PostVersion).where(
                    PostVersion.id.in_(
                        [x.current_version_id for x in variants if x.current_version_id]
                    )
                )
            )
        ).scalars()
    }
    now = datetime.now(UTC)
    previously_blocked = bool(
        await s.scalar(
            select(func.count())
            .select_from(GuardrailResult)
            .where(
                GuardrailResult.post_set_id == post_set.id,
                GuardrailResult.subject_type == "post_set",
                GuardrailResult.superseded_at.is_(None),
                GuardrailResult.outcome == GuardrailOutcome.BLOCK.value,
            )
        )
    )
    await s.execute(
        update(GuardrailResult)
        .where(
            GuardrailResult.post_set_id == post_set.id,
            GuardrailResult.subject_type == "post_set",
            GuardrailResult.superseded_at.is_(None),
        )
        .values(superseded_at=now)
    )
    by_platform: dict[str, list[PostVariant]] = defaultdict(list)
    for v in variants:
        ver = versions.get(v.current_version_id) if v.current_version_id else None
        if v.enabled and ver is not None:
            by_platform[v.platform].append(v)
    set_blocked = False
    unchecked: list[str] = []
    pairs: list[tuple[PostVariant, PostVariant, float]] = []
    for group in by_platform.values():
        if len(group) < 2:
            continue
        for i, a in enumerate(group):
            va = versions[a.current_version_id]  # type: ignore[index]
            if va.embedding is None:
                unchecked.append(str(a.id))
                continue
            for b in group[i + 1 :]:
                vb = versions[b.current_version_id]  # type: ignore[index]
                if vb.embedding is None:
                    continue
                sim = cosine(list(va.embedding), list(vb.embedding))
                # Embedding similarity can be gamed with padding; word 5-gram overlap
                # catches the same sentences dressed up differently (review M3).
                overlap = shingle_overlap(
                    embedding_text(va.body, va.thread_parts),
                    embedding_text(vb.body, vb.thread_parts),
                )
                pairs.append((a, b, max(sim, overlap)))
    if unchecked:
        s.add(
            _set_result(
                post_set, run_id, GuardrailOutcome.WARN, None, {"unchecked_variant_ids": unchecked}
            )
        )
        # An outage must not lift a block it can no longer evaluate (review H2).
        set_blocked = previously_blocked
    for a, b, sim in pairs:
        if sim < CROSS_ACCOUNT_WARN:
            continue
        outcome = GuardrailOutcome.BLOCK if sim >= CROSS_ACCOUNT_BLOCK else GuardrailOutcome.WARN
        set_blocked = set_blocked or outcome is GuardrailOutcome.BLOCK
        s.add(
            _set_result(
                post_set,
                run_id,
                outcome,
                sim,
                {"variant_ids": [str(a.id), str(b.id)], "platform": a.platform},
            )
        )
    if pairs and not any(sim >= CROSS_ACCOUNT_WARN for _, _, sim in pairs):
        best = max(sim for _, _, sim in pairs)
        s.add(_set_result(post_set, run_id, GuardrailOutcome.PASS, best, {"pairs": len(pairs)}))

    blocked_versions = set(
        (
            await s.execute(
                select(GuardrailResult.post_version_id).where(
                    GuardrailResult.post_version_id.in_(list(versions)),
                    GuardrailResult.outcome == GuardrailOutcome.BLOCK.value,
                )
            )
        ).scalars()
    )
    for v in variants:
        if v.state not in DRAFT_STATES:
            continue  # approval and publishing states are M6/M8's to change
        if not v.enabled:
            v.state = VariantState.SKIPPED.value
        elif v.current_version_id in blocked_versions:
            v.state = VariantState.BLOCKED.value
        else:
            v.state = VariantState.DRAFTING.value
    post_set.composite_hash = compute_composite_hash(
        VariantHash(
            v.social_account_id,
            v.enabled,
            versions[v.current_version_id].content_hash
            if v.current_version_id in versions
            else None,
        )
        for v in variants
    )
    if post_set.state in (PostSetState.DRAFTING.value, PostSetState.BLOCKED.value):
        # (An open approval request keeps the set in needs_approval; approvals.core settles it.)
        post_set.state = (PostSetState.BLOCKED if set_blocked else PostSetState.DRAFTING).value
    await s.flush()


def _set_result(
    post_set: PostSet,
    run_id: uuid.UUID | None,
    outcome: GuardrailOutcome,
    score: float | None,
    details: dict[str, Any],
) -> GuardrailResult:
    return GuardrailResult(
        id=uuid7(),
        workspace_id=post_set.workspace_id,
        agent_run_id=run_id,
        subject_type="post_set",
        subject_id=post_set.id,
        post_set_id=post_set.id,
        guardrail_key="cross_account_duplicate",
        outcome=outcome.value,
        score=round(score, 4) if score is not None else None,
        details=details,
    )
