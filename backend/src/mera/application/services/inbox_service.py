"""The unified inbox (P2-M6): comment polling, triage, replies, hide and delete.

Three rules carry the weight:

1. **A reply goes out as the account that was commented on, and nothing else.** The
   reply's account is copied from the comment (which copied it from the post) by the
   server; no request, rule, agent output or comment text can set it. The approval hash
   covers it, and ``assert_reply_sendable`` re-checks all three ids at send time.
2. **Comments are untrusted input.** They reach the Engagement agent inside an
   ``<untrusted_comment>`` block; the agent has no tools and its draft is cleaned of
   links, hashtags and foreign @mentions. Every reply waits for a person's approval.
3. **Polling tapers.** A fresh post is polled every five minutes, a day-old one every
   half hour, a week-old one every three hours, then daily; at thirty days it stops.
"""

from __future__ import annotations

import hmac
import json
import secrets
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application import second_factor
from mera.application.agents.base import AgentContext, CostCapReached
from mera.application.agents.engagement import EngagementBrief, EngagementTriage, clean_reply
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.drafting import core
from mera.application.drafting.pipeline import Drafting
from mera.application.engagement.sendable import NotSendable, assert_reply_sendable, reply_hash
from mera.application.errors import ConflictError, Forbidden, InvalidInput, NotFoundError
from mera.application.ingest import agent_runs
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import acting_user, load_user_principal
from mera.domain.context import WorkspaceContext
from mera.domain.errors import PermissionDenied
from mera.domain.policies.rbac import Permission
from mera.domain.ports.engagement import CommentRef, EngagementAdapter, PostRef
from mera.domain.ports.key_provider import KeyProvider
from mera.domain.ports.llm import LLMError
from mera.domain.ports.platform_adapter import AuthExpired, PublishError, RateLimited
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.db.models import (
    AccountGroup,
    AccountGroupMember,
    Comment,
    EngagementRule,
    PostVariant,
    PostVersion,
    PublishedPost,
    Reply,
    SocialAccount,
    Workspace,
)
from mera.infrastructure.db.repositories.audit import AuditEntry, record
from mera.infrastructure.db.session import system_scan_transaction, workspace_transaction
from mera.infrastructure.queue.deferrer import Deferrer

log = structlog.get_logger(__name__)

SEND_TASK: Final = "engagement:send_reply"
DISPATCH_TASK: Final = "approvals:dispatch"
CREDENTIALS_PURPOSE: Final = "social_account.credentials"
POSTS_PER_RUN: Final = 200
STATES: Final = ("new", "triaged", "replied", "hidden", "ignored", "escalated", "deleted")
TRIAGE_TO: Final = frozenset({"new", "triaged", "ignored", "escalated"})
RULE_ACTIONS: Final = ("draft_reply", "escalate", "ignore", "hide")
RULE_SCOPES: Final = ("workspace", "social_account", "account_group")


def next_poll(published_at: datetime, now: datetime) -> datetime | None:
    """The comment poll's taper. None: the post is no longer polled."""
    age = now - published_at
    if age < timedelta(hours=6):
        return now + timedelta(minutes=5)
    if age < timedelta(days=2):
        return now + timedelta(minutes=30)
    if age < timedelta(days=7):
        return now + timedelta(hours=3)
    if age < timedelta(days=30):
        return now + timedelta(days=1)
    return None


def rule_matches(rule: EngagementRule, text: str, triage: EngagementTriage | None) -> bool:
    """``match``: ``keywords`` (any, case-insensitive), ``intent`` and ``sentiment`` (any
    of a list). Every condition given must hold. A rule with intent or sentiment
    conditions only matches after the agent has classified the comment."""
    m = rule.match or {}
    words = [str(w).lower() for w in m.get("keywords") or [] if str(w).strip()]
    if words and not any(w in text.lower() for w in words):
        return False
    for key in ("intent", "sentiment"):
        wanted = m.get(key) or []
        if wanted and (triage is None or getattr(triage, key) not in wanted):
            return False
    return bool(words or m.get("intent") or m.get("sentiment"))


@dataclass(frozen=True, slots=True)
class ReplyView:
    id: uuid.UUID
    comment_id: uuid.UUID
    social_account_id: uuid.UUID
    body: str
    content_hash: str
    state: str
    source: str
    confidence: float | None
    error: str | None
    platform_reply_id: str | None
    created_at: datetime
    approved_at: datetime | None
    sent_at: datetime | None


def _card_matches(r: Reply, nonce: str) -> bool:
    """The card is the last one sent (its nonce) and the reply is still exactly what that
    card showed (the full content hash, not a prefix that could be ground against)."""
    return (
        r.card_nonce is not None
        and r.card_hash is not None
        and hmac.compare_digest(r.card_nonce.encode(), nonce.encode())
        and hmac.compare_digest(r.card_hash, r.content_hash)
    )


def _reply_view(r: Reply) -> ReplyView:
    return ReplyView(
        r.id,
        r.comment_id,
        r.social_account_id,
        r.body,
        r.content_hash.hex(),
        r.state,
        r.source,
        float(r.confidence) if r.confidence is not None else None,
        r.error,
        r.platform_reply_id,
        r.created_at,
        r.approved_at,
        r.sent_at,
    )


@dataclass(frozen=True, slots=True)
class CommentView:
    id: uuid.UUID
    published_post_id: uuid.UUID
    social_account_id: uuid.UUID
    account_nickname: str
    account_handle: str
    account_group_id: uuid.UUID | None
    platform: str
    author_handle: str | None
    body: str
    permalink: str | None
    post_permalink: str | None
    post_excerpt: str
    sentiment: str | None
    intent: str | None
    state: str
    escalation_reason: str | None
    is_own: bool
    created_at_platform: datetime | None
    created_at: datetime
    replies: list[ReplyView]
    can_hide: bool
    can_delete: bool


class InboxService(WorkspaceScopedService):
    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        engagement: dict[str, EngagementAdapter],
        key_provider: KeyProvider,
        deferrer: Deferrer | None,
        drafting: Drafting | None = None,
        *,
        triage_per_poll: int = 50,
        mfa_enforced: bool = False,
    ) -> None:
        super().__init__(sm)
        self._mfa_enforced = mfa_enforced
        self._eng = engagement
        self._kp = key_provider
        self._deferrer = deferrer
        self._drafting = drafting
        self._triage_cap = triage_per_poll

    def supports(self, platform: str) -> bool:
        return platform in self._eng

    def _creds(self, account: SocialAccount) -> dict[str, Any]:
        raw = self._kp.decrypt(
            str(account.credentials_ct),
            workspace_id=account.workspace_id,
            purpose=CREDENTIALS_PURPOSE,
            record_id=account.id,
        )
        out: dict[str, Any] = json.loads(raw)
        return out

    # =================================================================== polling

    async def poll_all(self, now: datetime | None = None) -> dict[str, int]:
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
        counts: Counter[str] = Counter()
        for ws in ws_ids:
            try:
                counts.update(await self.poll_workspace(ws, now))
            except Exception as exc:  # one workspace must not stop the others
                log.error("inbox.poll_failed", workspace_id=str(ws), error=type(exc).__name__)
                counts["error"] += 1
        if counts:
            log.info("inbox.poll", **counts)
        return dict(counts)

    async def poll_workspace(
        self, workspace_id: uuid.UUID, now: datetime, *, due_only: bool = True
    ) -> Counter[str]:
        counts: Counter[str] = Counter()
        ctx = WorkspaceContext(workspace_id)
        async with workspace_transaction(self._sm, ctx) as s:
            due = list(
                (
                    await s.execute(
                        select(PublishedPost, SocialAccount)
                        .join(SocialAccount, SocialAccount.id == PublishedPost.social_account_id)
                        .where(
                            PublishedPost.deleted_at.is_(None),
                            PublishedPost.next_comments_at <= now
                            if due_only
                            else PublishedPost.next_comments_at.is_not(None),
                        )
                        .order_by(PublishedPost.next_comments_at)
                        .limit(POSTS_PER_RUN)
                    )
                ).all()
            )
        for post, account in due:
            adapter = self._eng.get(post.platform)
            nxt = next_poll(post.published_at, now)
            if adapter is None or account.status != "active" or account.credentials_ct is None:
                await self._reschedule(ctx, post.id, None if adapter is None else nxt, None)
                counts["skipped"] += 1
                continue
            try:
                found = await adapter.fetch_comments(
                    self._creds(account),
                    PostRef(
                        post.platform_post_id, account.platform_account_id, dict(post.extra_ids)
                    ),
                    post.comment_cursor_at,
                )
            except RateLimited as exc:
                retry = now + timedelta(seconds=exc.retry_after or 900)
                await self._reschedule(ctx, post.id, retry, None)
                counts["rate_limited"] += 1
                continue
            except AuthExpired:
                await self._reschedule(ctx, post.id, nxt, None)
                counts["auth_expired"] += 1
                continue
            except PublishError as exc:
                log.info("inbox.fetch_failed", post=str(post.id), error=str(exc)[:200])
                await self._reschedule(ctx, post.id, nxt, None)
                counts["fetch_failed"] += 1
                continue
            newest = max((c.created_at for c in found if c.created_at), default=None)
            async with workspace_transaction(self._sm, ctx) as s:
                parents: dict[str, uuid.UUID] = {}
                for c in found:
                    row = {
                        "id": uuid7(),
                        "workspace_id": workspace_id,
                        "published_post_id": post.id,
                        # The identity a reply may use: the post's account, never the text's.
                        "social_account_id": post.social_account_id,
                        "account_group_id": post.account_group_id,
                        "platform": post.platform,
                        "platform_comment_id": c.platform_comment_id[:500],
                        "platform_parent_id": c.platform_parent_id,
                        "author_platform_id": c.author_platform_id,
                        "author_handle": (c.author_handle or "")[:200] or None,
                        "body": c.body[:20000],
                        "permalink": c.permalink,
                        "extra": dict(c.extra),
                        "is_own": c.is_own,
                        "state": "triaged" if c.is_own else "new",
                        "created_at_platform": c.created_at,
                        "parent_comment_id": parents.get(c.platform_parent_id or ""),
                    }
                    inserted = await s.scalar(
                        insert(Comment).values(row).on_conflict_do_nothing().returning(Comment.id)
                    )
                    if inserted is not None:
                        parents[c.platform_comment_id] = inserted
                        counts["new"] += 1
            await self._reschedule(ctx, post.id, nxt, newest)
            counts["polled"] += 1
        # The triage budget covers the backlog too, oldest first: comments over the cap in
        # an earlier poll are triaged by the next one, never left "new" for ever.
        async with workspace_transaction(self._sm, ctx) as s:
            backlog = list(
                (
                    await s.execute(
                        select(Comment.id)
                        .where(Comment.state == "new", Comment.is_own.is_(False))
                        .order_by(Comment.created_at, Comment.id)
                        .limit(self._triage_cap)
                    )
                ).scalars()
            )
        for cid in backlog:
            try:
                await self.triage_one(workspace_id, cid)
                counts["triaged"] += 1
            except (CostCapReached, LLMError) as exc:
                counts["triage_deferred"] += 1
                log.info("inbox.triage_deferred", reason=type(exc).__name__)
                break
        return counts

    async def _reschedule(
        self,
        ctx: WorkspaceContext,
        post_id: uuid.UUID,
        at: datetime | None,
        cursor: datetime | None,
    ) -> None:
        values: dict[str, Any] = {"next_comments_at": at}
        if cursor is not None:
            values["comment_cursor_at"] = func.greatest(
                func.coalesce(PublishedPost.comment_cursor_at, cursor), cursor
            )
        async with workspace_transaction(self._sm, ctx) as s:
            await s.execute(update(PublishedPost).where(PublishedPost.id == post_id).values(values))

    # ==================================================================== triage

    async def _brief(
        self, s: AsyncSession, c: Comment, instruction: str | None = None
    ) -> EngagementBrief:
        post = await s.get(PublishedPost, c.published_post_id)
        assert post is not None
        version = await s.get(PostVersion, post.post_version_id)
        variant = await s.get(PostVariant, post.post_variant_id)
        account = await s.get(SocialAccount, c.social_account_id)
        voice = await core.voice_for_variant(s, variant) if variant else None
        thread = []
        if c.parent_comment_id:
            parent = await s.get(Comment, c.parent_comment_id)
            if parent:
                thread.append(parent.body)
        links = tuple(x for x in [version.link_url if version else None] if x)
        return EngagementBrief(
            comment=c.body,
            commenter=c.author_handle,
            post_text=version.body if version else "",
            platform_name=core.platform_name(c.platform),
            account_name=account.nickname if account else "",
            voice_summary="; ".join(
                x
                for x in [voice.tone if voice else None, voice.target_audience if voice else None]
                if x
            )
            or None,
            thread=tuple(thread),
            instruction=instruction,
            allowed_links=links,
        )

    async def _rules(self, s: AsyncSession, c: Comment) -> list[EngagementRule]:
        groups = list(
            (
                await s.execute(
                    select(AccountGroupMember.account_group_id).where(
                        AccountGroupMember.social_account_id == c.social_account_id
                    )
                )
            ).scalars()
        )
        rows = list(
            (
                await s.execute(
                    select(EngagementRule)
                    .where(EngagementRule.enabled.is_(True))
                    .order_by(EngagementRule.priority, EngagementRule.created_at)
                )
            ).scalars()
        )
        rank = {"social_account": 0, "account_group": 1, "workspace": 2}
        applicable = [
            r
            for r in rows
            if r.scope_type == "workspace"
            or (r.scope_type == "social_account" and r.scope_id == c.social_account_id)
            or (r.scope_type == "account_group" and r.scope_id in groups)
        ]
        # Account rules, then group, then workspace (docs/06 §4.10); priority within each.
        return sorted(applicable, key=lambda r: (rank[r.scope_type], r.priority))

    async def triage_one(self, workspace_id: uuid.UUID, comment_id: uuid.UUID) -> str:
        """Rules first (keyword rules need no model), then the agent, then the rules that
        depend on its classification. Returns the comment's state afterwards."""
        ctx = WorkspaceContext(workspace_id)
        async with workspace_transaction(self._sm, ctx) as s:
            c = await s.get(Comment, comment_id)
            if c is None or c.state != "new":
                return c.state if c else "missing"
            rules = await self._rules(s, c)
            brief = await self._brief(s, c)
        rule = next((r for r in rules if rule_matches(r, brief.comment, None)), None)
        triage: EngagementTriage | None = None
        run = None
        if rule is None or rule.action == "draft_reply":
            triage, run = await self._run_agent(workspace_id, comment_id, brief)
            if rule is None:
                rule = next((r for r in rules if rule_matches(r, brief.comment, triage)), None)
        return await self._apply(workspace_id, comment_id, triage, rule, run, brief)

    async def _run_agent(
        self, workspace_id: uuid.UUID, comment_id: uuid.UUID, brief: EngagementBrief
    ) -> tuple[EngagementTriage | None, uuid.UUID | None]:
        if self._drafting is None:
            return None, None
        run = await agent_runs.start_run(
            self._sm,
            workspace_id,
            workflow_key="engagement",
            trigger="event",
            subject_type="comment",
            subject_id=comment_id,
        )
        ctx = AgentContext(
            self._sm,
            run,
            self._drafting.config.daily_cost_cap_usd,
            monthly_cap_usd=self._drafting.config.monthly_cost_cap_usd,
        )
        try:
            out = await self._drafting.engagement.run(ctx, brief)
        except (CostCapReached, LLMError) as exc:
            await agent_runs.finish_run(self._sm, run, state="failed", error=type(exc).__name__)
            raise
        await agent_runs.finish_run(self._sm, run, state="succeeded")
        return out, run.run_id

    async def _apply(
        self,
        workspace_id: uuid.UUID,
        comment_id: uuid.UUID,
        triage: EngagementTriage | None,
        rule: EngagementRule | None,
        run_id: uuid.UUID | None,
        brief: EngagementBrief,
    ) -> str:
        hide = False
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            c = await s.get(Comment, comment_id, with_for_update=True)
            if c is None or c.state != "new":
                return c.state if c else "missing"
            now = datetime.now(UTC)
            c.triaged_at, c.updated_at = now, now
            if triage is not None:
                c.sentiment, c.intent = triage.sentiment, triage.intent
                c.toxicity_score = Decimal(str(round(triage.toxicity_score, 3)))
                c.spam_score = Decimal(str(round(triage.spam_score, 3)))
                c.language = triage.language
                c.escalation_reason = triage.escalation_reason
            c.matched_rule_id = rule.id if rule else None
            draft: str | None = None
            action = rule.action if rule else None
            if action == "ignore":
                c.state = "ignored"
            elif action == "hide":
                c.state, hide = "hidden", True
            elif action == "escalate":
                c.state = "escalated"
                c.escalation_reason = c.escalation_reason or f"Rule: {rule.name if rule else ''}"
            else:
                if action == "draft_reply" and rule and rule.reply_template:
                    draft = rule.reply_template
                elif triage is not None and triage.intent in ("question", "praise"):
                    draft = triage.suggested_reply
                c.state = (
                    "escalated"
                    if triage is not None
                    and (triage.intent == "complaint" or triage.escalation_reason)
                    else "triaged"
                )
            body = clean_reply(draft, brief)
            if body:
                s.add(
                    Reply(
                        id=uuid7(),
                        workspace_id=workspace_id,
                        comment_id=c.id,
                        social_account_id=c.social_account_id,  # never from the agent
                        body=body,
                        content_hash=reply_hash(c.social_account_id, c.id, body),
                        state="draft",
                        source="agent",
                        confidence=Decimal(str(round(triage.reply_confidence, 3)))
                        if triage
                        else None,
                        agent_run_id=run_id,
                    )
                )
            state = c.state
        if hide:
            await self._platform_hide(workspace_id, comment_id, True)
        return state

    async def poll_now(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta, *, sandbox: bool
    ) -> dict[str, int]:
        """Sandbox only: fetch comments on every recent post now instead of on the poll's
        schedule (the sandbox inspector, demos and the browser tests)."""
        if not sandbox:
            raise Forbidden("Available in sandbox mode only.", code="sandbox_only")
        async with self._tx(user, workspace_id, meta) as s:
            await self._account_check_any(s, user, workspace_id)
        return dict(await self.poll_workspace(workspace_id, datetime.now(UTC), due_only=False))

    async def _account_check_any(
        self, s: AsyncSession, user: AuthenticatedUser, workspace_id: uuid.UUID
    ) -> None:
        from mera.domain.policies import rbac

        principal = await load_user_principal(s, user, workspace_id)
        rbac.require(principal, Permission.REPLY, workspace_id)

    # ===================================================================== reads

    async def _account_check(
        self,
        s: AsyncSession,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        account_id: uuid.UUID,
        perm: Permission,
    ) -> None:
        principal = await load_user_principal(s, user, workspace_id)
        groups = list(
            (
                await s.execute(
                    select(AccountGroupMember.account_group_id).where(
                        AccountGroupMember.social_account_id == account_id
                    )
                )
            ).scalars()
        )
        self._require_for_account(principal, perm, workspace_id, account_id, groups)

    async def summary(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> dict[str, Any]:
        async with self._tx(user, workspace_id, meta) as s:
            await load_user_principal(s, user, workspace_id)
            rows = (
                await s.execute(select(Comment.state, func.count()).group_by(Comment.state))
            ).all()
            pending = await s.scalar(
                select(func.count()).select_from(Reply).where(Reply.state == "pending_approval")
            )
        return {
            "states": {k: int(n) for k, n in rows},
            "replies_pending_approval": int(pending or 0),
            "platforms": sorted(self._eng),
        }

    async def list_comments(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        state: str | None = None,
        platform: str | None = None,
        social_account_id: uuid.UUID | None = None,
        account_group_id: uuid.UUID | None = None,
        sentiment: str | None = None,
        intent: str | None = None,
        limit: int = 50,
        before: datetime | None = None,
    ) -> list[CommentView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            q = (
                select(Comment)
                .where(Comment.is_own.is_(False))
                .order_by(Comment.created_at.desc())
                .limit(min(limit, 200))
            )
            if state:
                q = q.where(Comment.state == state)
            else:
                q = q.where(Comment.state.not_in(("deleted",)))
            if platform:
                q = q.where(Comment.platform == platform)
            if social_account_id:
                q = q.where(Comment.social_account_id == social_account_id)
            if account_group_id:
                members = select(AccountGroupMember.social_account_id).where(
                    AccountGroupMember.account_group_id == account_group_id
                )
                q = q.where(Comment.social_account_id.in_(members))
            if sentiment:
                q = q.where(Comment.sentiment == sentiment)
            if intent:
                q = q.where(Comment.intent == intent)
            if before:
                q = q.where(Comment.created_at < before)
            rows = list((await s.execute(q)).scalars())
            # One scope check per account, not per comment: a page of 100 comments is a
            # handful of queries whatever the number of accounts (P2-M10.3 load check).
            account_ids = {c.social_account_id for c in rows}
            groups: dict[uuid.UUID, list[uuid.UUID]] = {a: [] for a in account_ids}
            if account_ids:
                for acc, grp in (
                    await s.execute(
                        select(
                            AccountGroupMember.social_account_id,
                            AccountGroupMember.account_group_id,
                        ).where(AccountGroupMember.social_account_id.in_(account_ids))
                    )
                ).all():
                    groups[acc].append(grp)
            readable = set()
            for acc in account_ids:
                try:
                    self._require_for_account(
                        principal, Permission.READ, workspace_id, acc, groups[acc]
                    )
                except PermissionDenied:  # out of this member's scope: not shown
                    continue
                readable.add(acc)
            visible = [c for c in rows if c.social_account_id in readable]
            return await self._comment_views(s, visible)

    async def get_comment(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        comment_id: uuid.UUID,
    ) -> CommentView:
        async with self._tx(user, workspace_id, meta) as s:
            c = await s.get(Comment, comment_id)
            if c is None:
                raise NotFoundError("Comment not found.")
            await self._account_check(s, user, workspace_id, c.social_account_id, Permission.READ)
            return await self._comment_view(s, c)

    async def _comment_view(self, s: AsyncSession, c: Comment) -> CommentView:
        return (await self._comment_views(s, [c]))[0]

    async def _comment_views(self, s: AsyncSession, comments: list[Comment]) -> list[CommentView]:
        """Views for many comments with one query per related table."""
        if not comments:
            return []
        post_ids = {c.published_post_id for c in comments}
        posts = {
            p.id: p
            for p in (
                await s.execute(select(PublishedPost).where(PublishedPost.id.in_(post_ids)))
            ).scalars()
        }
        version_ids = {p.post_version_id for p in posts.values() if p.post_version_id}
        versions = (
            {
                v.id: v
                for v in (
                    await s.execute(select(PostVersion).where(PostVersion.id.in_(version_ids)))
                ).scalars()
            }
            if version_ids
            else {}
        )
        account_ids = {c.social_account_id for c in comments}
        accounts = {
            a.id: a
            for a in (
                await s.execute(select(SocialAccount).where(SocialAccount.id.in_(account_ids)))
            ).scalars()
        }
        replies: dict[uuid.UUID, list[Reply]] = {c.id: [] for c in comments}
        for r in (
            await s.execute(
                select(Reply).where(Reply.comment_id.in_(list(replies))).order_by(Reply.created_at)
            )
        ).scalars():
            replies[r.comment_id].append(r)
        return [
            self._build_view(
                c,
                posts.get(c.published_post_id),
                accounts.get(c.social_account_id),
                versions,
                replies[c.id],
            )
            for c in comments
        ]

    def _build_view(
        self,
        c: Comment,
        post: PublishedPost | None,
        account: SocialAccount | None,
        versions: dict[uuid.UUID, PostVersion],
        replies: list[Reply],
    ) -> CommentView:
        version = versions.get(post.post_version_id) if post and post.post_version_id else None
        adapter = self._eng.get(c.platform)
        return CommentView(
            id=c.id,
            published_post_id=c.published_post_id,
            social_account_id=c.social_account_id,
            account_nickname=account.nickname if account else "",
            account_handle=account.handle if account else "",
            account_group_id=c.account_group_id,
            platform=c.platform,
            author_handle=c.author_handle,
            body=c.body,
            permalink=c.permalink,
            post_permalink=post.permalink if post else None,
            post_excerpt=(version.body if version else "")[:280],
            sentiment=c.sentiment,
            intent=c.intent,
            state=c.state,
            escalation_reason=c.escalation_reason,
            is_own=c.is_own,
            created_at_platform=c.created_at_platform,
            created_at=c.created_at,
            replies=[_reply_view(r) for r in replies],
            can_hide=bool(adapter and adapter.can_hide),
            can_delete=bool(adapter and adapter.can_delete),
        )

    # =================================================================== triage (people)

    async def set_state(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        comment_ids: list[uuid.UUID],
        *,
        state: str,
        assigned_to_user_id: uuid.UUID | None = None,
    ) -> int:
        """Bulk triage: mark comments new, triaged, ignored or escalated."""
        if state not in TRIAGE_TO:
            raise InvalidInput("Choose new, triaged, ignored or escalated.", code="invalid_state")
        if not 1 <= len(comment_ids) <= 200:
            raise InvalidInput("Between 1 and 200 comments.", code="invalid_count")
        changed = 0
        async with self._tx(user, workspace_id, meta) as s:
            ws = await s.get(Workspace, workspace_id)
            assert ws is not None
            self._ensure_writable(ws)
            for cid in comment_ids:
                c = await s.get(Comment, cid, with_for_update=True)
                if c is None or c.state in ("deleted", "replied", "hidden"):
                    continue
                await self._account_check(
                    s, user, workspace_id, c.social_account_id, Permission.REPLY
                )
                c.state = state
                c.updated_at = datetime.now(UTC)
                if assigned_to_user_id is not None:
                    c.assigned_to_user_id = assigned_to_user_id
                changed += 1
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "comment.triaged",
                    workspace_id=workspace_id,
                    after={"state": state, "count": changed},
                ),
            )
        return changed

    # ==================================================================== replies

    async def draft_reply(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        comment_id: uuid.UUID,
        *,
        body: str | None = None,
        instruction: str | None = None,
    ) -> ReplyView:
        """A reply draft: the text given, or the Engagement agent's. Either way, it is the
        commented account's reply and it waits for approval."""
        async with self._tx(user, workspace_id, meta) as s:
            c = await s.get(Comment, comment_id)
            if c is None or c.state == "deleted":
                raise NotFoundError("Comment not found.")
            await self._account_check(s, user, workspace_id, c.social_account_id, Permission.REPLY)
            ws = await s.get(Workspace, workspace_id)
            assert ws is not None
            self._ensure_writable(ws)
            brief = await self._brief(s, c, instruction)
        source, confidence, run_id = "human", None, None
        if body is None:
            try:
                triage, run_id = await self._run_agent(workspace_id, comment_id, brief)
            except CostCapReached as exc:
                raise ConflictError("The AI budget for today is spent.", code="cost_cap") from exc
            except LLMError as exc:
                raise ConflictError(
                    "The Engagement agent is unavailable right now.", code="agent_unavailable"
                ) from exc
            body = clean_reply(triage.suggested_reply if triage else None, brief)
            if not body:
                raise ConflictError(
                    (triage.escalation_reason if triage else None)
                    or "The agent would not draft a reply to this; write one yourself.",
                    code="no_draft",
                )
            source, confidence = "agent", triage.reply_confidence if triage else None
        text = " ".join(body.split()) if source == "agent" else body.strip()
        if not 1 <= len(text) <= 3000:
            raise InvalidInput(
                "A reply is 1 to 3000 characters.", code="invalid_body", field="body"
            )
        async with self._tx(user, workspace_id, meta) as s:
            c = await s.get(Comment, comment_id)
            assert c is not None
            r = Reply(
                id=uuid7(),
                workspace_id=workspace_id,
                comment_id=c.id,
                social_account_id=c.social_account_id,
                body=text,
                content_hash=reply_hash(c.social_account_id, c.id, text),
                state="draft",
                source=source,
                confidence=Decimal(str(round(confidence, 3))) if confidence is not None else None,
                agent_run_id=run_id,
                created_by_user_id=user.user_id,
            )
            s.add(r)
            await s.flush()
            await s.refresh(r)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "reply.drafted",
                    workspace_id=workspace_id,
                    subject_type="reply",
                    subject_id=r.id,
                    social_account_id=c.social_account_id,
                    after={"source": source},
                ),
            )
            return _reply_view(r)

    async def _reply_for(
        self,
        s: AsyncSession,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        reply_id: uuid.UUID,
        perm: Permission,
    ) -> Reply:
        r = await s.get(Reply, reply_id, with_for_update=True)
        if r is None:
            raise NotFoundError("Reply not found.")
        await self._account_check(s, user, workspace_id, r.social_account_id, perm)
        ws = await s.get(Workspace, workspace_id)
        assert ws is not None
        self._ensure_writable(ws)
        return r

    async def edit_reply(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        reply_id: uuid.UUID,
        *,
        body: str,
    ) -> ReplyView:
        text = body.strip()
        if not 1 <= len(text) <= 3000:
            raise InvalidInput(
                "A reply is 1 to 3000 characters.", code="invalid_body", field="body"
            )
        async with self._tx(user, workspace_id, meta) as s:
            r = await self._reply_for(s, user, workspace_id, reply_id, Permission.REPLY)
            if r.state not in ("draft", "pending_approval", "rejected", "failed"):
                raise ConflictError("This reply can no longer be edited.", code="reply_locked")
            r.body = text
            r.content_hash = reply_hash(r.social_account_id, r.comment_id, text)
            # An edit voids any approval: it is a different reply now.
            r.state, r.approved_hash, r.approved_at, r.approved_by_user_id = (
                "draft",
                None,
                None,
                None,
            )
            r.source, r.updated_at = "human", datetime.now(UTC)
            await s.flush()
            await s.refresh(r)
            return _reply_view(r)

    async def submit_reply(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        reply_id: uuid.UUID,
    ) -> ReplyView:
        """Ask for approval. Approvers with a paired chat get a reply card."""
        async with self._tx(user, workspace_id, meta) as s:
            r = await self._reply_for(s, user, workspace_id, reply_id, Permission.REPLY)
            if r.state not in ("draft", "rejected", "failed"):
                raise ConflictError("This reply is not a draft.", code="reply_locked")
            await self._banned(s, r)
            r.state, r.updated_at = "pending_approval", datetime.now(UTC)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "reply.submitted",
                    workspace_id=workspace_id,
                    subject_type="reply",
                    subject_id=r.id,
                    social_account_id=r.social_account_id,
                    subject_content_hash=r.content_hash,
                ),
            )
            await s.flush()
            await s.refresh(r)
            view = _reply_view(r)
        if self._deferrer is not None:
            await self._deferrer.defer(
                DISPATCH_TASK,
                queue="approvals",
                queueing_lock=f"reply-card:{reply_id}",
                kind="reply",
                workspace_id=str(workspace_id),
                subject_id=str(reply_id),
            )
        return view

    async def _banned(self, s: AsyncSession, r: Reply) -> None:
        c = await s.get(Comment, r.comment_id)
        post = await s.get(PublishedPost, c.published_post_id) if c else None
        variant = await s.get(PostVariant, post.post_variant_id) if post else None
        voice = await core.voice_for_variant(s, variant) if variant else None
        low = r.body.lower()
        hit = [w for w in (voice.banned_words if voice else ()) if w and w.lower() in low]
        if hit:
            raise InvalidInput(
                f"The reply uses a word this account's voice bans: {hit[0]}.",
                code="banned_word",
                field="body",
            )

    async def approve_reply(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        reply_id: uuid.UUID,
        *,
        content_hash: str,
    ) -> ReplyView:
        """Approve exactly the text the approver saw (``content_hash``), then send."""
        self._require_human(user)
        async with self._tx(user, workspace_id, meta) as s:
            r = await self._reply_for(s, user, workspace_id, reply_id, Permission.APPROVE)
            view = await self._approve_locked(s, r, user.user_id, content_hash)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "reply.approved",
                    workspace_id=workspace_id,
                    subject_type="reply",
                    subject_id=r.id,
                    social_account_id=r.social_account_id,
                    subject_content_hash=r.content_hash,
                ),
            )
        await self._defer_send(workspace_id, reply_id)
        return view

    async def _approve_locked(
        self, s: AsyncSession, r: Reply, user_id: uuid.UUID, content_hash: str
    ) -> ReplyView:
        if r.state not in ("draft", "pending_approval"):
            raise ConflictError("This reply is not waiting for approval.", code="reply_locked")
        if content_hash.lower() != r.content_hash.hex():
            raise ConflictError(
                "The reply changed since you opened it; review it again.", code="stale_hash"
            )
        await self._banned(s, r)
        now = datetime.now(UTC)
        r.state, r.approved_hash, r.approved_at = "approved", r.content_hash, now
        r.approved_by_user_id, r.updated_at = user_id, now
        await s.flush()
        await s.refresh(r)
        return _reply_view(r)

    async def approve_from_channel(
        self,
        workspace_id: uuid.UUID,
        reply_id: uuid.UUID,
        user_id: uuid.UUID,
        card_nonce: str,
        surface: str,
    ) -> str:
        """A tap on a reply card. The caller has checked the chat's binding; this checks
        the person's approve permission on the account, and that the card is the one last
        sent for exactly this content (its nonce)."""
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            from mera.infrastructure.db.models import User

            u = await s.get(User, user_id)
            r = await s.get(Reply, reply_id, with_for_update=True)
            if u is None or r is None:
                return "missing"
            who = acting_user(u, mfa_enforced=self._mfa_enforced)
            try:
                await self._account_check(
                    s, who, workspace_id, r.social_account_id, Permission.APPROVE
                )
            except PermissionDenied:
                return "forbidden"
            if not _card_matches(r, card_nonce):
                return "stale"
            if r.state not in ("draft", "pending_approval"):
                return "closed"
            await self._approve_locked(s, r, u.id, r.content_hash.hex())
            await record(
                s,
                AuditEntry(
                    action="reply.approved",
                    actor_type="user",
                    actor_id=u.id,
                    actor_label=u.display_name,
                    surface=surface,
                    workspace_id=workspace_id,
                    subject_type="reply",
                    subject_id=r.id,
                    social_account_id=r.social_account_id,
                    subject_content_hash=r.content_hash,
                ),
            )
        await self._defer_send(workspace_id, reply_id)
        return "approved"

    async def can_approve(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID, account_id: uuid.UUID
    ) -> bool:
        from mera.infrastructure.db.models import User

        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            u = await s.get(User, user_id)
            if u is None:
                return False
            who = acting_user(u, mfa_enforced=self._mfa_enforced)
            try:
                await self._account_check(s, who, workspace_id, account_id, Permission.APPROVE)
            except (PermissionDenied, Forbidden, NotFoundError):
                return False
            return True

    async def reject_from_channel(
        self,
        workspace_id: uuid.UUID,
        reply_id: uuid.UUID,
        user_id: uuid.UUID,
        card_nonce: str,
        surface: str,
    ) -> str:
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            r = await s.get(Reply, reply_id, with_for_update=True)
            if r is None:
                return "missing"
            account_id = r.social_account_id
        if not await self.can_approve(workspace_id, user_id, account_id):
            return "forbidden"
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            r = await s.get(Reply, reply_id, with_for_update=True)
            assert r is not None
            if not _card_matches(r, card_nonce):
                return "stale"
            if r.state not in ("draft", "pending_approval"):
                return "closed"
            r.state, r.updated_at = "rejected", datetime.now(UTC)
            await record(
                s,
                AuditEntry(
                    action="reply.rejected",
                    actor_type="user",
                    actor_id=user_id,
                    surface=surface,
                    workspace_id=workspace_id,
                    subject_type="reply",
                    subject_id=r.id,
                    social_account_id=r.social_account_id,
                ),
            )
        return "rejected"

    async def reject_reply(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        reply_id: uuid.UUID,
    ) -> ReplyView:
        async with self._tx(user, workspace_id, meta) as s:
            r = await self._reply_for(s, user, workspace_id, reply_id, Permission.APPROVE)
            if r.state not in ("draft", "pending_approval"):
                raise ConflictError("This reply is not waiting for approval.", code="reply_locked")
            r.state, r.updated_at = "rejected", datetime.now(UTC)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "reply.rejected",
                    workspace_id=workspace_id,
                    subject_type="reply",
                    subject_id=r.id,
                    social_account_id=r.social_account_id,
                ),
            )
            await s.flush()
            await s.refresh(r)
            return _reply_view(r)

    async def _defer_send(self, workspace_id: uuid.UUID, reply_id: uuid.UUID) -> None:
        if self._deferrer is not None:
            await self._deferrer.defer(
                SEND_TASK,
                queue="publish",
                queueing_lock=f"reply:{reply_id}",
                workspace_id=str(workspace_id),
                reply_id=str(reply_id),
            )

    async def send_reply(self, workspace_id: uuid.UUID, reply_id: uuid.UUID) -> str:
        """The worker's send: claim, check, call the platform, record."""
        ctx = WorkspaceContext(workspace_id)
        async with workspace_transaction(self._sm, ctx) as s:
            r = await s.get(Reply, reply_id, with_for_update=True)
            if r is None or r.state != "approved":
                return r.state if r else "missing"
            try:
                comment, post, account = await assert_reply_sendable(s, r)
            except NotSendable as exc:
                r.state, r.error = "failed", f"{exc.code}: {exc.message}"
                await record(
                    s,
                    AuditEntry(
                        action="reply.refused",
                        actor_type="system",
                        surface="worker",
                        workspace_id=workspace_id,
                        subject_type="reply",
                        subject_id=r.id,
                        social_account_id=r.social_account_id,
                        after={"code": exc.code},
                    ),
                )
                return "refused:" + exc.code
            r.state, r.attempt_count = "sending", r.attempt_count + 1
            adapter = self._eng.get(post.platform)
            creds = self._creds(account)
            postref = PostRef(
                post.platform_post_id, account.platform_account_id, dict(post.extra_ids)
            )
            cref = CommentRef(comment.platform_comment_id, dict(comment.extra))
            body, key = r.body, f"mera-reply-{r.id}"
        if adapter is None:
            await self._finish_send(
                ctx, reply_id, "failed", error="replies are not available on this platform"
            )
            return "failed"
        try:
            pid = await adapter.reply(creds, postref, cref, body, key)
        except PublishError as exc:
            await self._finish_send(ctx, reply_id, "failed", error=str(exc)[:500])
            return "failed"
        await self._finish_send(ctx, reply_id, "sent", platform_reply_id=pid)
        return "sent"

    async def _finish_send(
        self,
        ctx: WorkspaceContext,
        reply_id: uuid.UUID,
        state: str,
        *,
        error: str | None = None,
        platform_reply_id: str | None = None,
    ) -> None:
        async with workspace_transaction(self._sm, ctx) as s:
            r = await s.get(Reply, reply_id, with_for_update=True)
            assert r is not None
            r.state, r.error, r.updated_at = state, error, datetime.now(UTC)
            if state == "sent":
                r.platform_reply_id, r.sent_at = platform_reply_id, datetime.now(UTC)
                c = await s.get(Comment, r.comment_id)
                if c is not None:
                    c.state = "replied"
            await record(
                s,
                AuditEntry(
                    action=f"reply.{state}",
                    actor_type="system",
                    surface="worker",
                    workspace_id=ctx.workspace_id,
                    subject_type="reply",
                    subject_id=r.id,
                    social_account_id=r.social_account_id,
                    subject_content_hash=r.content_hash,
                ),
            )

    # ============================================================ hide and delete

    async def _platform_hide(
        self, workspace_id: uuid.UUID, comment_id: uuid.UUID, hidden: bool
    ) -> None:
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            c = await s.get(Comment, comment_id)
            account = await s.get(SocialAccount, c.social_account_id) if c else None
        adapter = self._eng.get(c.platform) if c else None
        if c is None or account is None or adapter is None or not adapter.can_hide:
            return
        try:
            await adapter.hide(
                self._creds(account), CommentRef(c.platform_comment_id, dict(c.extra)), hidden
            )
        except (PublishError, NotImplementedError) as exc:
            log.info("inbox.hide_failed", comment=str(comment_id), error=str(exc)[:200])

    async def hide_comment(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        comment_id: uuid.UUID,
        *,
        hidden: bool,
    ) -> CommentView:
        async with self._tx(user, workspace_id, meta) as s:
            c = await s.get(Comment, comment_id, with_for_update=True)
            if c is None or c.state == "deleted":
                raise NotFoundError("Comment not found.")
            await self._account_check(s, user, workspace_id, c.social_account_id, Permission.REPLY)
            adapter = self._eng.get(c.platform)
            if adapter is None or not adapter.can_hide:
                raise ConflictError(
                    f"{core.platform_name(c.platform)} does not let Mera hide comments.",
                    code="not_supported",
                )
            account = await s.get(SocialAccount, c.social_account_id)
            assert account is not None
            await adapter.hide(
                self._creds(account), CommentRef(c.platform_comment_id, dict(c.extra)), hidden
            )
            c.state = "hidden" if hidden else "triaged"
            c.updated_at = datetime.now(UTC)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "comment.hidden" if hidden else "comment.unhidden",
                    workspace_id=workspace_id,
                    subject_type="comment",
                    subject_id=c.id,
                    social_account_id=c.social_account_id,
                ),
            )
            return await self._comment_view(s, c)

    async def describe_delete(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        comment_id: uuid.UUID,
    ) -> str:
        """What a confirmation page shows for deleting a comment, after the same checks the
        deletion itself makes: the person may reply on that account, and it can be done."""
        async with self._tx(user, workspace_id, meta) as s:
            c = await s.get(Comment, comment_id)
            if c is None or c.state == "deleted":
                raise NotFoundError("Comment not found.")
            await self._account_check(s, user, workspace_id, c.social_account_id, Permission.REPLY)
            adapter = self._eng.get(c.platform)
            if adapter is None or not adapter.can_delete:
                raise ConflictError(
                    f"{core.platform_name(c.platform)} does not let Mera delete comments.",
                    code="not_supported",
                )
            account = await s.get(SocialAccount, c.social_account_id)
            body = " ".join(c.body.split())
            quoted = body if len(body) <= 140 else body[:139] + "…"
            where = (account.nickname or account.handle) if account else c.platform
            return (
                f"Delete @{c.author_handle}'s comment on {core.platform_name(c.platform)} "
                f"({where}): “{quoted}”"
            )[:300]

    async def delete_comment(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        comment_id: uuid.UUID,
    ) -> None:
        """Deleting someone's comment is final, so it takes a second factor (P2-M6.7)."""
        async with self._tx(user, workspace_id, meta) as s:
            c = await s.get(Comment, comment_id, with_for_update=True)
            if c is None or c.state == "deleted":
                raise NotFoundError("Comment not found.")
            await self._account_check(s, user, workspace_id, c.social_account_id, Permission.REPLY)
            adapter = self._eng.get(c.platform)
            if adapter is None or not adapter.can_delete:
                raise ConflictError(
                    f"{core.platform_name(c.platform)} does not let Mera delete comments.",
                    code="not_supported",
                )
            await second_factor.require(s, user, workspace_id, "comment_delete", meta)
            account = await s.get(SocialAccount, c.social_account_id)
            assert account is not None
            await adapter.delete(
                self._creds(account), CommentRef(c.platform_comment_id, dict(c.extra))
            )
            c.state, c.deleted_at, c.updated_at = "deleted", datetime.now(UTC), datetime.now(UTC)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "comment.deleted",
                    workspace_id=workspace_id,
                    subject_type="comment",
                    subject_id=c.id,
                    social_account_id=c.social_account_id,
                    before={"author": c.author_handle, "chars": len(c.body)},
                ),
            )

    # ===================================================================== rules

    async def list_rules(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> list[EngagementRule]:
        async with self._tx(user, workspace_id, meta) as s:
            await load_user_principal(s, user, workspace_id)
            return list(
                (
                    await s.execute(
                        select(EngagementRule).order_by(
                            EngagementRule.priority, EngagementRule.created_at
                        )
                    )
                ).scalars()
            )

    async def save_rule(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        rule_id: uuid.UUID | None,
        fields: dict[str, Any],
    ) -> EngagementRule:
        action = fields.get("action")
        if action is not None and action not in RULE_ACTIONS:
            raise InvalidInput("Unknown action.", code="invalid_action", field="action")
        match = fields.get("match")
        if match is not None:
            allowed = {"keywords", "intent", "sentiment"}
            if not isinstance(match, dict) or set(match) - allowed or not any(match.values()):
                raise InvalidInput(
                    "Match on keywords, intent or sentiment.", code="invalid_match", field="match"
                )
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            from mera.domain.policies import rbac

            rbac.require(principal, Permission.CONFIGURE_CONTENT, workspace_id)
            ws = await s.get(Workspace, workspace_id)
            assert ws is not None
            self._ensure_writable(ws)
            if rule_id is None:
                rule = EngagementRule(
                    id=uuid7(), workspace_id=workspace_id, created_by_user_id=user.user_id
                )
                s.add(rule)
            else:
                found = await s.get(EngagementRule, rule_id, with_for_update=True)
                if found is None:
                    raise NotFoundError("Rule not found.")
                rule = found
            for k, v in fields.items():
                if v is not None:
                    setattr(rule, k, v)
            # The scope must be a record of this workspace (RLS hides any other), so a rule
            # can never point at another tenant's account or group (release review #10).
            rule.scope_type = rule.scope_type or "workspace"
            if rule.scope_type not in RULE_SCOPES:
                raise InvalidInput("Unknown scope.", code="invalid_scope", field="scope_type")
            if rule.scope_type == "workspace":
                rule.scope_id = None
            else:
                model = SocialAccount if rule.scope_type == "social_account" else AccountGroup
                if rule.scope_id is None or await s.get(model, rule.scope_id) is None:
                    raise InvalidInput(
                        "Choose an account or group in this workspace.",
                        code="invalid_scope",
                        field="scope_id",
                    )
            if rule_id is None and (not rule.name or not rule.action or not rule.match):
                raise InvalidInput(
                    "A rule needs a name, a match and an action.", code="invalid_rule"
                )
            rule.updated_at = datetime.now(UTC)
            await s.flush()
            await s.refresh(rule)
            return rule

    async def delete_rule(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        rule_id: uuid.UUID,
    ) -> None:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            from mera.domain.policies import rbac

            rbac.require(principal, Permission.CONFIGURE_CONTENT, workspace_id)
            rule = await s.get(EngagementRule, rule_id)
            if rule is None:
                raise NotFoundError("Rule not found.")
            await s.delete(rule)


def new_idempotency() -> str:
    return secrets.token_hex(8)


__all__ = ["CommentView", "Forbidden", "InboxService", "ReplyView", "next_poll", "rule_matches"]
