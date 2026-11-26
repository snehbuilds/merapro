"""Scheduling and publishing endpoints (M8.12; docs/04 §4.10).

Nothing here can make an unapproved post publish: a slot exists only for an approved
version (``schedule_approved`` or the set-level schedule below, which only places
accounts whose approval anchor names their current version), and the worker runs
``assert_publishable`` before every platform call. These endpoints move times, cancel,
retry and report.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application.approvals import core as approvals
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import ConflictError, Forbidden, InvalidInput, NotFoundError
from mera.application.events import emit
from mera.application.publishing import scheduler
from mera.application.publishing.scheduler import place_variant, rollup_set
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.domain.errors import PermissionDenied
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.domain.policies.scheduling import idempotency_key, upcoming_posting_times
from mera.domain.principal import Principal
from mera.domain.state_machines.approval import VariantApproval
from mera.domain.value_objects.ids import uuid7
from mera.domain.value_objects.posts import VariantState
from mera.domain.value_objects.publishing import (
    MAX_PUBLISH_ATTEMPTS,
    MIN_ACCOUNT_SPACING_SECONDS,
    ScheduledState,
)
from mera.infrastructure.db.models import (
    AccountGroup,
    AccountGroupMember,
    ApprovalVariantState,
    DeadLetter,
    PostSet,
    PostVariant,
    PostVersion,
    PublishedComment,
    PublishedPost,
    ScheduledPost,
    SocialAccount,
    Workspace,
)
from mera.infrastructure.db.repositories.audit import record
from mera.infrastructure.queue.deferrer import Deferrer

log = structlog.get_logger(__name__)
PUBLISH_TASK: Final = "publish:publish"
MAX_ROWS: Final = 500


@dataclass(frozen=True, slots=True)
class PostingSlotView:
    social_account_id: uuid.UUID
    account_nickname: str
    platform: str
    account_group_id: uuid.UUID | None
    at: datetime
    taken: bool
    source: str


@dataclass(frozen=True, slots=True)
class ScheduledView:
    id: uuid.UUID
    post_set_id: uuid.UUID
    post_set_title: str
    post_variant_id: uuid.UUID
    post_version_id: uuid.UUID
    social_account_id: uuid.UUID
    account_nickname: str
    account_handle: str
    account_avatar_url: str | None
    platform: str
    account_group_id: uuid.UUID | None
    group_name: str | None
    group_color: str | None
    scheduled_for: datetime
    stagger_offset_seconds: int
    state: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None
    last_error: str | None
    moved_by: list[str]
    excerpt: str
    permalink: str | None
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class PublishedView:
    id: uuid.UUID
    post_set_id: uuid.UUID
    post_variant_id: uuid.UUID
    social_account_id: uuid.UUID
    account_nickname: str
    platform: str
    account_group_id: uuid.UUID | None
    platform_post_id: str
    permalink: str | None
    published_at: datetime
    excerpt: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class DeadLetterView:
    id: uuid.UUID
    subject_id: uuid.UUID
    social_account_id: uuid.UUID | None
    account_nickname: str | None
    post_set_id: uuid.UUID | None
    post_set_title: str | None
    error: str
    attempts: int
    failed_at: datetime
    resolved_at: datetime | None
    resolution: str | None


@dataclass(frozen=True, slots=True)
class SetStatusView:
    post_set_id: uuid.UUID
    state: str
    accounts: list[dict[str, Any]]


class PublishingService(WorkspaceScopedService):
    def __init__(self, sm: Any, deferrer: Deferrer | None = None) -> None:
        super().__init__(sm)
        self._deferrer = deferrer

    # ---------------------------------------------------------------------- reads

    async def list_scheduled(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        state: str | None = None,
        account_id: uuid.UUID | None = None,
        group_id: uuid.UUID | None = None,
        frm: datetime | None = None,
        to: datetime | None = None,
        post_set_id: uuid.UUID | None = None,
        limit: int = 200,
    ) -> list[ScheduledView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            q = select(ScheduledPost).order_by(ScheduledPost.scheduled_for)
            if state == "open":
                q = q.where(
                    ScheduledPost.state.in_(
                        [
                            ScheduledState.QUEUED.value,
                            ScheduledState.FAILED.value,
                            ScheduledState.PUBLISHING.value,
                        ]
                    )
                )
            elif state:
                q = q.where(ScheduledPost.state == state)
            if account_id:
                q = q.where(ScheduledPost.social_account_id == account_id)
            if group_id:
                q = q.where(ScheduledPost.account_group_id == group_id)
            if post_set_id:
                q = q.where(ScheduledPost.post_set_id == post_set_id)
            if frm:
                q = q.where(ScheduledPost.scheduled_for >= frm)
            if to:
                q = q.where(ScheduledPost.scheduled_for < to)
            rows = list((await s.execute(q.limit(min(limit, MAX_ROWS)))).scalars())
            return [v for v in [await self._scheduled_view(s, principal, r) for r in rows] if v]

    async def calendar(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        frm: datetime,
        to: datetime,
        group_id: uuid.UUID | None,
        account_id: uuid.UUID | None,
    ) -> list[ScheduledView]:
        if to <= frm or to - frm > timedelta(days=62):
            raise InvalidInput(
                "Pick a window of at most 62 days.", code="invalid_window", field="to"
            )
        rows = await self.list_scheduled(
            user,
            workspace_id,
            meta,
            account_id=account_id,
            group_id=group_id,
            frm=frm,
            to=to,
            limit=MAX_ROWS,
        )
        return [r for r in rows if r.state != ScheduledState.CANCELLED.value]

    async def posting_slots(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        account_id: uuid.UUID | None,
        group_id: uuid.UUID | None,
        days: int,
    ) -> list[PostingSlotView]:
        """Each account's upcoming posting times (docs/04 §4.10), from its own times, else
        its group's (the group asked for, or the first it belongs to), else the
        workspace's. ``taken`` marks a time within the spacing of a queued or published
        post, which placement would move past."""
        if not 1 <= days <= 14:
            raise InvalidInput("Pick 1 to 14 days.", code="invalid_window", field="days")
        now = datetime.now(UTC)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            ws = await s.get(Workspace, workspace_id)
            assert ws is not None
            q = select(SocialAccount).where(SocialAccount.status == "active")
            if account_id is not None:
                q = q.where(SocialAccount.id == account_id)
            if group_id is not None:
                q = q.join(
                    AccountGroupMember, AccountGroupMember.social_account_id == SocialAccount.id
                ).where(AccountGroupMember.account_group_id == group_id)
            out: list[PostingSlotView] = []
            for acct in (await s.execute(q.order_by(SocialAccount.nickname))).scalars():
                if not await self._can(s, principal, Permission.READ, workspace_id, acct.id):
                    continue
                gid = group_id or await s.scalar(
                    select(AccountGroupMember.account_group_id)
                    .where(AccountGroupMember.social_account_id == acct.id)
                    .order_by(AccountGroupMember.account_group_id)
                    .limit(1)
                )
                grp = await s.get(AccountGroup, gid) if gid else None
                times = scheduler.resolved_posting_times(ws, grp, acct)
                upcoming = upcoming_posting_times(now, times, ws.timezone, days=days)
                busy = await scheduler.account_slots(s, acct.id, now)
                spacing = MIN_ACCOUNT_SPACING_SECONDS
                for at in upcoming:
                    taken = any(abs((at - b).total_seconds()) < spacing for b in busy)
                    out.append(
                        PostingSlotView(
                            social_account_id=acct.id,
                            account_nickname=acct.nickname,
                            platform=acct.platform,
                            account_group_id=grp.id if grp else None,
                            at=at,
                            taken=taken,
                            source="account"
                            if acct.default_posting_times
                            else "group"
                            if grp is not None and grp.posting_schedule
                            else "workspace",
                        )
                    )
            return sorted(out, key=lambda x: (x.at, x.account_nickname))

    async def set_status(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, set_id: uuid.UUID, meta: RequestMeta
    ) -> SetStatusView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            ps = await s.get(PostSet, set_id)
            if ps is None:
                raise NotFoundError("Post set not found.")
            variants = list(
                (
                    await s.execute(
                        select(PostVariant)
                        .where(PostVariant.post_set_id == set_id)
                        .order_by(PostVariant.position)
                    )
                ).scalars()
            )
            accounts = []
            for v in variants:
                if not await self._can(
                    s, principal, Permission.READ, workspace_id, v.social_account_id
                ):
                    continue
                acct = await s.get(SocialAccount, v.social_account_id)
                sp = (
                    await s.execute(
                        select(ScheduledPost)
                        .where(ScheduledPost.post_variant_id == v.id)
                        .order_by(ScheduledPost.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                pp = (
                    (
                        await s.execute(
                            select(PublishedPost).where(PublishedPost.scheduled_post_id == sp.id)
                        )
                    ).scalar_one_or_none()
                    if sp
                    else None
                )
                pc = (
                    (
                        await s.execute(
                            select(PublishedComment)
                            .where(PublishedComment.published_post_id == pp.id)
                            .order_by(PublishedComment.created_at.desc())
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if pp
                    else None
                )
                accounts.append(
                    {
                        "post_variant_id": str(v.id),
                        "social_account_id": str(v.social_account_id),
                        "nickname": acct.nickname if acct else "",
                        "platform": v.platform,
                        "enabled": v.enabled,
                        "variant_state": v.state,
                        "scheduled_post_id": str(sp.id) if sp else None,
                        "publish_state": sp.state if sp else None,
                        "scheduled_for": sp.scheduled_for.isoformat() if sp else None,
                        "attempt_count": sp.attempt_count if sp else 0,
                        "max_attempts": MAX_PUBLISH_ATTEMPTS,
                        "last_error": sp.last_error if sp else None,
                        "permalink": pp.permalink if pp else None,
                        "published_at": pp.published_at.isoformat() if pp else None,
                        "first_comment_state": pc.state if pc else None,
                        "platform_comment_id": pc.platform_comment_id if pc else None,
                        "first_comment_error": pc.last_error if pc else None,
                    }
                )
            return SetStatusView(ps.id, ps.state, accounts)

    async def list_published(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        account_id: uuid.UUID | None,
        group_id: uuid.UUID | None,
        post_set_id: uuid.UUID | None,
        since: datetime | None,
        limit: int,
    ) -> list[PublishedView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            q = (
                select(PublishedPost)
                .where(PublishedPost.deleted_at.is_(None))
                .order_by(PublishedPost.published_at.desc())
            )
            if account_id:
                q = q.where(PublishedPost.social_account_id == account_id)
            if group_id:
                q = q.where(PublishedPost.account_group_id == group_id)
            if post_set_id:
                q = q.where(PublishedPost.post_set_id == post_set_id)
            if since:
                q = q.where(PublishedPost.published_at >= since)
            out = []
            for pp in (await s.execute(q.limit(min(limit, MAX_ROWS)))).scalars():
                if not await self._can(
                    s, principal, Permission.READ, workspace_id, pp.social_account_id
                ):
                    continue
                acct = await s.get(SocialAccount, pp.social_account_id)
                ver = await s.get(PostVersion, pp.post_version_id)
                out.append(
                    PublishedView(
                        pp.id,
                        pp.post_set_id,
                        pp.post_variant_id,
                        pp.social_account_id,
                        acct.nickname if acct else "",
                        pp.platform,
                        pp.account_group_id,
                        pp.platform_post_id,
                        pp.permalink,
                        pp.published_at,
                        _excerpt(ver.body if ver else ""),
                        pp.content_hash.hex(),
                    )
                )
            return out

    async def list_dead_letters(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        account_id: uuid.UUID | None,
        include_resolved: bool,
    ) -> list[DeadLetterView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            q = select(DeadLetter).order_by(DeadLetter.failed_at.desc())
            if not include_resolved:
                q = q.where(DeadLetter.resolved_at.is_(None))
            if account_id:
                q = q.where(DeadLetter.social_account_id == account_id)
            out = []
            for d in (await s.execute(q.limit(MAX_ROWS))).scalars():
                if d.social_account_id and not await self._can(
                    s, principal, Permission.READ, workspace_id, d.social_account_id
                ):
                    continue
                acct = (
                    await s.get(SocialAccount, d.social_account_id) if d.social_account_id else None
                )
                ps = await s.get(PostSet, d.post_set_id) if d.post_set_id else None
                out.append(
                    DeadLetterView(
                        d.id,
                        d.subject_id,
                        d.social_account_id,
                        acct.nickname if acct else None,
                        d.post_set_id,
                        ps.title if ps else None,
                        d.error,
                        d.attempts,
                        d.failed_at,
                        d.resolved_at,
                        d.resolution,
                    )
                )
            return out

    # ---------------------------------------------------------------------- writes

    async def reschedule(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        sp_id: uuid.UUID,
        meta: RequestMeta,
        *,
        scheduled_for: datetime,
        timezone: str | None = None,
    ) -> ScheduledView:
        """Move one account's slot (calendar drag, the bot's Reschedule). The new time is
        still subject to quiet hours, the daily cap and spacing; the response says if it
        moved."""
        now = datetime.now(UTC)
        if scheduled_for.tzinfo is None or scheduled_for < now + timedelta(minutes=1):
            raise InvalidInput(
                "Pick a time at least a minute from now (with a time zone).",
                code="invalid_scheduled_for",
                field="scheduled_for",
            )
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            sp = await self._slot(s, sp_id)
            await self._require(
                s, principal, Permission.PUBLISH, workspace_id, sp.social_account_id
            )
            self._ensure_writable(await self._ws(s, workspace_id))
            if sp.state not in (ScheduledState.QUEUED.value, ScheduledState.FAILED.value):
                raise ConflictError(
                    f"This post is {sp.state.replace('_', ' ')}.", code="not_reschedulable"
                )
            variant = await s.get(PostVariant, sp.post_variant_id)
            assert variant is not None
            before = sp.scheduled_for
            at, moved = await place_variant(
                s, variant, base=scheduled_for, now=now, exclude=sp.id, offset=0
            )
            sp.scheduled_for = at
            sp.next_attempt_at = at
            sp.placement = {
                "moved_by": moved,
                "requested": scheduled_for.isoformat(),
                "rescheduled": True,
                **({"requested_timezone": timezone} if timezone else {}),
            }
            variant.scheduled_for = at
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "scheduled_post.rescheduled",
                    workspace_id=workspace_id,
                    subject_type="scheduled_post",
                    subject_id=sp.id,
                    social_account_id=sp.social_account_id,
                    before={"scheduled_for": before.isoformat()},
                    after={"scheduled_for": at.isoformat(), "moved_by": moved},
                ),
            )
            await emit(
                s,
                workspace_id,
                "post.scheduled",
                subject_type="scheduled_post",
                subject_id=sp.id,
                social_account_id=sp.social_account_id,
                account_group_id=sp.account_group_id,
                payload={"post_set_id": str(sp.post_set_id), "scheduled_for": at.isoformat()},
            )
            view = await self._scheduled_view(s, principal, sp)
            assert view is not None
            return view

    async def cancel(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, sp_id: uuid.UUID, meta: RequestMeta
    ) -> ScheduledView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            sp = await self._slot(s, sp_id)
            await self._require(
                s, principal, Permission.PUBLISH, workspace_id, sp.social_account_id
            )
            if sp.state not in (ScheduledState.QUEUED.value, ScheduledState.FAILED.value):
                raise ConflictError(
                    f"This post is {sp.state.replace('_', ' ')}.", code="not_cancellable"
                )
            sp.state = ScheduledState.CANCELLED.value
            sp.next_attempt_at = None
            variant = await s.get(PostVariant, sp.post_variant_id)
            if variant is not None and variant.state == VariantState.SCHEDULED.value:
                variant.state = VariantState.APPROVED.value
            ps = await s.get(PostSet, sp.post_set_id)
            if ps is not None:
                await rollup_set(s, ps)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "scheduled_post.cancelled",
                    workspace_id=workspace_id,
                    subject_type="scheduled_post",
                    subject_id=sp.id,
                    social_account_id=sp.social_account_id,
                ),
            )
            view = await self._scheduled_view(s, principal, sp)
            assert view is not None
            return view

    async def publish_now(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, sp_id: uuid.UUID, meta: RequestMeta
    ) -> ScheduledView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            sp = await self._slot(s, sp_id)
            await self._require(
                s, principal, Permission.PUBLISH, workspace_id, sp.social_account_id
            )
            self._ensure_writable(await self._ws(s, workspace_id))
            if sp.state not in (ScheduledState.QUEUED.value, ScheduledState.FAILED.value):
                raise ConflictError(
                    f"This post is {sp.state.replace('_', ' ')}.", code="not_publishable_now"
                )
            now = datetime.now(UTC)
            sp.scheduled_for = now
            sp.next_attempt_at = now
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "scheduled_post.publish_now",
                    workspace_id=workspace_id,
                    subject_type="scheduled_post",
                    subject_id=sp.id,
                    social_account_id=sp.social_account_id,
                ),
            )
            view = await self._scheduled_view(s, principal, sp)
            assert view is not None
        await self._kick(workspace_id, sp.id, sp.social_account_id)
        return view

    async def retry(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, sp_id: uuid.UUID, meta: RequestMeta
    ) -> ScheduledView:
        """Retry from the dead-letter queue: the post is checked again from scratch,
        including ``assert_publishable``, so a retry can never bypass an approval."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            sp = await self._slot(s, sp_id)
            await self._require(
                s, principal, Permission.PUBLISH, workspace_id, sp.social_account_id
            )
            self._ensure_writable(await self._ws(s, workspace_id))
            if sp.state not in (ScheduledState.DEAD_LETTERED.value, ScheduledState.FAILED.value):
                raise ConflictError("Only failed posts can be retried.", code="not_retryable")
            now = datetime.now(UTC)
            sp.state = ScheduledState.QUEUED.value
            sp.attempt_count = 0
            sp.next_attempt_at = now
            sp.dead_lettered_at = None
            variant = await s.get(PostVariant, sp.post_variant_id)
            if variant is not None and variant.state == VariantState.FAILED.value:
                variant.state = VariantState.SCHEDULED.value
            for d in (
                await s.execute(
                    select(DeadLetter).where(
                        DeadLetter.subject_id == sp.id, DeadLetter.resolved_at.is_(None)
                    )
                )
            ).scalars():
                d.resolved_at = now
                d.resolved_by_user_id = user.user_id
                d.resolution = "retried"
            ps = await s.get(PostSet, sp.post_set_id)
            if ps is not None:
                await rollup_set(s, ps)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "scheduled_post.retried",
                    workspace_id=workspace_id,
                    subject_type="scheduled_post",
                    subject_id=sp.id,
                    social_account_id=sp.social_account_id,
                ),
            )
            view = await self._scheduled_view(s, principal, sp)
            assert view is not None
        await self._kick(workspace_id, sp.id, sp.social_account_id)
        return view

    async def resolve_dead_letter(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, dl_id: uuid.UUID, meta: RequestMeta
    ) -> DeadLetterView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            d = await s.get(DeadLetter, dl_id, with_for_update=True)
            if d is None:
                raise NotFoundError("Dead letter not found.")
            if d.social_account_id:
                await self._require(
                    s, principal, Permission.PUBLISH, workspace_id, d.social_account_id
                )
            else:
                rbac.require(principal, Permission.PUBLISH, workspace_id)
            if d.resolved_at is None:
                d.resolved_at = datetime.now(UTC)
                d.resolved_by_user_id = user.user_id
                d.resolution = "dismissed"
            acct = await s.get(SocialAccount, d.social_account_id) if d.social_account_id else None
            ps = await s.get(PostSet, d.post_set_id) if d.post_set_id else None
            return DeadLetterView(
                d.id,
                d.subject_id,
                d.social_account_id,
                acct.nickname if acct else None,
                d.post_set_id,
                ps.title if ps else None,
                d.error,
                d.attempts,
                d.failed_at,
                d.resolved_at,
                d.resolution,
            )

    async def schedule_set(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        set_id: uuid.UUID,
        meta: RequestMeta,
        *,
        scheduled_for: datetime | None,
        now_mode: bool = False,
        timezone: str | None = None,
    ) -> list[dict[str, Any]]:
        """Place every approved account that has no live slot (docs/04 §4.10: not a
        transaction across accounts; a per-account result says what landed). Used to
        re-place cancelled slots, or to publish a whole set now with its stagger kept."""
        now = datetime.now(UTC)
        base = now + timedelta(seconds=30) if now_mode or scheduled_for is None else scheduled_for
        if base.tzinfo is None or base < now:
            raise InvalidInput("Pick a future time with a time zone.", code="invalid_scheduled_for")
        results: list[dict[str, Any]] = []
        kicks: list[tuple[uuid.UUID, uuid.UUID]] = []
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            self._ensure_writable(await self._ws(s, workspace_id))
            ps = await s.get(PostSet, set_id, with_for_update=True)
            if ps is None:
                raise NotFoundError("Post set not found.")
            variants = list(
                (
                    await s.execute(
                        select(PostVariant)
                        .where(PostVariant.post_set_id == set_id, PostVariant.enabled.is_(True))
                        .order_by(PostVariant.position)
                    )
                ).scalars()
            )
            for v in variants:
                try:
                    await self._require(
                        s, principal, Permission.PUBLISH, workspace_id, v.social_account_id
                    )
                except Forbidden:
                    results.append({"post_variant_id": str(v.id), "outcome": "not_authorized"})
                    continue
                anchor = (
                    (
                        await s.execute(
                            select(ApprovalVariantState).where(
                                ApprovalVariantState.post_variant_id == v.id,
                                ApprovalVariantState.post_version_id == v.current_version_id,
                                ApprovalVariantState.state == VariantApproval.APPROVED.value,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if anchor is None or v.current_version_id is None:
                    results.append({"post_variant_id": str(v.id), "outcome": "approval_required"})
                    continue
                key = idempotency_key(v.social_account_id, v.current_version_id)
                sp = (
                    await s.execute(
                        select(ScheduledPost)
                        .where(ScheduledPost.idempotency_key == key)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if sp is not None and sp.state not in (ScheduledState.CANCELLED.value,):
                    if now_mode and sp.state in (
                        ScheduledState.QUEUED.value,
                        ScheduledState.FAILED.value,
                    ):
                        at, _ = await place_variant(s, v, base=base, now=now, exclude=sp.id)
                        sp.scheduled_for = sp.next_attempt_at = at
                        sp.placement = {
                            **(sp.placement or {}),
                            **({"requested_timezone": timezone} if timezone else {}),
                        }
                        kicks.append((sp.id, sp.social_account_id))
                        results.append(
                            {
                                "post_variant_id": str(v.id),
                                "outcome": "moved",
                                "scheduled_for": at.isoformat(),
                            }
                        )
                    else:
                        results.append({"post_variant_id": str(v.id), "outcome": sp.state})
                    continue
                at, moved = await place_variant(
                    s, v, base=base, now=now, exclude=sp.id if sp else None
                )
                if sp is None:
                    sp = ScheduledPost(
                        id=uuid7(),
                        workspace_id=workspace_id,
                        post_set_id=set_id,
                        post_variant_id=v.id,
                        post_version_id=v.current_version_id,
                        social_account_id=v.social_account_id,
                        account_group_id=v.via_account_group_id,
                        approval_request_id=anchor.approval_request_id,
                        scheduled_for=at,
                        stagger_offset_seconds=v.stagger_offset_seconds,
                        placement={
                            "moved_by": moved,
                            "requested": base.isoformat(),
                            **({"requested_timezone": timezone} if timezone else {}),
                        },
                        state=ScheduledState.QUEUED.value,
                        idempotency_key=key,
                        next_attempt_at=at,
                        created_by_user_id=user.user_id,
                    )
                    s.add(sp)
                else:
                    sp.state = ScheduledState.QUEUED.value
                    sp.scheduled_for = sp.next_attempt_at = at
                    sp.attempt_count = 0
                    sp.placement = {
                        "moved_by": moved,
                        "requested": base.isoformat(),
                        **({"requested_timezone": timezone} if timezone else {}),
                    }
                v.state = VariantState.SCHEDULED.value
                v.scheduled_for = at
                kicks.append((sp.id, sp.social_account_id))
                results.append(
                    {
                        "post_variant_id": str(v.id),
                        "outcome": "scheduled",
                        "scheduled_for": at.isoformat(),
                    }
                )
            await s.flush()
            await rollup_set(s, ps)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "post_set.publish_now" if now_mode else "post_set.scheduled",
                    workspace_id=workspace_id,
                    subject_type="post_set",
                    subject_id=set_id,
                    after={"results": results},
                ),
            )
        if now_mode:
            for sp_id, acct in kicks:
                await self._kick(workspace_id, sp_id, acct)
        return results

    # --------------------------------------------------------------------- helpers

    async def _kick(self, workspace_id: uuid.UUID, sp_id: uuid.UUID, account_id: uuid.UUID) -> None:
        if self._deferrer is None:
            return
        try:
            await self._deferrer.defer(
                PUBLISH_TASK,
                queue="publish",
                lock=f"account:{account_id}",
                queueing_lock=f"publish:{sp_id}",
                workspace_id=str(workspace_id),
                scheduled_post_id=str(sp_id),
            )
        except Exception as exc:  # the minute dispatcher picks it up anyway
            log.warning("publish.kick_failed", error_type=type(exc).__name__)

    async def _slot(self, s: AsyncSession, sp_id: uuid.UUID) -> ScheduledPost:
        sp = await s.get(ScheduledPost, sp_id, with_for_update=True)
        if sp is None:
            raise NotFoundError("Scheduled post not found.")
        return sp

    async def _ws(self, s: AsyncSession, workspace_id: uuid.UUID) -> Workspace:
        ws = await s.get(Workspace, workspace_id)
        if ws is None:
            raise NotFoundError("Workspace not found.")
        return ws

    async def _require(
        self,
        s: AsyncSession,
        principal: Principal,
        permission: Permission,
        workspace_id: uuid.UUID,
        account_id: uuid.UUID,
    ) -> None:
        groups = await approvals.account_groups(s, account_id)
        try:
            self._require_for_account(principal, permission, workspace_id, account_id, groups)
        except PermissionDenied as exc:
            if permission is Permission.READ:
                raise NotFoundError("Not found.") from exc
            raise Forbidden("You can't do that for this account.", code="forbidden") from exc

    async def _can(
        self,
        s: AsyncSession,
        principal: Principal,
        permission: Permission,
        workspace_id: uuid.UUID,
        account_id: uuid.UUID,
    ) -> bool:
        groups = await approvals.account_groups(s, account_id)
        try:
            self._require_for_account(principal, permission, workspace_id, account_id, groups)
        except PermissionDenied:
            return False
        return True

    async def _scheduled_view(
        self, s: AsyncSession, principal: Principal, sp: ScheduledPost
    ) -> ScheduledView | None:
        if not await self._can(
            s, principal, Permission.READ, sp.workspace_id, sp.social_account_id
        ):
            return None
        acct = await s.get(SocialAccount, sp.social_account_id)
        ps = await s.get(PostSet, sp.post_set_id)
        ver = await s.get(PostVersion, sp.post_version_id)
        grp = await s.get(AccountGroup, sp.account_group_id) if sp.account_group_id else None
        pp = None
        if sp.state == ScheduledState.PUBLISHED.value:
            pp = (
                await s.execute(
                    select(PublishedPost).where(PublishedPost.scheduled_post_id == sp.id)
                )
            ).scalar_one_or_none()
        return ScheduledView(
            id=sp.id,
            post_set_id=sp.post_set_id,
            post_set_title=ps.title if ps else "",
            post_variant_id=sp.post_variant_id,
            post_version_id=sp.post_version_id,
            social_account_id=sp.social_account_id,
            account_nickname=acct.nickname if acct else "",
            account_handle=acct.handle if acct else "",
            account_avatar_url=acct.avatar_url if acct else None,
            platform=acct.platform if acct else "",
            account_group_id=sp.account_group_id,
            group_name=grp.name if grp else None,
            group_color=getattr(grp, "color", None) if grp else None,
            scheduled_for=sp.scheduled_for,
            stagger_offset_seconds=sp.stagger_offset_seconds,
            state=sp.state,
            attempt_count=sp.attempt_count,
            max_attempts=MAX_PUBLISH_ATTEMPTS,
            next_attempt_at=sp.next_attempt_at,
            last_error=sp.last_error,
            moved_by=list((sp.placement or {}).get("moved_by") or []),
            excerpt=_excerpt(ver.body if ver else ""),
            permalink=pp.permalink if pp else None,
            published_at=sp.published_at,
        )


def _excerpt(text: str, n: int = 160) -> str:
    one = " ".join(text.split())
    return one if len(one) <= n else one[: n - 1] + "…"
