"""The publish worker (M8.5, 8.8, 8.9): one account's post at a time.

For each due ``scheduled_post``:

1. Under a row lock, ``assert_publishable`` runs against the variant **and** the slot must
   still name the approved version. Nothing else decides whether a post goes out.
   - Kill switch, or the account needing reconnection: the post is **held** (it stays
     queued and is looked at again later); it is never dead-lettered for a stop a person
     can lift.
   - Anything else (not approved, edited since, stale approval): dead-lettered with the
     reason. Retrying cannot fix those.
2. The adapter publishes with the slot's idempotency key. A retry first asks the platform
   whether the post already exists (lost response), so a retry never double-posts.
3. The outcome is recorded per account. Partial failure is the normal case: one account's
   failure never touches another's row (docs/03 §8).

Jobs are serialised per account by the queue's ``lock`` (``account:<id>``), so two posts for
one account never race, and different accounts never wait on each other.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.approvals.publishable import NotPublishable, assert_publishable
from mera.application.events import emit
from mera.application.publishing.scheduler import rollup_set
from mera.domain.context import WorkspaceContext
from mera.domain.ports.key_provider import KeyProvider
from mera.domain.ports.object_store import ObjectNotFound, ObjectStore
from mera.domain.ports.platform_adapter import (
    AuthExpired,
    MediaFile,
    PlatformAdapter,
    PublishError,
    PublishPayload,
    PublishResult,
    RateLimited,
)
from mera.domain.value_objects.ids import uuid7
from mera.domain.value_objects.platform import AccountStatus, Platform
from mera.domain.value_objects.posts import VariantState
from mera.domain.value_objects.publishing import (
    HOLD_RECHECK_SECONDS,
    MAX_PUBLISH_ATTEMPTS,
    ScheduledState,
    backoff_seconds,
)
from mera.infrastructure.db.models import (
    DeadLetter,
    MediaAsset,
    Notification,
    PostSet,
    PostVariant,
    PostVersion,
    PublishedComment,
    PublishedPost,
    SandboxCapture,
    ScheduledPost,
    SocialAccount,
    Workspace,
)
from mera.infrastructure.db.repositories.audit import AuditEntry, record
from mera.infrastructure.db.session import system_scan_transaction, workspace_transaction
from mera.infrastructure.platforms.capabilities import capabilities_for
from mera.infrastructure.queue.deferrer import Deferrer

log = structlog.get_logger(__name__)
PUBLISH_TASK = "publish:publish"
COMMENT_TASK = "publish:comment"
DISPATCH_TASK = "approvals:dispatch"
CREDENTIALS_PURPOSE = "social_account.credentials"
MEDIA_URL_TTL = timedelta(hours=1)
REAUTH_RECHECK = timedelta(minutes=15)
# Same-platform accounts of one set never go out closer than this (the default stagger
# minimum), even when held posts resume together.
SAME_PLATFORM_GAP = timedelta(minutes=3)


@dataclass(frozen=True, slots=True)
class Due:
    workspace_id: uuid.UUID
    scheduled_post_id: uuid.UUID
    social_account_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class CommentDue:
    workspace_id: uuid.UUID
    comment_id: uuid.UUID
    social_account_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class _Prepared:
    sp_id: uuid.UUID
    workspace_id: uuid.UUID
    account_id: uuid.UUID
    platform: str
    credentials: dict[str, Any]
    payload: PublishPayload
    attempt: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _PreparationTerminal:
    """A permanent failure discovered before a platform request can be made."""

    outcome: str
    finished_post_set_id: uuid.UUID | None = None


class Publisher:
    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        adapters: dict[str, PlatformAdapter],
        key_provider: KeyProvider,
        store: ObjectStore,
        *,
        deferrer: Deferrer | None = None,
        sandbox: bool = False,
    ) -> None:
        self._sm = sm
        self._adapters = adapters
        self._kp = key_provider
        self._store = store
        self._deferrer = deferrer
        self._sandbox = sandbox

    # --------------------------------------------------------------------- finding

    async def due(
        self, now: datetime | None = None, *, horizon: timedelta = timedelta(seconds=15)
    ) -> list[Due]:
        now = now or datetime.now(UTC)
        async with system_scan_transaction(self._sm) as s:
            ws_ids = list(
                (
                    await s.execute(
                        select(Workspace.id).where(
                            Workspace.deleted_at.is_(None), Workspace.archived_at.is_(None)
                        )
                    )
                ).scalars()
            )
        out: list[Due] = []
        for ws in ws_ids:
            async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
                rows = (
                    await s.execute(
                        select(ScheduledPost.id, ScheduledPost.social_account_id)
                        .where(
                            ScheduledPost.state.in_(
                                [ScheduledState.QUEUED.value, ScheduledState.FAILED.value]
                            ),
                            ScheduledPost.next_attempt_at <= now + horizon,
                        )
                        .order_by(ScheduledPost.next_attempt_at)
                        .limit(200)
                    )
                ).all()
                out.extend(Due(ws, sp_id, acct) for sp_id, acct in rows)
        return out

    async def enqueue_due(self, now: datetime | None = None) -> int:
        """The minute dispatcher: one job per due post, serialised per account."""
        if self._deferrer is None:
            return 0
        n = 0
        for d in await self.due(now):
            if await self._deferrer.defer(
                PUBLISH_TASK,
                queue="publish",
                lock=f"account:{d.social_account_id}",
                queueing_lock=f"publish:{d.scheduled_post_id}",
                workspace_id=str(d.workspace_id),
                scheduled_post_id=str(d.scheduled_post_id),
            ):
                n += 1
        for c in await self.comments_due(now):
            if await self._deferrer.defer(
                COMMENT_TASK,
                queue="publish",
                lock=f"account:{c.social_account_id}",
                queueing_lock=f"comment:{c.comment_id}",
                workspace_id=str(c.workspace_id),
                published_comment_id=str(c.comment_id),
            ):
                n += 1
        return n

    async def comments_due(
        self, now: datetime | None = None, *, horizon: timedelta = timedelta(seconds=15)
    ) -> list[CommentDue]:
        now = now or datetime.now(UTC)
        async with system_scan_transaction(self._sm) as s:
            ws_ids = list(
                (
                    await s.execute(
                        select(Workspace.id).where(
                            Workspace.deleted_at.is_(None), Workspace.archived_at.is_(None)
                        )
                    )
                ).scalars()
            )
        out: list[CommentDue] = []
        for ws in ws_ids:
            async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
                rows = (
                    await s.execute(
                        select(PublishedComment.id, PublishedComment.social_account_id)
                        .where(
                            PublishedComment.state.in_(["queued", "failed"]),
                            PublishedComment.next_attempt_at <= now + horizon,
                        )
                        .order_by(PublishedComment.next_attempt_at)
                        .limit(200)
                    )
                ).all()
                out.extend(
                    CommentDue(ws, comment_id, account_id) for comment_id, account_id in rows
                )
        return out

    # -------------------------------------------------------------------- publishing

    async def publish(
        self, workspace_id: uuid.UUID, scheduled_post_id: uuid.UUID, *, now: datetime | None = None
    ) -> str:
        now = now or datetime.now(UTC)
        prepared = await self._prepare(workspace_id, scheduled_post_id, now)
        if isinstance(prepared, _PreparationTerminal):
            if prepared.finished_post_set_id is not None:
                await self._report(workspace_id, prepared.finished_post_set_id)
            return prepared.outcome
        if isinstance(prepared, str):
            return prepared
        adapter = self._adapters.get(prepared.platform)
        if adapter is None:
            return await self._fail(
                prepared, PublishError(f"no adapter for {prepared.platform}"), now, permanent=True
            )
        result: PublishResult | None = None
        try:
            if prepared.attempt > 1:
                # A previous attempt may have succeeded with its response lost: reconcile
                # before posting again (M8 acceptance: no double post).
                result = await adapter.find_existing(
                    prepared.credentials,
                    prepared.payload,
                    prepared.created_at - timedelta(minutes=5),
                )
            if result is None:
                result = await adapter.publish(prepared.credentials, prepared.payload)
        except AuthExpired as exc:
            await self._capture(prepared, {"outcome": "auth_expired", "error": exc.message})
            return await self._reauth(prepared, exc, now)
        except PublishError as exc:
            await self._capture(
                prepared, {"outcome": "error", "error": exc.message, "status": exc.status}
            )
            return await self._fail(prepared, exc, now, permanent=not exc.retryable)
        await self._capture(
            prepared,
            {
                "outcome": "published",
                "platform_post_id": result.platform_post_id,
                "permalink": result.permalink,
            },
        )
        return await self._succeed(prepared, result, datetime.now(UTC))

    async def _prepare(
        self, workspace_id: uuid.UUID, sp_id: uuid.UUID, now: datetime
    ) -> _Prepared | _PreparationTerminal | str:
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            sp = await s.get(ScheduledPost, sp_id, with_for_update=True)
            if sp is None or sp.state not in (
                ScheduledState.QUEUED.value,
                ScheduledState.FAILED.value,
            ):
                return "skipped"
            if sp.next_attempt_at is not None and sp.next_attempt_at > now + timedelta(seconds=15):
                return "not_due"
            try:
                anchor = await assert_publishable(
                    s, sp.post_variant_id, now=now, approved_slot=_approved_slot(sp)
                )
            except NotPublishable as exc:
                if exc.code in ("kill_switch_engaged", "account_unavailable"):
                    recheck = (
                        HOLD_RECHECK_SECONDS
                        if exc.code == "kill_switch_engaged"
                        else int(REAUTH_RECHECK.total_seconds())
                    )
                    sp.next_attempt_at = now + timedelta(seconds=recheck)
                    sp.last_error = f"On hold: {exc.message}"
                    return "held"
                await self._dead_letter(s, sp, f"{exc.message} ({exc.code})", now)
                return "dead_lettered"
            if anchor.post_version_id != sp.post_version_id:
                await self._dead_letter(
                    s, sp, "The approved version is not the scheduled one.", now
                )
                return "dead_lettered"
            spaced = await self._same_platform_gap(s, sp, now)
            if spaced is not None:
                sp.next_attempt_at = spaced
                sp.last_error = "Waiting to stay minutes apart from another account here."
                return "spaced"
            budget = await _budget_wait(s, sp, now)
            if budget is not None:
                frees_at, limit = budget
                sp.next_attempt_at = frees_at
                sp.last_error = (
                    f"Holding: this account can publish {limit} posts in 24 hours here; "
                    "it goes out when the window frees."
                )
                return "budget"
            account = await s.get(SocialAccount, sp.social_account_id)
            version = await s.get(PostVersion, sp.post_version_id)
            variant = await s.get(PostVariant, sp.post_variant_id)
            assert account is not None
            assert version is not None
            assert variant is not None
            raw = self._kp.decrypt(
                str(account.credentials_ct),
                workspace_id=workspace_id,
                purpose=CREDENTIALS_PURPOSE,
                record_id=account.id,
            )
            credentials: dict[str, Any] = json.loads(raw)
            try:
                media = await self._media(s, version)
            except ObjectNotFound as exc:
                # The object-store key is safe to expose to operators and makes recovery
                # possible, while deliberately avoiding the host filesystem path. This is
                # permanent until an operator restores the object, so leaving the slot
                # queued would make the minute dispatcher create jobs forever.
                key = str(exc.args[0]) if exc.args else "unknown"
                message = f"MEDIA_NOT_FOUND: attached media object is missing ({key})."
                log.error(
                    "publish.media_not_found",
                    workspace_id=str(workspace_id),
                    scheduled_post_id=str(sp.id),
                    object_key=key,
                )
                await self._dead_letter(s, sp, message, now)
                finished = await self._after_terminal(s, sp)
                return _PreparationTerminal("dead_lettered", finished)
            caps = capabilities_for(Platform(account.platform))
            payload = PublishPayload(
                platform=account.platform,
                platform_account_id=account.platform_account_id,
                account_type=account.account_type,
                body=version.body,
                thread_parts=list(version.thread_parts) if version.thread_parts else None,
                hashtags=list(version.hashtags),
                link_url=version.link_url,
                link_placement=caps.link_placement,
                media=media,
                idempotency_key=sp.idempotency_key,
                first_comment=version.first_comment,
            )
            sp.state = ScheduledState.PUBLISHING.value
            sp.attempt_count += 1
            sp.last_error = None
            variant.state = VariantState.PUBLISHING.value
            return _Prepared(
                sp.id,
                workspace_id,
                account.id,
                account.platform,
                credentials,
                payload,
                sp.attempt_count,
                sp.created_at,
            )

    async def _same_platform_gap(
        self, s: AsyncSession, sp: ScheduledPost, now: datetime
    ) -> datetime | None:
        """Stagger holds at publish time too (M8.7, docs/08 §3.3). Slots are spaced when
        placed, but posts held by a kill switch or a reconnect resume together; a resumed
        post waits until it is ``SAME_PLATFORM_GAP`` after any sibling of the same set on the
        same platform. The set row lock serialises siblings that resume at the same tick."""
        account = await s.get(SocialAccount, sp.social_account_id)
        if (
            account is None
            or not capabilities_for(Platform(account.platform)).requires_stagger_within_platform
        ):
            return None
        await s.get(PostSet, sp.post_set_id, with_for_update=True)
        siblings = select(SocialAccount.id).where(SocialAccount.platform == account.platform)
        last_published = await s.scalar(
            select(func.max(PublishedPost.published_at)).where(
                PublishedPost.post_set_id == sp.post_set_id,
                PublishedPost.social_account_id != sp.social_account_id,
                PublishedPost.social_account_id.in_(siblings),
            )
        )
        in_flight = await s.scalar(
            select(ScheduledPost.id).where(
                ScheduledPost.post_set_id == sp.post_set_id,
                ScheduledPost.social_account_id != sp.social_account_id,
                ScheduledPost.social_account_id.in_(siblings),
                ScheduledPost.state == ScheduledState.PUBLISHING.value,
            )
        )
        latest = now if in_flight is not None else last_published
        if latest is None or now - latest >= SAME_PLATFORM_GAP:
            return None
        return latest + SAME_PLATFORM_GAP

    async def _media(self, s: AsyncSession, version: PostVersion) -> list[MediaFile]:
        out = []
        for entry in version.media:
            asset = await s.get(MediaAsset, uuid.UUID(str(entry["asset_id"])))
            if asset is None:
                raise PublishError("an attached image is missing")
            data = await self._store.get(asset.object_key)
            url = await self._store.presigned_url(asset.object_key, MEDIA_URL_TTL)
            out.append(
                MediaFile(
                    data=data,
                    mime_type=asset.content_type,
                    alt_text=entry.get("alt_text"),
                    width=asset.width,
                    height=asset.height,
                    public_url=url,
                )
            )
        return out

    async def _succeed(self, p: _Prepared, result: PublishResult, now: datetime) -> str:
        async with workspace_transaction(self._sm, WorkspaceContext(p.workspace_id)) as s:
            sp = await s.get(ScheduledPost, p.sp_id, with_for_update=True)
            assert sp is not None
            version = await s.get(PostVersion, sp.post_version_id)
            variant = await s.get(PostVariant, sp.post_variant_id)
            assert version is not None
            assert variant is not None
            pp = PublishedPost(
                id=uuid7(),
                workspace_id=p.workspace_id,
                post_set_id=sp.post_set_id,
                scheduled_post_id=sp.id,
                post_variant_id=sp.post_variant_id,
                post_version_id=sp.post_version_id,
                social_account_id=sp.social_account_id,
                account_group_id=sp.account_group_id,
                platform=p.platform,
                platform_post_id=result.platform_post_id,
                permalink=result.permalink,
                extra_ids=dict(result.extra_ids),
                content_hash=version.content_hash,
                via_autopilot=await _via_autopilot(s, sp.post_variant_id, version.content_hash),
                published_at=now,
                next_metrics_at=now + timedelta(hours=1),
                next_comments_at=now + timedelta(minutes=5),  # P2-M6.2
            )
            s.add(pp)
            comment_body = _first_comment(
                version, capabilities_for(Platform(p.platform)).link_placement
            )
            comment: PublishedComment | None = None
            if comment_body:
                digest = hashlib.sha256(comment_body.encode("utf-8")).digest()
                comment = PublishedComment(
                    id=uuid7(),
                    workspace_id=p.workspace_id,
                    published_post_id=pp.id,
                    social_account_id=sp.social_account_id,
                    body=comment_body,
                    content_hash=digest,
                    state="queued",
                    idempotency_key=f"comment:{pp.id}:{digest.hex()}",
                    next_attempt_at=now,
                )
                s.add(comment)
            sp.state = ScheduledState.PUBLISHED.value
            sp.published_at = now
            sp.next_attempt_at = None
            variant.state = VariantState.PUBLISHED.value
            await s.flush()
            await record(
                s,
                AuditEntry(
                    action="post.published",
                    actor_type="system",
                    surface="worker",
                    workspace_id=p.workspace_id,
                    subject_type="published_post",
                    subject_id=pp.id,
                    social_account_id=sp.social_account_id,
                    account_group_id=sp.account_group_id,
                    subject_content_hash=version.content_hash,
                    after={
                        "platform_post_id": result.platform_post_id,
                        "attempt": sp.attempt_count,
                        "approval_request_id": str(sp.approval_request_id),
                    },
                ),
            )
            await emit(
                s,
                p.workspace_id,
                "post.published",
                subject_type="published_post",
                subject_id=pp.id,
                social_account_id=sp.social_account_id,
                account_group_id=sp.account_group_id,
                payload={"post_set_id": str(sp.post_set_id), "permalink": result.permalink},
            )
            finished = await self._after_terminal(s, sp)
        if finished:
            await self._report(p.workspace_id, finished)
        if comment is not None and self._deferrer is not None:
            await self._deferrer.defer(
                COMMENT_TASK,
                queue="publish",
                lock=f"account:{p.account_id}",
                queueing_lock=f"comment:{comment.id}",
                workspace_id=str(p.workspace_id),
                published_comment_id=str(comment.id),
            )
        return "published"

    async def publish_comment(
        self, workspace_id: uuid.UUID, comment_id: uuid.UUID, *, now: datetime | None = None
    ) -> str:
        """Deliver only the comment; retries here can never invoke main-post publishing."""
        now = now or datetime.now(UTC)
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            row = await s.get(PublishedComment, comment_id, with_for_update=True)
            if row is None or row.state not in ("queued", "failed"):
                return "skipped"
            if row.next_attempt_at and row.next_attempt_at > now + timedelta(seconds=15):
                return "not_due"
            pp = await s.get(PublishedPost, row.published_post_id)
            account = await s.get(SocialAccount, row.social_account_id)
            if pp is None or account is None:
                row.state, row.last_error = (
                    "dead_lettered",
                    "Published post or account is missing.",
                )
                return "dead_lettered"
            raw = self._kp.decrypt(
                str(account.credentials_ct),
                workspace_id=workspace_id,
                purpose=CREDENTIALS_PURPOSE,
                record_id=account.id,
            )
            credentials: dict[str, Any] = json.loads(raw)
            adapter = self._adapters.get(pp.platform)
            method = getattr(adapter, "publish_comment", None) if adapter else None
            if method is None:
                row.state, row.last_error = (
                    "dead_lettered",
                    "Platform does not support first comments.",
                )
                return "dead_lettered"
            row.state = "publishing"
            row.attempt_count += 1
            attempt = row.attempt_count
            account_id = account.platform_account_id
            post_id, body, key = pp.platform_post_id, row.body, row.idempotency_key
        try:
            finder = getattr(adapter, "find_comment", None)
            platform_id = (
                await finder(
                    credentials,
                    platform_account_id=account_id,
                    platform_post_id=post_id,
                    body=body,
                )
                if attempt > 1 and finder is not None
                else None
            )
            if platform_id is None:
                platform_id = await method(
                    credentials,
                    platform_account_id=account_id,
                    platform_post_id=post_id,
                    body=body,
                    idempotency_key=key,
                )
        except AuthExpired as exc:
            async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
                row = await s.get(PublishedComment, comment_id, with_for_update=True)
                assert row is not None
                account = await s.get(SocialAccount, row.social_account_id, with_for_update=True)
                row.state = "failed"
                row.last_error = "On hold: the account needs reconnecting."
                row.next_attempt_at = now + REAUTH_RECHECK
                row.attempt_count = max(0, row.attempt_count - 1)
                if account is not None:
                    account.status = AccountStatus.NEEDS_REAUTH.value
                    account.status_reason = exc.message[:200]
                    account.health = {
                        **account.health,
                        "token_valid": False,
                        "last_error": exc.message[:200],
                    }
            return "needs_reauth"
        except PublishError as exc:
            error = exc
        except ValueError as exc:
            error = PublishError(str(exc))
        else:
            async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
                row = await s.get(PublishedComment, comment_id, with_for_update=True)
                assert row is not None
                row.state = "published"
                row.platform_comment_id = platform_id
                row.published_at = datetime.now(UTC)
                row.next_attempt_at = None
                row.last_error = None
                await record(
                    s,
                    AuditEntry(
                        action="first_comment.published",
                        actor_type="system",
                        surface="worker",
                        workspace_id=workspace_id,
                        subject_type="published_comment",
                        subject_id=row.id,
                        social_account_id=row.social_account_id,
                        after={
                            "platform_comment_id": platform_id,
                            "attempt": row.attempt_count,
                        },
                    ),
                )
            return "published"
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            row = await s.get(PublishedComment, comment_id, with_for_update=True)
            assert row is not None
            row.last_error = error.message[:300]
            if not error.retryable or row.attempt_count >= MAX_PUBLISH_ATTEMPTS:
                row.state, row.next_attempt_at = "dead_lettered", None
                return "dead_lettered"
            retry_after = error.retry_after if isinstance(error, RateLimited) else None
            row.state = "failed"
            row.next_attempt_at = now + timedelta(
                seconds=backoff_seconds(row.attempt_count, retry_after)
            )
            return "retrying"

    async def _fail(
        self, p: _Prepared, exc: PublishError, now: datetime, *, permanent: bool
    ) -> str:
        async with workspace_transaction(self._sm, WorkspaceContext(p.workspace_id)) as s:
            sp = await s.get(ScheduledPost, p.sp_id, with_for_update=True)
            assert sp is not None
            message = exc.message[:300]
            if permanent or sp.attempt_count >= MAX_PUBLISH_ATTEMPTS:
                await self._dead_letter(s, sp, message, now)
                outcome = "dead_lettered"
            else:
                retry_after = exc.retry_after if isinstance(exc, RateLimited) else None
                sp.state = ScheduledState.FAILED.value
                sp.last_error = message
                sp.next_attempt_at = now + timedelta(
                    seconds=backoff_seconds(sp.attempt_count, retry_after)
                )
                variant = await s.get(PostVariant, sp.post_variant_id)
                if variant is not None:
                    variant.state = VariantState.SCHEDULED.value
                await emit(
                    s,
                    p.workspace_id,
                    "post.failed",
                    subject_type="scheduled_post",
                    subject_id=sp.id,
                    social_account_id=sp.social_account_id,
                    account_group_id=sp.account_group_id,
                    payload={
                        "post_set_id": str(sp.post_set_id),
                        "error": message,
                        "attempt": sp.attempt_count,
                        "max_attempts": MAX_PUBLISH_ATTEMPTS,
                        "next_attempt_at": sp.next_attempt_at.isoformat(),
                    },
                )
                outcome = "retrying"
            finished = await self._after_terminal(s, sp) if outcome == "dead_lettered" else None
        if finished:
            await self._report(p.workspace_id, finished)
        return outcome

    async def _reauth(self, p: _Prepared, exc: AuthExpired, now: datetime) -> str:
        """Only this account stops (M8 acceptance): it moves to NEEDS_REAUTH, its slot is
        held, a notification is raised, and its siblings keep publishing."""
        async with workspace_transaction(self._sm, WorkspaceContext(p.workspace_id)) as s:
            sp = await s.get(ScheduledPost, p.sp_id, with_for_update=True)
            account = await s.get(SocialAccount, p.account_id, with_for_update=True)
            assert sp is not None
            assert account is not None
            sp.state = ScheduledState.QUEUED.value
            sp.last_error = "On hold: the account needs reconnecting."
            sp.next_attempt_at = now + REAUTH_RECHECK
            sp.attempt_count = max(0, sp.attempt_count - 1)  # not the post's fault
            variant = await s.get(PostVariant, sp.post_variant_id)
            if variant is not None:
                variant.state = VariantState.SCHEDULED.value
            if account.status == AccountStatus.ACTIVE.value:
                reason = " ".join(exc.message.split())[:200]
                account.status = AccountStatus.NEEDS_REAUTH.value
                account.status_reason = reason
                account.health = {**account.health, "token_valid": False, "last_error": reason}
                s.add(
                    Notification(
                        id=uuid7(),
                        workspace_id=p.workspace_id,
                        kind="account.needs_reauth",
                        severity="critical",
                        title=f"{account.nickname} needs reconnecting",
                        body="Its scheduled posts are on hold; other accounts are unaffected.",
                        subject_type="social_account",
                        subject_id=account.id,
                        social_account_id=account.id,
                    )
                )
                await record(
                    s,
                    AuditEntry(
                        action="account.needs_reauth",
                        actor_type="system",
                        surface="worker",
                        workspace_id=p.workspace_id,
                        subject_type="social_account",
                        subject_id=account.id,
                        social_account_id=account.id,
                        after={"reason": reason, "during": "publish"},
                    ),
                )
                await emit(
                    s,
                    p.workspace_id,
                    "account.needs_reauth",
                    subject_type="social_account",
                    subject_id=account.id,
                    social_account_id=account.id,
                )
        await self._notify_channels("health", p.workspace_id, p.account_id)
        return "needs_reauth"

    async def _dead_letter(
        self, s: AsyncSession, sp: ScheduledPost, message: str, now: datetime
    ) -> None:
        sp.state = ScheduledState.DEAD_LETTERED.value
        sp.last_error = message
        sp.dead_lettered_at = now
        sp.next_attempt_at = None
        variant = await s.get(PostVariant, sp.post_variant_id)
        if variant is not None:
            variant.state = VariantState.FAILED.value
        account = await s.get(SocialAccount, sp.social_account_id)
        s.add(
            DeadLetter(
                id=uuid7(),
                workspace_id=sp.workspace_id,
                queue="publish",
                task_name=PUBLISH_TASK,
                subject_type="scheduled_post",
                subject_id=sp.id,
                social_account_id=sp.social_account_id,
                post_set_id=sp.post_set_id,
                error=message,
                attempts=sp.attempt_count,
                failed_at=now,
            )
        )
        s.add(
            Notification(
                id=uuid7(),
                workspace_id=sp.workspace_id,
                kind="post.dead_lettered",
                severity="warning",
                title=f"A post to {account.nickname if account else 'an account'} failed",
                body=message,
                subject_type="scheduled_post",
                subject_id=sp.id,
                social_account_id=sp.social_account_id,
            )
        )
        await record(
            s,
            AuditEntry(
                action="post.dead_lettered",
                actor_type="system",
                surface="worker",
                workspace_id=sp.workspace_id,
                subject_type="scheduled_post",
                subject_id=sp.id,
                social_account_id=sp.social_account_id,
                account_group_id=sp.account_group_id,
                after={"error": message, "attempts": sp.attempt_count},
            ),
        )
        await emit(
            s,
            sp.workspace_id,
            "post.dead_lettered",
            subject_type="scheduled_post",
            subject_id=sp.id,
            social_account_id=sp.social_account_id,
            account_group_id=sp.account_group_id,
            payload={"post_set_id": str(sp.post_set_id), "error": message},
        )

    async def _after_terminal(self, s: AsyncSession, sp: ScheduledPost) -> uuid.UUID | None:
        """Roll the set up; if every scheduled account is now final, return the set id so the
        bots can send the per-account publish report (docs/07 §3.4)."""
        ps = await s.get(PostSet, sp.post_set_id, with_for_update=True)
        if ps is None:
            return None
        await s.flush()
        before = ps.state
        state = await rollup_set(s, ps)
        open_rows = await s.scalar(
            select(ScheduledPost.id)
            .where(
                ScheduledPost.post_set_id == ps.id,
                ScheduledPost.state.in_(
                    [
                        ScheduledState.QUEUED.value,
                        ScheduledState.FAILED.value,
                        ScheduledState.PUBLISHING.value,
                    ]
                ),
            )
            .limit(1)
        )
        if open_rows is not None:
            return None
        if state != before or state in ("published", "partially_published"):
            await emit(
                s,
                ps.workspace_id,
                f"post_set.{state}",
                subject_type="post_set",
                subject_id=ps.id,
                payload={"title": ps.title},
            )
        return ps.id

    async def _report(self, workspace_id: uuid.UUID, post_set_id: uuid.UUID) -> None:
        await self._notify_channels("publish_report", workspace_id, post_set_id)

    async def _notify_channels(
        self, kind: str, workspace_id: uuid.UUID, subject_id: uuid.UUID
    ) -> None:
        if self._deferrer is None:
            return
        try:
            await self._deferrer.defer(
                DISPATCH_TASK,
                queue="approvals",
                queueing_lock=f"dispatch:{kind}:{subject_id}",
                kind=kind,
                workspace_id=str(workspace_id),
                subject_id=str(subject_id),
            )
        except Exception as exc:  # pragma: no cover - logged only
            log.warning("publish.notify_failed", kind=kind, error_type=type(exc).__name__)

    async def _capture(self, p: _Prepared, outcome: dict[str, Any]) -> None:
        """Sandbox only: every platform call is recorded for the /sandbox inspector."""
        if not self._sandbox:
            return
        async with workspace_transaction(self._sm, WorkspaceContext(p.workspace_id)) as s:
            s.add(
                SandboxCapture(
                    workspace_id=p.workspace_id,
                    kind="platform_call",
                    target=p.platform,
                    social_account_id=p.account_id,
                    payload={
                        "op": "publish",
                        "attempt": p.attempt,
                        "text": p.payload.body[:2000],
                        "thread_parts": p.payload.thread_parts,
                        "hashtags": p.payload.hashtags,
                        "link_url": p.payload.link_url,
                        "link_placement": p.payload.link_placement,
                        "media": [
                            {"mime": m.mime_type, "alt_text": m.alt_text, "bytes": len(m.data)}
                            for m in p.payload.media
                        ],
                        "idempotency_key": p.payload.idempotency_key[:16],
                        **outcome,
                    },
                )
            )


def _approved_slot(sp: ScheduledPost) -> datetime | None:
    """The time the approval placed this slot for (ADR-0016); None on rows from before it."""
    raw = (sp.placement or {}).get("approved_slot")
    try:
        return datetime.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        return None


def _first_comment(version: PostVersion, link_placement: str) -> str | None:
    parts = [version.first_comment.strip()] if version.first_comment else []
    # Reply-placement adapters publish their link as part of their own thread flow. Only
    # first-comment placement is a separately retried comment job.
    if version.link_url and link_placement == "first_comment":
        parts.append(version.link_url)
    body = "\n\n".join(p for p in parts if p).strip()
    return body or None


async def _budget_wait(
    s: AsyncSession, sp: ScheduledPost, now: datetime
) -> tuple[datetime, int] | None:
    """The per-account rate budget (P2-M2.9): when the account already published its
    platform's allowance in the last 24 hours, the time the oldest of those leaves the
    window, and the limit. Held, never failed: the platform would refuse it anyway."""
    account = await s.get(SocialAccount, sp.social_account_id)
    if account is None:
        return None
    own = (account.rate_budget or {}).get("posts_per_24h")
    limit = (
        int(own)
        if isinstance(own, int) and own > 0
        else capabilities_for(Platform(account.platform)).posts_per_24h
    )
    if not limit:
        return None
    since = now - timedelta(hours=24)
    times = sorted(
        (
            await s.execute(
                select(PublishedPost.published_at).where(
                    PublishedPost.social_account_id == account.id,
                    PublishedPost.published_at > since,
                    PublishedPost.deleted_at.is_(None),
                )
            )
        ).scalars()
    )
    if len(times) < limit:
        return None
    return times[len(times) - limit] + timedelta(hours=24, minutes=1), limit


async def _via_autopilot(s: AsyncSession, variant_id: uuid.UUID, content_hash: bytes) -> bool:
    """Whether this exact content went out on an autopilot authorization (P3-M3)."""
    from mera.infrastructure.db.models import AutopilotAuthorization

    found = await s.scalar(
        select(AutopilotAuthorization.id)
        .where(
            AutopilotAuthorization.post_variant_id == variant_id,
            AutopilotAuthorization.content_hash == content_hash,
            AutopilotAuthorization.revoked_at.is_(None),
        )
        .limit(1)
    )
    return found is not None
