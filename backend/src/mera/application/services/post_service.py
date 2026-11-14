"""Post sets, variants and versions (M5.7, 5.15; docs/04 §4.6).

Three separations from the contract hold here (docs/04 §4.6): set PATCH touches metadata
only, variant PATCH touches enablement and voice only, and content changes **only** by
creating a new version. No code path updates a version (the database refuses as well).

Authorization: ``EDIT_POSTS`` on every account a write touches (a group-scoped Editor
works on their own accounts); ``READ`` to view, with scoped members seeing only their
accounts' variants. Generation runs on the ``agents`` queue; the API only queues it.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application.agents.base import AgentContext, CostCapReached
from mera.application.agents.contracts import AccountSummary
from mera.application.agents.reviewers import HashtagRequest, SuggestedTag
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.drafting import core
from mera.application.drafting.pipeline import Drafting
from mera.application.errors import (
    ConflictError,
    InvalidInput,
    NotFoundError,
    ServiceUnavailableError,
    TooManyAttempts,
)
from mera.application.hashtags import account_evidence, apply_evidence, registry_tags
from mera.application.ingest import agent_runs
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.domain.context import WorkspaceContext
from mera.domain.policies import rbac, targets, text_checks, version_diff
from mera.domain.policies.rbac import Permission
from mera.domain.ports.llm import LLMError
from mera.domain.principal import Principal
from mera.domain.value_objects.ids import uuid7
from mera.domain.value_objects.platform import AccountStatus
from mera.domain.value_objects.posts import GenerationStatus, PostOrigin, PostSetState
from mera.infrastructure.db.models import (
    AccountGroup,
    AccountGroupMember,
    BrandVoice,
    ContentItem,
    GuardrailResult,
    PostSet,
    PostVariant,
    PostVersion,
    SocialAccount,
    Source,
    Workspace,
)
from mera.infrastructure.db.rate_limit import Limit, take
from mera.infrastructure.db.repositories.audit import record
from mera.infrastructure.db.session import workspace_transaction
from mera.infrastructure.http.safe_fetch import validate_public_url
from mera.infrastructure.queue.deferrer import Deferrer

DRAFT_TASK: Final = "drafting:draft"
MAX_TARGETS: Final = 25
STALE_GENERATION: Final = timedelta(minutes=15)
LLM_REQUEST_LIMIT: Final = Limit(capacity=30, per_seconds=60)
VERSION_CONFLICT: Final = (
    "This account's text changed since you opened it; reload and reapply your edit."
)


# ------------------------------------------------------------------------------ views


@dataclass(frozen=True, slots=True)
class GuardrailView:
    guardrail_key: str
    outcome: str
    score: float | None
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class VersionView:
    id: uuid.UUID
    version_no: int
    body: str
    thread_parts: list[str] | None
    hashtags: list[str]
    link_url: str | None
    media: list[dict[str, Any]]
    content_hash: str
    confidence_score: float | None
    compliance_flags: list[dict[str, Any]]
    validation: list[dict[str, Any]]
    agent_notes: dict[str, Any]
    hashtag_suggestions: list[dict[str, Any]]
    source: str
    revision_instruction: str | None
    char_count: int
    char_limit: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class VariantView:
    id: uuid.UUID
    social_account_id: uuid.UUID
    nickname: str
    handle: str
    account_type: str
    platform: str
    via_account_group_id: uuid.UUID | None
    group_name: str | None
    enabled: bool
    state: str
    brand_voice_id: uuid.UUID | None
    voice_name: str
    position: int
    stagger_offset_seconds: int
    version_count: int
    current_version: VersionView | None
    guardrails: list[GuardrailView]


@dataclass(frozen=True, slots=True)
class ContentRef:
    id: uuid.UUID
    title: str
    url: str
    source_name: str | None


@dataclass(frozen=True, slots=True)
class SkippedTarget:
    social_account_id: uuid.UUID
    reason: str


@dataclass(frozen=True, slots=True)
class PostSetView:
    id: uuid.UUID
    title: str
    topic_brief: str | None
    origin: str
    state: str
    content: ContentRef | None
    target_account_group_ids: list[uuid.UUID]
    target_social_account_ids: list[uuid.UUID]
    composite_hash: str | None
    generation_status: str
    generation_error: str | None
    last_run_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    variants: list[VariantView]
    set_guardrails: list[GuardrailView]
    skipped: list[SkippedTarget] = field(default_factory=list)
    # P3-M2 / P3-M5
    is_evergreen: bool = False
    recycle_after: datetime | None = None
    ab_parent_id: uuid.UUID | None = None
    ab_label: str | None = None
    ab_hypothesis: str | None = None


@dataclass(frozen=True, slots=True)
class TargetSelection:
    account_group_ids: list[uuid.UUID] = field(default_factory=list)
    social_account_ids: list[uuid.UUID] = field(default_factory=list)
    exclude_social_account_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ValidationView:
    variant_id: uuid.UUID
    issues: list[dict[str, Any]]


class PostService(WorkspaceScopedService):
    def __init__(self, sm: Any, drafting: Drafting, deferrer: Deferrer) -> None:
        super().__init__(sm)
        self._drafting = drafting
        self._deferrer = deferrer

    # ------------------------------------------------------------------------ sets

    async def create(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        content_item_id: uuid.UUID | None,
        topic_brief: str | None,
        title: str | None,
        selection: TargetSelection,
        generate: bool,
    ) -> PostSetView:
        if content_item_id is None and not (topic_brief or "").strip():
            raise InvalidInput(
                "Start from a content item or write a topic brief.",
                code="no_subject",
                field="topic_brief",
            )
        set_id = uuid7()
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            await self._writable(s, workspace_id)
            item = None
            if content_item_id is not None:
                item = await s.get(ContentItem, content_item_id)
                if item is None or item.state == "filtered":
                    raise InvalidInput(
                        "Unknown content item.",
                        code="unknown_content_item",
                        field="content_item_id",
                    )
            expansion = await _expand(s, selection, seed=str(set_id))
            if not expansion.targets:
                raise InvalidInput(
                    "Pick at least one connected, active account.",
                    code="no_targets",
                    field="targets",
                )
            if len(expansion.targets) > MAX_TARGETS:
                raise InvalidInput(
                    f"At most {MAX_TARGETS} accounts per post.",
                    code="too_many_targets",
                    field="targets",
                )
            memberships = await _memberships(s, [t.social_account_id for t in expansion.targets])
            for t in expansion.targets:
                self._require_for_account(
                    principal,
                    Permission.EDIT_POSTS,
                    workspace_id,
                    t.social_account_id,
                    memberships[t.social_account_id],
                )
            ps = PostSet(
                id=set_id,
                workspace_id=workspace_id,
                content_item_id=content_item_id,
                title=(title or (item.title if item else topic_brief or "Untitled"))[:200].strip(),
                topic_brief=(topic_brief or "").strip() or None,
                origin=PostOrigin.MANUAL.value,
                state=PostSetState.DRAFTING.value,
                target_account_group_ids=list(dict.fromkeys(selection.account_group_ids)),
                target_social_account_ids=list(dict.fromkeys(selection.social_account_ids)),
                generation_status=GenerationStatus.IDLE.value,
                created_by_user_id=user.user_id,
            )
            s.add(ps)
            await s.flush()
            for pos, t in enumerate(expansion.targets):
                await self._add_variant(s, ps, t, pos)
            await core.refresh_set(s, ps)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "post_set.created",
                    workspace_id=workspace_id,
                    subject_type="post_set",
                    subject_id=set_id,
                    after={
                        "targets": [str(t.social_account_id) for t in expansion.targets],
                        "content_item_id": str(content_item_id) if content_item_id else None,
                    },
                ),
            )
            skipped = [SkippedTarget(x.social_account_id, x.reason) for x in expansion.skipped]
        if generate:
            await self._queue(user, workspace_id, set_id, meta, mode="create")
        return _with_skipped(await self.get(user, workspace_id, set_id, meta), skipped)

    async def _add_variant(
        self, s: AsyncSession, ps: PostSet, t: targets.Target, position: int
    ) -> PostVariant:
        account = await s.get(SocialAccount, t.social_account_id)
        assert account is not None
        voice, _ = await core.resolve_voice(s, account, t.via_account_group_id)
        v = PostVariant(
            id=uuid7(),
            workspace_id=ps.workspace_id,
            post_set_id=ps.id,
            social_account_id=t.social_account_id,
            via_account_group_id=t.via_account_group_id,
            platform=t.platform,
            brand_voice_id=voice.id if voice else None,
            enabled=True,
            position=position,
            stagger_offset_seconds=t.stagger_offset_seconds,
        )
        s.add(v)
        await s.flush()
        return v

    async def list_sets(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        state: str | None,
        account_id: uuid.UUID | None,
        group_id: uuid.UUID | None,
        limit: int,
        include_archived: bool,
    ) -> list[PostSetView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id, account_group_id=group_id)
            q = select(PostSet).order_by(PostSet.created_at.desc()).limit(limit)
            if state:
                q = q.where(PostSet.state == state)
            elif not include_archived:
                q = q.where(PostSet.state != PostSetState.ARCHIVED.value)
            if account_id:
                q = q.where(
                    PostSet.id.in_(
                        select(PostVariant.post_set_id).where(
                            PostVariant.social_account_id == account_id
                        )
                    )
                )
            if group_id:
                q = q.where(
                    PostSet.id.in_(
                        select(PostVariant.post_set_id).where(
                            PostVariant.via_account_group_id == group_id
                        )
                    )
                )
            if principal.member_scope.restricted:
                # Filter before the limit, so a scoped member gets full pages (review M1).
                reachable = await _reachable(s, principal)
                q = q.where(
                    PostSet.id.in_(
                        select(PostVariant.post_set_id).where(
                            PostVariant.social_account_id.in_(list(reachable))
                        )
                    )
                )
            sets = list((await s.execute(q)).scalars())
            views = [await _set_view(s, principal, ps, summary=True) for ps in sets]
            return [v for v in views if v.variants or not principal.member_scope.restricted]

    async def get(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, set_id: uuid.UUID, meta: RequestMeta
    ) -> PostSetView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            ps = await _get_set(s, set_id)
            view = await _set_view(s, principal, ps, summary=False)
            if principal.member_scope.restricted and not view.variants:
                raise NotFoundError("Post set not found.")
            return view

    async def update_set(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        set_id: uuid.UUID,
        meta: RequestMeta,
        *,
        title: str | None,
        topic_brief: str | None,
    ) -> PostSetView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            ps = await _get_set(s, set_id, lock=True)
            await self._require_all(s, principal, workspace_id, ps)
            await self._writable(s, workspace_id)
            if title is not None:
                ps.title = title.strip()[:200] or ps.title
            if topic_brief is not None:
                ps.topic_brief = topic_brief.strip() or None
        return await self.get(user, workspace_id, set_id, meta)

    async def archive(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, set_id: uuid.UUID, meta: RequestMeta
    ) -> None:
        """``DELETE`` archives: versions are immutable and kept (ADR-0004)."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            ps = await _get_set(s, set_id, lock=True)
            await self._require_all(s, principal, workspace_id, ps)
            await self._writable(s, workspace_id)
            if ps.state not in (
                PostSetState.DRAFTING.value,
                PostSetState.BLOCKED.value,
                PostSetState.ARCHIVED.value,
            ):
                raise ConflictError(
                    "Only drafts can be archived; this post is past drafting.",
                    code="post_set_locked",
                )
            if ps.state != PostSetState.ARCHIVED.value:
                ps.state = PostSetState.ARCHIVED.value
                ps.archived_at = datetime.now(UTC)
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "post_set.archived",
                        workspace_id=workspace_id,
                        subject_type="post_set",
                        subject_id=set_id,
                    ),
                )

    # ------------------------------------------------------------------ generation

    async def generate(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        set_id: uuid.UUID,
        meta: RequestMeta,
        *,
        variant_ids: Sequence[uuid.UUID] | None = None,
        instruction: str | None = None,
        mode: str = "create",
    ) -> PostSetView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            ps = await _get_set(s, set_id, lock=True)
            variants = await _variants(s, set_id)
            chosen = [
                v
                for v in variants
                if v.enabled
                and v.state in core.EDITABLE_STATES
                and (not variant_ids or v.id in set(variant_ids))
            ]
            if variant_ids and len(chosen) != len(set(variant_ids)):
                raise InvalidInput(
                    "Unknown or disabled variant.", code="unknown_variant", field="variant_ids"
                )
            if not chosen:
                raise InvalidInput("No enabled accounts to draft for.", code="no_targets")
            memberships = await _memberships(s, [v.social_account_id for v in chosen])
            for v in chosen:
                self._require_for_account(
                    principal,
                    Permission.EDIT_POSTS,
                    workspace_id,
                    v.social_account_id,
                    memberships[v.social_account_id],
                )
            await self._writable(s, workspace_id)
            if ps.state == PostSetState.ARCHIVED.value:
                raise ConflictError("This post set is archived.", code="post_set_archived")
            stale = ps.generation_requested_at is not None and (
                datetime.now(UTC) - ps.generation_requested_at > STALE_GENERATION
            )
            if (
                ps.generation_status
                in (
                    GenerationStatus.QUEUED.value,
                    GenerationStatus.RUNNING.value,
                )
                and not stale
            ):
                raise ConflictError(
                    "Drafting is already in progress for this post.", code="generation_in_progress"
                )
            if mode == "revise" and not all(v.current_version_id for v in chosen):
                raise InvalidInput(
                    "Draft it first, then ask for a rewrite.", code="nothing_to_revise"
                )
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "post_set.rewrite_requested"
                    if mode == "revise"
                    else "post_set.generate_requested",
                    workspace_id=workspace_id,
                    subject_type="post_set",
                    subject_id=set_id,
                    after={"variant_ids": [str(v.id) for v in chosen], "instruction": instruction},
                ),
            )
        await self._queue(
            user,
            workspace_id,
            set_id,
            meta,
            mode=mode,
            variant_ids=[v.id for v in chosen] if variant_ids else None,
            instruction=instruction,
        )
        return await self.get(user, workspace_id, set_id, meta)

    async def _queue(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        set_id: uuid.UUID,
        meta: RequestMeta,
        *,
        mode: str,
        variant_ids: Sequence[uuid.UUID] | None = None,
        instruction: str | None = None,
    ) -> None:
        async with self._tx(user, workspace_id, meta) as s:
            ps = await s.get(PostSet, set_id, with_for_update=True)
            if ps is None:
                raise NotFoundError("Post set not found.")
            previous = (ps.generation_status, ps.generation_error)
            ps.generation_status = GenerationStatus.QUEUED.value
            ps.generation_requested_at = datetime.now(UTC)
            ps.generation_error = None
        try:
            queued = await self._deferrer.defer(
                DRAFT_TASK,
                queue="agents",
                lock=f"draft:{set_id}",
                queueing_lock=f"draft:{set_id}",
                workspace_id=str(workspace_id),
                post_set_id=str(set_id),
                mode=mode,
                variant_ids=[str(v) for v in variant_ids] if variant_ids else None,
                instruction=instruction,
                user_id=str(user.user_id),
            )
        except Exception:
            await self._restore_status(user, workspace_id, set_id, meta, previous)
            raise
        if not queued:
            await self._restore_status(user, workspace_id, set_id, meta, previous)
            raise ConflictError(
                "Drafting is already queued for this post.", code="generation_in_progress"
            )

    async def _restore_status(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        set_id: uuid.UUID,
        meta: RequestMeta,
        previous: tuple[str, str | None],
    ) -> None:
        async with self._tx(user, workspace_id, meta) as s:
            ps = await s.get(PostSet, set_id, with_for_update=True)
            if ps is not None and ps.generation_status == GenerationStatus.QUEUED.value:
                ps.generation_status, ps.generation_error = previous

    # ---------------------------------------------------------------------- variants

    async def add_variant(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        set_id: uuid.UUID,
        meta: RequestMeta,
        *,
        account_id: uuid.UUID,
        generate: bool,
    ) -> PostSetView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            ps = await _get_set(s, set_id, lock=True)
            await self._writable(s, workspace_id)
            memberships = await _memberships(s, [account_id])
            self._require_for_account(
                principal, Permission.EDIT_POSTS, workspace_id, account_id, memberships[account_id]
            )
            if ps.state == PostSetState.ARCHIVED.value:
                raise ConflictError("This post set is archived.", code="post_set_archived")
            existing = await _variants(s, set_id)
            if len(existing) >= MAX_TARGETS:
                raise InvalidInput(
                    f"At most {MAX_TARGETS} accounts per post.", code="too_many_targets"
                )
            if any(v.social_account_id == account_id for v in existing):
                raise ConflictError(
                    "That account is already in this post.", code="duplicate_target"
                )
            expansion = await _expand(
                s, TargetSelection(social_account_ids=[account_id]), seed=f"{set_id}:{account_id}"
            )
            if not expansion.targets:
                raise InvalidInput(
                    "That account is not connected and active.",
                    code="inactive_account",
                    field="social_account_id",
                )
            last_offset = max((v.stagger_offset_seconds for v in existing), default=0)
            t = expansion.targets[0]
            variant = await self._add_variant(
                s,
                ps,
                targets.Target(
                    t.social_account_id, t.platform, None, last_offset + (180 if existing else 0)
                ),
                len(existing),
            )
            ps.target_social_account_ids = list(
                dict.fromkeys([*ps.target_social_account_ids, account_id])
            )
            await core.refresh_set(s, ps)
        if generate:
            await self._queue(
                user, workspace_id, set_id, meta, mode="create", variant_ids=[variant.id]
            )
        return await self.get(user, workspace_id, set_id, meta)

    async def update_variant(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        set_id: uuid.UUID,
        variant_id: uuid.UUID,
        meta: RequestMeta,
        *,
        enabled: bool | None,
        brand_voice_id: uuid.UUID | None,
        clear_brand_voice: bool,
    ) -> PostSetView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            ps = await _get_set(s, set_id, lock=True)
            v = await _get_variant(s, set_id, variant_id, lock=True)
            memberships = await _memberships(s, [v.social_account_id])
            self._require_for_account(
                principal,
                Permission.EDIT_POSTS,
                workspace_id,
                v.social_account_id,
                memberships[v.social_account_id],
            )
            await self._writable(s, workspace_id)
            if ps.state == PostSetState.ARCHIVED.value:
                raise ConflictError("This post set is archived.", code="post_set_archived")
            if v.state not in core.DRAFT_STATES:
                # Turning an account on or off changes the composite hash an approval binds
                # to; withdraw the approval first (or edit the post, which supersedes it).
                raise ConflictError(
                    "This account's post is in approval or past it; it can't be switched now.",
                    code="variant_locked",
                )
            changes: dict[str, Any] = {}
            if enabled is not None and enabled != v.enabled:
                v.enabled = enabled
                changes["enabled"] = enabled
            if brand_voice_id is not None or clear_brand_voice:
                voice = await s.get(BrandVoice, brand_voice_id) if brand_voice_id else None
                if brand_voice_id is not None and voice is None:
                    raise InvalidInput(
                        "Unknown brand voice.", code="unknown_brand_voice", field="brand_voice_id"
                    )
                if voice is not None and not _voice_covers(
                    voice, v, memberships[v.social_account_id]
                ):
                    # Another account's or group's voice (examples included) is not this
                    # account's to borrow (review M1).
                    raise InvalidInput(
                        "That voice belongs to another account or group.",
                        code="voice_not_applicable",
                        field="brand_voice_id",
                    )
                v.brand_voice_id = brand_voice_id
                changes["brand_voice_id"] = str(brand_voice_id) if brand_voice_id else None
            await s.flush()
            # Disabling changes the composite hash (a set-level approval no longer covers it).
            await core.refresh_set(s, ps)
            if changes:
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "post_variant.updated",
                        workspace_id=workspace_id,
                        subject_type="post_variant",
                        subject_id=variant_id,
                        social_account_id=v.social_account_id,
                        after=changes,
                    ),
                )
        return await self.get(user, workspace_id, set_id, meta)

    async def list_versions(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        set_id: uuid.UUID,
        variant_id: uuid.UUID,
        meta: RequestMeta,
    ) -> list[VersionView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            v = await _get_variant(s, set_id, variant_id)
            memberships = await _memberships(s, [v.social_account_id])
            self._require_for_account(
                principal,
                Permission.READ,
                workspace_id,
                v.social_account_id,
                memberships[v.social_account_id],
            )
            rows = (
                await s.execute(
                    select(PostVersion)
                    .where(PostVersion.post_variant_id == variant_id)
                    .order_by(PostVersion.version_no.desc())
                )
            ).scalars()
            return [_version_view(x, v.platform) for x in rows]

    async def diff_versions(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        set_id: uuid.UUID,
        variant_id: uuid.UUID,
        a: uuid.UUID,
        b: uuid.UUID,
        meta: RequestMeta,
    ) -> dict[str, Any]:
        """What changed from version ``a`` to version ``b`` of this account's post."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            v = await _get_variant(s, set_id, variant_id)
            memberships = await _memberships(s, [v.social_account_id])
            self._require_for_account(
                principal,
                Permission.READ,
                workspace_id,
                v.social_account_id,
                memberships[v.social_account_id],
            )
            va, vb = await s.get(PostVersion, a), await s.get(PostVersion, b)
            if va is None or vb is None or {va.post_variant_id, vb.post_variant_id} != {v.id}:
                raise NotFoundError("Version not found.")
            return {
                "from_version_no": va.version_no,
                "to_version_no": vb.version_no,
                **version_diff.diff(va, vb),
            }

    async def create_version(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        set_id: uuid.UUID,
        variant_id: uuid.UUID,
        meta: RequestMeta,
        *,
        content: core.ContentInput,
        based_on_version_id: uuid.UUID | None,
    ) -> PostSetView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            ps = await _get_set(s, set_id)
            v = await _get_variant(s, set_id, variant_id)
            memberships = await _memberships(s, [v.social_account_id])
            self._require_for_account(
                principal,
                Permission.EDIT_POSTS,
                workspace_id,
                v.social_account_id,
                memberships[v.social_account_id],
            )
            await self._writable(s, workspace_id)
            if ps.state == PostSetState.ARCHIVED.value:
                raise ConflictError("This post set is archived.", code="post_set_archived")
            if v.current_version_id is not None and based_on_version_id is None:
                # Optimistic concurrency (docs/04 §3.6) is not optional once there is text.
                raise InvalidInput(
                    "Say which version you edited (based_on_version_id).",
                    code="based_on_version_required",
                    field="based_on_version_id",
                )
            if v.current_version_id != based_on_version_id:
                raise ConflictError(VERSION_CONFLICT, code="version_conflict")
            if not content.body.strip() and not content.thread_parts:
                raise InvalidInput("The post is empty.", code="empty_post", field="body")
            if content.link_url and validate_public_url(content.link_url.strip()) is not None:
                raise InvalidInput(
                    "The link must be a public http(s) URL.", code="invalid_link", field="link_url"
                )
            prior_media = []
            if v.current_version_id:
                prior_version = await s.get(PostVersion, v.current_version_id)
                prior_media = list(prior_version.media) if prior_version else []
        await self._llm_budget(user)
        normalized = core.normalize_content(
            core.ContentInput(
                content.body, content.thread_parts, content.hashtags, content.link_url, prior_media
            )
        )
        embedding = await self._embed(
            workspace_id, variant_id, core.embedding_text(normalized.body, normalized.thread_parts)
        )
        async with self._tx(user, workspace_id, meta) as s:
            # Re-checked after the provider call: access, archive and state may have moved.
            principal = await load_user_principal(s, user, workspace_id)
            ps = await _get_set(s, set_id, lock=True)
            v = await _get_variant(s, set_id, variant_id, lock=True)
            memberships = await _memberships(s, [v.social_account_id])
            self._require_for_account(
                principal,
                Permission.EDIT_POSTS,
                workspace_id,
                v.social_account_id,
                memberships[v.social_account_id],
            )
            await self._writable(s, workspace_id)
            if ps.state == PostSetState.ARCHIVED.value:
                raise ConflictError("This post set is archived.", code="post_set_archived")
            if v.current_version_id != based_on_version_id:
                raise ConflictError(VERSION_CONFLICT, code="version_conflict")
            prior = await s.get(PostVersion, v.current_version_id) if v.current_version_id else None
            version = await core.create_version(
                s,
                v,
                normalized,
                source="human",
                embedding=embedding,
                voice=await core.voice_for_variant(s, v),
                user_id=user.user_id,
                # Agent notes and suggestions describe the agent's text; a person's edit
                # keeps only the suggestions list for the hashtag picker.
                hashtag_suggestions=list(prior.hashtag_suggestions) if prior else [],
            )
            await core.refresh_set(s, ps)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "post_version.created",
                    workspace_id=workspace_id,
                    subject_type="post_version",
                    subject_id=version.id,
                    social_account_id=v.social_account_id,
                    subject_content_hash=version.content_hash,
                    after={"variant_id": str(v.id), "version_no": version.version_no},
                ),
            )
        return await self.get(user, workspace_id, set_id, meta)

    async def _embed(
        self, workspace_id: uuid.UUID, variant_id: uuid.UUID, text: str
    ) -> list[float] | None:
        run = await agent_runs.start_run(
            self._sm,
            workspace_id,
            workflow_key="manual_edit",
            trigger="manual",
            subject_type="post_variant",
            subject_id=variant_id,
        )
        ctx = AgentContext(
            self._sm,
            run,
            self._drafting.config.daily_cost_cap_usd,
            monthly_cap_usd=self._drafting.config.monthly_cost_cap_usd,
        )
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            spent = await agent_runs.spent_today(s, agent_keys=ctx.cap_family)
        # Over the cap the version is still saved; the duplicate checks then report
        # "not checked" rather than spending past the budget.
        vec = (
            None
            if spent >= ctx.daily_cap_usd
            else await self._drafting.embed_for_version(ctx, text, variant_id)
        )
        await agent_runs.finish_run(
            self._sm, run, state="succeeded" if vec is not None else "failed"
        )
        return vec

    async def suggest_hashtags(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        set_id: uuid.UUID,
        variant_id: uuid.UUID,
        meta: RequestMeta,
    ) -> list[SuggestedTag]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            ps = await _get_set(s, set_id)
            v = await _get_variant(s, set_id, variant_id)
            memberships = await _memberships(s, [v.social_account_id])
            self._require_for_account(
                principal,
                Permission.EDIT_POSTS,
                workspace_id,
                v.social_account_id,
                memberships[v.social_account_id],
            )
            version = (
                await s.get(PostVersion, v.current_version_id) if v.current_version_id else None
            )
            if version is None:
                raise InvalidInput("Draft it first.", code="nothing_to_tag")
            await self._writable(s, workspace_id)
            voice = await core.voice_for_variant(s, v)
            banned, preferred = await registry_tags(s)
            account = await s.get(SocialAccount, v.social_account_id)
            assert account is not None
            item = await s.get(ContentItem, ps.content_item_id) if ps.content_item_id else None
            req = HashtagRequest(
                account=AccountSummary(
                    account.nickname,
                    account.handle,
                    account.account_type,
                    v.platform,
                    core.platform_name(v.platform),
                ),
                platform=core.platform_spec(v.platform),
                body=version.body,
                topics=tuple(item.topics or ()) if item else (),
                banned_tags=(*voice.banned_words, *sorted(banned)),
                preferred_tags=tuple(sorted(preferred)),
                evidence=await account_evidence(s, account.id),
            )
        await self._llm_budget(user)
        run = await agent_runs.start_run(
            self._sm,
            workspace_id,
            workflow_key="suggest_hashtags",
            trigger="manual",
            subject_type="post_variant",
            subject_id=variant_id,
        )
        ctx = AgentContext(
            self._sm,
            run,
            self._drafting.config.daily_cost_cap_usd,
            monthly_cap_usd=self._drafting.config.monthly_cost_cap_usd,
        )
        try:
            out = await self._drafting.hashtag.run(ctx, req, post_variant_id=variant_id)
        except CostCapReached as exc:
            await agent_runs.finish_run(self._sm, run, state="failed", error=str(exc))
            raise ServiceUnavailableError(
                "Today's drafting budget is spent; try again tomorrow."
                if exc.period == "daily"
                else "This month's AI budget for the workspace is spent."
            ) from exc
        except LLMError as exc:
            await agent_runs.finish_run(self._sm, run, state="failed", error=exc.kind)
            raise ServiceUnavailableError("Hashtag suggestions are unavailable right now.") from exc
        await agent_runs.finish_run(self._sm, run, state="succeeded")
        never = {b.lower() for b in req.banned_tags}
        return [
            t
            for t in apply_evidence(out.tags, req.evidence)
            if t.tag.lstrip("#").lower() not in never
        ]

    async def validate(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, set_id: uuid.UUID, meta: RequestMeta
    ) -> list[ValidationView]:
        view = await self.get(user, workspace_id, set_id, meta)
        out = []
        for v in view.variants:
            cv = v.current_version
            out.append(
                ValidationView(
                    v.id,
                    list(cv.validation)
                    if cv
                    else [
                        {"code": "not_drafted", "severity": "block", "message": "Not drafted yet."}
                    ],
                )
            )
        return out

    # ---------------------------------------------------------------------- helpers

    async def _require_all(
        self, s: AsyncSession, principal: Principal, workspace_id: uuid.UUID, ps: PostSet
    ) -> None:
        variants = await _variants(s, ps.id)
        memberships = await _memberships(s, [v.social_account_id for v in variants])
        for v in variants:
            self._require_for_account(
                principal,
                Permission.EDIT_POSTS,
                workspace_id,
                v.social_account_id,
                memberships[v.social_account_id],
            )
        if not variants:
            rbac.require(principal, Permission.EDIT_POSTS, workspace_id)

    async def _llm_budget(self, user: AuthenticatedUser) -> None:
        """Requests that call a model synchronously get their own per-actor budget."""
        actor = user.api_key.api_key_id if user.api_key else user.user_id
        decision = await take(self._sm, f"llm_request:{actor}", LLM_REQUEST_LIMIT)
        if not decision.allowed:
            raise TooManyAttempts(
                "Too many drafting requests; try again shortly.", retry_after=decision.retry_after
            )

    async def _writable(self, s: AsyncSession, workspace_id: uuid.UUID) -> None:
        ws = await s.get(Workspace, workspace_id)
        if ws is None:
            raise NotFoundError("Workspace not found.")
        self._ensure_writable(ws)


async def _reachable(s: AsyncSession, principal: Principal) -> set[uuid.UUID]:
    scope = principal.member_scope
    via_groups = set(
        (
            await s.execute(
                select(AccountGroupMember.social_account_id).where(
                    AccountGroupMember.account_group_id.in_(list(scope.group_ids))
                )
            )
        ).scalars()
    )
    return set(scope.account_ids) | via_groups


async def _voice_label(
    s: AsyncSession, v: PostVariant, account: SocialAccount, names: dict[uuid.UUID, str]
) -> str:
    """The voice drafting and the guardrails actually use, and where it comes from."""
    if v.brand_voice_id:
        return names.get(v.brand_voice_id, "Unknown voice")
    resolved, level = await core.resolve_voice(s, account, v.via_account_group_id)
    return f"{resolved.name} (from {level})" if resolved else "Built-in default"


def _voice_covers(voice: BrandVoice, variant: PostVariant, account_groups: list[uuid.UUID]) -> bool:
    if voice.scope_type == "workspace":
        return True
    if voice.scope_type == "social_account":
        return voice.scope_id == variant.social_account_id
    return voice.scope_id in account_groups


def _with_skipped(view: PostSetView, skipped: list[SkippedTarget]) -> PostSetView:
    return PostSetView(
        **{f: getattr(view, f) for f in view.__dataclass_fields__ if f != "skipped"},
        skipped=skipped,
    )


async def _get_set(s: AsyncSession, set_id: uuid.UUID, *, lock: bool = False) -> PostSet:
    ps = await s.get(PostSet, set_id, with_for_update=lock)
    if ps is None:
        raise NotFoundError("Post set not found.")
    return ps


async def _get_variant(
    s: AsyncSession, set_id: uuid.UUID, variant_id: uuid.UUID, *, lock: bool = False
) -> PostVariant:
    v = await s.get(PostVariant, variant_id, with_for_update=lock)
    if v is None or v.post_set_id != set_id:
        raise NotFoundError("Variant not found.")
    return v


async def _variants(s: AsyncSession, set_id: uuid.UUID) -> list[PostVariant]:
    return list(
        (
            await s.execute(
                select(PostVariant)
                .where(PostVariant.post_set_id == set_id)
                .order_by(PostVariant.position)
            )
        ).scalars()
    )


async def _memberships(
    s: AsyncSession, account_ids: list[uuid.UUID]
) -> defaultdict[uuid.UUID, list[uuid.UUID]]:
    out: defaultdict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    if account_ids:
        rows = await s.execute(
            select(AccountGroupMember.social_account_id, AccountGroupMember.account_group_id).where(
                AccountGroupMember.social_account_id.in_(account_ids)
            )
        )
        for aid, gid in rows.all():
            out[aid].append(gid)
    return out


async def _expand(s: AsyncSession, sel: TargetSelection, *, seed: str) -> targets.Expansion:
    group_rows = list(
        (
            await s.execute(select(AccountGroup).where(AccountGroup.id.in_(sel.account_group_ids)))
        ).scalars()
    )
    if len(group_rows) != len(set(sel.account_group_ids)):
        raise InvalidInput("Unknown group.", code="unknown_group", field="targets")
    members = (
        await s.execute(
            select(AccountGroupMember.account_group_id, AccountGroupMember.social_account_id)
            .where(AccountGroupMember.account_group_id.in_([g.id for g in group_rows]))
            .order_by(AccountGroupMember.account_group_id, AccountGroupMember.position)
        )
    ).all()
    member_map: defaultdict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for gid, aid in members:
        member_map[gid].append(aid)
    groups = {
        g.id: targets.GroupInfo(
            g.id,
            tuple(member_map[g.id]),
            g.stagger_mode,
            g.stagger_min_seconds,
            g.stagger_max_seconds,
        )
        for g in group_rows
    }
    account_ids = {
        *sel.social_account_ids,
        *(a for _, a in members),
        *sel.exclude_social_account_ids,
    }
    accounts = {
        a.id: targets.AccountInfo(a.id, a.platform, a.status == AccountStatus.ACTIVE.value)
        for a in (
            await s.execute(select(SocialAccount).where(SocialAccount.id.in_(list(account_ids))))
        ).scalars()
    }
    return targets.expand(
        group_ids=sel.account_group_ids,
        account_ids=sel.social_account_ids,
        exclude_ids=sel.exclude_social_account_ids,
        groups=groups,
        accounts=accounts,
        seed=seed,
    )


def _in_scope(principal: Principal, account_id: uuid.UUID, groups: list[uuid.UUID]) -> bool:
    scope = principal.member_scope
    return (
        not scope.restricted
        or account_id in scope.account_ids
        or any(g in scope.group_ids for g in groups)
    )


def _guardrail(g: GuardrailResult) -> GuardrailView:
    return GuardrailView(g.guardrail_key, g.outcome, g.score, dict(g.details))


def _version_view(v: PostVersion, platform: str) -> VersionView:
    spec = core.platform_spec(platform)
    composed = text_checks.compose(v.body, v.hashtags)
    return VersionView(
        id=v.id,
        version_no=v.version_no,
        body=v.body,
        thread_parts=v.thread_parts,
        hashtags=list(v.hashtags),
        link_url=v.link_url,
        media=list(v.media),
        content_hash=v.content_hash.hex(),
        confidence_score=v.confidence_score,
        compliance_flags=list(v.compliance_flags),
        validation=list(v.validation),
        agent_notes=dict(v.agent_notes),
        hashtag_suggestions=list(v.hashtag_suggestions),
        source=v.source,
        revision_instruction=v.revision_instruction,
        char_count=text_checks.weighted_length(
            composed, platform=platform, counts_graphemes=spec.counts_graphemes
        ),
        char_limit=spec.max_chars,
        created_at=v.created_at,
    )


async def _set_view(
    s: AsyncSession, principal: Principal, ps: PostSet, *, summary: bool
) -> PostSetView:
    variants = await _variants(s, ps.id)
    memberships = await _memberships(s, [v.social_account_id for v in variants])
    variants = [
        v
        for v in variants
        if _in_scope(principal, v.social_account_id, memberships[v.social_account_id])
    ]
    accounts = {
        a.id: a
        for a in (
            await s.execute(
                select(SocialAccount).where(
                    SocialAccount.id.in_([v.social_account_id for v in variants])
                )
            )
        ).scalars()
    }
    group_names = dict((await s.execute(select(AccountGroup.id, AccountGroup.name))).all())
    voice_names = dict((await s.execute(select(BrandVoice.id, BrandVoice.name))).all())
    version_ids = [v.current_version_id for v in variants if v.current_version_id]
    versions = (
        {
            x.id: x
            for x in (
                await s.execute(select(PostVersion).where(PostVersion.id.in_(version_ids)))
            ).scalars()
        }
        if version_ids
        else {}
    )
    counts = dict(
        (
            await s.execute(
                select(PostVersion.post_variant_id, func.count())
                .where(PostVersion.post_variant_id.in_([v.id for v in variants]))
                .group_by(PostVersion.post_variant_id)
            )
        ).all()
    )
    results: defaultdict[uuid.UUID, list[GuardrailView]] = defaultdict(list)
    if version_ids:
        for g in (
            await s.execute(
                select(GuardrailResult)
                .where(GuardrailResult.post_version_id.in_(version_ids))
                .order_by(GuardrailResult.created_at)
            )
        ).scalars():
            results[g.post_version_id].append(_guardrail(g))  # type: ignore[index]
    set_results = [
        _guardrail(g)
        for g in (
            await s.execute(
                select(GuardrailResult).where(
                    GuardrailResult.post_set_id == ps.id,
                    GuardrailResult.subject_type == "post_set",
                    GuardrailResult.superseded_at.is_(None),
                )
            )
        ).scalars()
    ]
    content = None
    if ps.content_item_id:
        item = await s.get(ContentItem, ps.content_item_id)
        if item:
            src = await s.get(Source, item.source_id) if item.source_id else None
            content = ContentRef(item.id, item.title, item.url, src.name if src else None)
    vviews = []
    for v in variants:
        a = accounts[v.social_account_id]
        ver = versions.get(v.current_version_id) if v.current_version_id else None
        vviews.append(
            VariantView(
                id=v.id,
                social_account_id=v.social_account_id,
                nickname=a.nickname,
                handle=a.handle,
                account_type=a.account_type,
                platform=v.platform,
                via_account_group_id=v.via_account_group_id,
                group_name=group_names.get(v.via_account_group_id)
                if v.via_account_group_id
                else None,
                enabled=v.enabled,
                state=v.state,
                brand_voice_id=v.brand_voice_id,
                voice_name=await _voice_label(s, v, a, voice_names),
                position=v.position,
                stagger_offset_seconds=v.stagger_offset_seconds,
                version_count=int(counts.get(v.id, 0)),
                current_version=_version_view(ver, v.platform) if ver else None,
                guardrails=results.get(ver.id, []) if ver else [],
            )
        )
    restricted = principal.member_scope.restricted
    visible_ids = {str(v.id) for v in variants}
    if restricted:
        # A scoped member sees their part of the set and nothing that names the rest
        # (review M1): other targets, other accounts' guardrail pairs, run links, errors.
        set_results = [
            g
            for g in set_results
            if not g.details.get("variant_ids") or set(g.details["variant_ids"]) <= visible_ids
        ]
    return PostSetView(
        id=ps.id,
        title=ps.title,
        topic_brief=ps.topic_brief,
        origin=ps.origin,
        state=ps.state,
        content=content,
        target_account_group_ids=[
            g
            for g in ps.target_account_group_ids
            if not restricted or g in principal.member_scope.group_ids
        ],
        target_social_account_ids=[
            a for a in ps.target_social_account_ids if not restricted or a in accounts
        ],
        composite_hash=ps.composite_hash.hex() if ps.composite_hash else None,
        generation_status=ps.generation_status,
        generation_error=(
            "Drafting did not finish for every account."
            if restricted and ps.generation_error
            else ps.generation_error
        ),
        last_run_id=None if restricted else ps.last_run_id,
        created_at=ps.created_at,
        updated_at=ps.updated_at,
        variants=vviews,
        set_guardrails=set_results,
        is_evergreen=ps.is_evergreen,
        recycle_after=ps.recycle_after,
        ab_parent_id=ps.ab_parent_id,
        ab_label=ps.ab_label,
        ab_hypothesis=ps.ab_hypothesis,
    )
