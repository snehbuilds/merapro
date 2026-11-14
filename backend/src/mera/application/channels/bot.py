"""The approval bot runtime (M7): outbound dispatch and inbound updates, one class.

**Outbound** (``dispatch``, on the ``approvals`` queue): cards for a new request go to every
paired member who may approve at least one of its accounts, each card rendered *for that
person* (a scoped approver sees only their accounts) with tokens minted for that person.
Any later change, from any surface, edits the cards in place; a terminal outcome removes
the buttons (docs/07 §3.4).

**Inbound** (``handle_update``): one Telegram update, deduplicated by ``update_id``. Button
taps carry ``<kind>:<workspace>:<…>``; nothing in them is trusted: a decision is a token
validated in its workspace, against the chat's active binding (``identity_mismatch`` for a
forwarded card); every other button is checked against the card's own recipient. Text
replies are data: an edit instruction goes to the Writer fenced, never to a command.

Unpaired chats get one generic answer and learn nothing (docs/07 §6).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.approvals import core as approvals
from mera.application.approvals.card import card_extras
from mera.application.channels import render
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.drafting import core as drafting
from mera.application.errors import ApplicationError, TooManyAttempts
from mera.application.services.approval_service import ApprovalRequestView, ApprovalService
from mera.application.services.principals import acting_user, load_user_principal
from mera.domain.context import WorkspaceContext
from mera.domain.errors import PermissionDenied
from mera.domain.policies.edit_reply import classify
from mera.domain.policies.rbac import Permission, require
from mera.domain.policies.scheduling import zone
from mera.domain.ports.approval_channel import (
    ApprovalChannel,
    Button,
    ChannelError,
    OutboundMessage,
)
from mera.domain.state_machines.approval import is_open
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.db.models import (
    AccountGroup,
    AccountGroupMember,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalToken,
    ApproverBinding,
    BotSecondFactor,
    ChannelMessage,
    ChannelPairingAttempt,
    ChannelUpdate,
    PairingCode,
    PostSet,
    PostVariant,
    PostVersion,
    PublishedPost,
    SandboxCapture,
    ScheduledPost,
    SocialAccount,
    User,
    Workspace,
    WorkspaceMember,
)
from mera.infrastructure.db.models import (
    ApprovalChannel as ApprovalChannelRow,
)
from mera.infrastructure.db.repositories.audit import AuditEntry, record
from mera.infrastructure.db.session import (
    channel_transaction,
    global_transaction,
    user_transaction,
    workspace_transaction,
)

log = structlog.get_logger(__name__)

CHANNEL: Final = "telegram"  # the default; a runtime serves its channel's type
PAIRING_TTL: Final = timedelta(minutes=10)
PAIRING_LOCK: Final = timedelta(hours=1)
PAIRING_MAX_FAILURES: Final = 5
CODE_ALPHABET: Final = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
EDIT_PROMPT_TTL: Final = timedelta(minutes=30)
TOKEN_TTL: Final = timedelta(hours=24)
GENERIC_UNPAIRED: Final = (
    "This chat isn't paired with Mera. In the web app open Approval Channels, generate a "
    "pairing code, and send it here with /pair CODE."
)
HELP: Final = (
    "<b>Mera approvals</b>\n"
    "/queue · what is waiting, by group\n"
    "/today · today's schedule\n"
    "/stats · the last 7 days\n"
    "/groups · /accounts · your workspace\n"
    "/workspaces · /switch NAME · pick a workspace\n"
    "/pause [group|account] · hold publishing\n"
    "/kill [group|account] · emergency stop (/kill all: every workspace)\n"
    "/resume · resuming needs an Admin in the web app\n"
    "/pin NUMBER · confirm an approval that needs your PIN\n"
    "/whoami · who this chat is"
)


# ---------------------------------------------------------------------------- ids


def short(u: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(u.bytes).rstrip(b"=").decode()


def unshort(s: str) -> uuid.UUID:
    return uuid.UUID(bytes=base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))


def normalize_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum()).removeprefix("MERA")


def code_hash(code: str) -> str:
    return hashlib.sha256(("mera-pair:" + normalize_code(code)).encode()).hexdigest()


def new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return f"MERA-{raw[:4]}-{raw[4:]}"


@dataclass(frozen=True, slots=True)
class Recipient:
    user: User
    binding: ApproverBinding


# ======================================================================== runtime


PIN_WAIT = timedelta(minutes=3)
NL = chr(10)


class BotRuntime:
    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        channel: ApprovalChannel,
        approvals_svc: ApprovalService,
        *,
        public_url: str,
        posts: Any | None = None,
        governance: Any | None = None,
        sandbox_capture: bool = False,
        security: Any | None = None,
        media: Any | None = None,
        inbox: Any | None = None,
        registry: Any | None = None,
        mfa_enforced: bool = False,
    ) -> None:
        self._sm = sm
        self._mfa_enforced = mfa_enforced
        self._registry = registry  # McpRegistryService: tool-call cards (P2-M9.7)
        self._media = media  # MediaGenerationService: "🎨 Regenerate image" (P2-M5.6)
        self._inbox = inbox  # InboxService: reply cards (P2-M6.6)
        # The channel this runtime serves (telegram, whatsapp): bindings, tokens, messages
        # and pairing are all keyed by it (P2-M4).
        self._ct: str = getattr(channel, "channel_type", CHANNEL)
        self._security_svc = security
        self._ch = channel
        self._ap = approvals_svc
        self._posts = posts
        self._gov = governance
        self._public = public_url.rstrip("/")
        self._capture = sandbox_capture

    # ------------------------------------------------------------------ outbound

    async def dispatch(self, kind: str, workspace_id: uuid.UUID, subject_id: uuid.UUID) -> int:
        if not await self._enabled(workspace_id):
            return 0
        if kind == "card":
            return await self.send_cards(workspace_id, subject_id)
        if kind == "refresh":
            return await self.refresh_cards(workspace_id, subject_id)
        if kind == "reminder":
            return await self.send_followups(workspace_id, subject_id, "reminder")
        if kind == "escalation":
            return await self.send_followups(workspace_id, subject_id, "escalation")
        if kind == "publish_report":
            return await self.publish_report(workspace_id, subject_id)
        if kind == "health":
            return await self.health_alert(workspace_id, subject_id)
        if kind == "reply":
            return await self.send_reply_cards(workspace_id, subject_id)
        if kind == "mcp_call":
            return await self.send_tool_call_cards(workspace_id, subject_id)
        if kind == "autopilot":
            return await self.send_autopilot_notice(workspace_id, subject_id)
        log.warning("bot.unknown_dispatch", kind=kind)
        return 0

    async def send_reply_cards(self, workspace_id: uuid.UUID, reply_id: uuid.UUID) -> int:
        """A reply waiting for approval (P2-M6.6), to each paired person who may approve
        replies for that account. The buttons carry the reply's hash prefix, so a reply
        edited after the card went out cannot be approved from it."""
        if self._inbox is None:
            return 0
        from mera.infrastructure.db.models import Comment, Reply

        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            r = await s.get(Reply, reply_id, with_for_update=True)
            c = await s.get(Comment, r.comment_id) if r else None
            if r is None or c is None or r.state != "pending_approval":
                return 0
            # The card's buttons carry a random nonce, not a hash prefix (release review #6):
            # a tap counts only for the card last sent, and only while the reply is still
            # exactly what it showed. Same content, same nonce, so a resend keeps old cards.
            if r.card_nonce is None or r.card_hash != r.content_hash:
                r.card_nonce, r.card_hash = secrets.token_urlsafe(8), r.content_hash
            account = await s.get(SocialAccount, r.social_account_id)
            ws = await s.get(Workspace, workspace_id)
            people = await self._recipients(s, workspace_id)
            text = render.reply_card(
                workspace=ws.name if ws else "",
                platform=c.platform,
                account=account.nickname if account else "",
                handle=account.handle if account else "",
                author=c.author_handle,
                comment=c.body,
                reply=r.body,
            )
            ref = f"{short(workspace_id)}:{short(r.id)}:{r.card_nonce}"
            web = self._web(ws, "inbox")
            complete = render.reply_fits(r.body)
        sent = 0
        for p in people:
            if not await self._inbox.can_approve(workspace_id, p.user.id, r.social_account_id):
                continue
            # A reply the card cannot show in full is approved in the web; rejecting
            # from here is always safe (release review #5).
            first = [Button("✅ Send reply", f"y:{ref}")] if complete else []
            rows = [[*first, Button("❌ Reject", f"n:{ref}")]]
            if web:
                rows.append([Button("🌐 Open in web", url=web)])
            await self._reply(p.binding.external_id, text, rows)
            sent += 1
        return sent

    async def send_tool_call_cards(self, workspace_id: uuid.UUID, call_id: uuid.UUID) -> int:
        """An external write tool waits for a person (P2-M9.7): the card shows the server,
        the tool, its risk and the **exact arguments** that will be sent."""
        if self._registry is None:
            return 0
        from mera.infrastructure.db.models import McpServer, McpTool, McpToolCall

        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            call = await s.get(McpToolCall, call_id)
            if call is None or call.state != "pending_approval":
                return 0
            srv = await s.get(McpServer, call.server_id)
            tool = await s.get(McpTool, call.tool_id)
            ws = await s.get(Workspace, workspace_id)
            people = await self._recipients(s, workspace_id)
            text = render.tool_call_card(
                workspace=ws.name if ws else "",
                server=srv.name if srv else "",
                tool=call.tool_name,
                risk=tool.risk_class if tool else "write",
                agent=call.agent_key,
                arguments=call.arguments,
            )
            ref = f"{short(workspace_id)}:{short(call.id)}:{call.content_hash.hex()[:10]}"
            web = self._web(ws, "mcp")
            complete = render.tool_args_fit(call.arguments)
        sent = 0
        for p in people:
            first = [Button("✅ Allow", f"m:{ref}")] if complete else []
            rows = [[*first, Button("❌ Deny", f"z:{ref}")]]
            if web:
                rows.append([Button("🌐 Open in web", url=web)])
            await self._reply(p.binding.external_id, text, rows)
            sent += 1
        return sent

    async def _tool_call_decision(
        self, cb_id: str, ws: uuid.UUID, kind: str, parts: list[str], user: User
    ) -> str:
        if self._registry is None:
            await self._ch.answer(cb_id)
            return "ignored"
        try:
            call_id = unshort(parts[2])
        except (IndexError, ValueError):
            await self._ch.answer(cb_id)
            return "ignored"
        out = await self._registry.decide_from_channel(
            ws, call_id, user.id, parts[3], kind == "m", self._ct
        )
        text = {
            "allowed": "✅ Allowed. The tool runs with exactly these arguments.",
            "denied": "❌ Denied. The agent continues without it.",
            "forbidden": "You can't decide tool calls in this workspace.",
            "stale": "This call changed after the card was sent.",
            "expired": "This call expired. Nothing was run.",
            "closed": "This call was already decided.",
            "missing": "This call no longer exists.",
        }.get(out, "Done.")
        await self._ch.answer(cb_id, text, alert=out in ("forbidden", "stale", "expired"))
        return f"tool_call_{out}"

    async def _reply_decision(
        self, cb_id: str, ws: uuid.UUID, kind: str, parts: list[str], user: User
    ) -> str:
        if self._inbox is None:
            await self._ch.answer(cb_id)
            return "ignored"
        try:
            reply_id = unshort(parts[2])
        except (IndexError, ValueError):
            await self._ch.answer(cb_id)
            return "ignored"
        if kind == "y":
            out = await self._inbox.approve_from_channel(ws, reply_id, user.id, parts[3], self._ct)
        else:
            out = await self._inbox.reject_from_channel(ws, reply_id, user.id, parts[3], self._ct)
        text = {
            "approved": "✅ Approved. Sending it as the account that was commented on.",
            "rejected": "❌ Rejected. It will not be sent.",
            "forbidden": "You can't approve replies for this account.",
            "stale": "This reply changed after the card was sent. Open it in the web.",
            "closed": "This reply was already decided.",
            "missing": "This reply no longer exists.",
        }.get(out, "Done.")
        await self._ch.answer(cb_id, text, alert=out in ("forbidden", "stale"))
        return f"reply_{out}"

    async def _enabled(self, workspace_id: uuid.UUID) -> bool:
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            row = (
                await s.execute(
                    select(ApprovalChannelRow).where(ApprovalChannelRow.channel_type == self._ct)
                )
            ).scalar_one_or_none()
            return row is None or row.status == "active"

    async def _recipients(self, s: AsyncSession, workspace_id: uuid.UUID) -> list[Recipient]:
        rows = (
            await s.execute(
                select(User, ApproverBinding)
                .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
                .join(ApproverBinding, ApproverBinding.user_id == User.id)
                .where(
                    WorkspaceMember.workspace_id == workspace_id,
                    ApproverBinding.channel_type == self._ct,
                    ApproverBinding.status == "active",
                    # Out of office (P2-M2.8): no new cards while away.
                    or_(
                        WorkspaceMember.away_until.is_(None),
                        WorkspaceMember.away_until <= func.now(),
                    ),
                )
            )
        ).all()
        return [Recipient(u, b) for u, b in rows]

    def _who(self, user: User) -> AuthenticatedUser:
        return acting_user(user, mfa_enforced=self._mfa_enforced)

    def _web(self, ws: Workspace | None, path: str = "approvals") -> str | None:
        if ws is None or not self._public.startswith("https://"):
            # Telegram rejects non-HTTPS URL buttons; local stacks show no web button.
            return None
        return f"{self._public}/w/{ws.slug}/{path}"

    async def _view_for(
        self, s: AsyncSession, req: ApprovalRequest, user: User
    ) -> tuple[ApprovalRequestView, dict[str, Any]] | None:
        try:
            principal = await load_user_principal(s, self._who(user), req.workspace_id)
        except ApplicationError:
            return None
        view = await self._ap._view(s, principal, req)
        if not view.variants:
            return None
        return view, await card_extras(s, view)

    async def _mint(
        self,
        s: AsyncSession,
        req: ApprovalRequest,
        user_id: uuid.UUID,
        action: str,
        scope: list[uuid.UUID] | None,
    ) -> str:
        return await self._ap.mint_token(
            s, req, approver_id=user_id, action=action, scope=scope, channel=self._ct, ttl=TOKEN_TTL
        )

    async def _summary(
        self, s: AsyncSession, req: ApprovalRequest, rec: Recipient, cm: ChannelMessage
    ) -> OutboundMessage | None:
        got = await self._view_for(s, req, rec.user)
        if got is None:
            return None
        view, payload = got
        ref = f"{short(req.workspace_id)}:{short(cm.id)}"
        approve_all = reject_all = None
        await self._revoke_card_tokens(s, cm)
        tokens: dict[str, Any] = {}
        if is_open(req.state):
            pending = [v for v in view.variants if v.state == "pending"]
            all_rows = await approvals.variant_states(s, req.id)
            sees_all = len(view.variants) == len(all_rows)
            if pending and sees_all and all(v.can_decide for v in pending):
                a = await self._mint(s, req, rec.user.id, "approve", None)
                r = await self._mint(s, req, rec.user.id, "reject", None)
                tokens = {"approve_all": a, "reject_all": r}
                approve_all = f"a:{short(req.workspace_id)}:{a}"
                reject_all = f"a:{short(req.workspace_id)}:{r}"
        revising = await self._revising(s, req)
        msg = render.summary_card(
            payload,
            now=datetime.now(UTC),
            approve_all=approve_all,
            per_account=[f"v:{ref}:{i}" for i in range(len(view.variants))],
            message_ref=ref,
            web_url=self._web(await s.get(Workspace, req.workspace_id)),
            revising=revising,
            constraints=self._ch.constraints,
        )
        if reject_all:  # point the summary's Reject all at its token
            msg = OutboundMessage(
                text=msg.text,
                rows=[
                    [Button(b.label, reject_all) if b.data == f"R:{ref}" else b for b in row]
                    for row in msg.rows
                ],
            )
        else:
            msg = OutboundMessage(
                text=msg.text,
                rows=[[b for b in row if b.data != f"R:{ref}"] for row in msg.rows if row],
            )
            msg = OutboundMessage(text=msg.text, rows=[r for r in msg.rows if r])
        cm.view = {
            "mode": "summary",
            "variants": [str(v.post_variant_id) for v in view.variants],
            "tokens": tokens,
            "can_decide": any(v.can_decide for v in view.variants),
        }
        return msg

    async def _revising(self, s: AsyncSession, req: ApprovalRequest) -> set[str]:
        ps = await s.get(PostSet, req.post_set_id)
        if ps is None or ps.generation_status not in ("queued", "running"):
            return set()
        last = (
            await s.execute(
                select(ApprovalDecision)
                .where(
                    ApprovalDecision.approval_request_id == req.id,
                    ApprovalDecision.action == "edit_requested",
                )
                .order_by(ApprovalDecision.decided_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return {str(v) for v in last.variant_scope} if last else set()

    async def _revoke_card_tokens(self, s: AsyncSession, cm: ChannelMessage) -> None:
        jtis = [j for j in (cm.view or {}).get("tokens", {}).values() if isinstance(j, str)]
        detail = (cm.view or {}).get("detail") or {}
        jtis += [j for j in (detail.get("approve"), detail.get("reject")) if j]
        if not jtis:
            return
        now = datetime.now(UTC)
        for jti in jtis:
            tok = await s.get(ApprovalToken, jti, with_for_update=True)
            if tok is not None and tok.used_at is None and tok.revoked_at is None:
                tok.revoked_at = now

    async def send_autopilot_notice(self, workspace_id: uuid.UUID, request_id: uuid.UUID) -> int:
        """P3-M3: what autopilot approved, when each account goes out, and a Stop button
        per account while it is still queued. Sent to everyone who could have approved."""
        from mera.infrastructure.db.models import AutopilotAuthorization, ScheduledPost

        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            req = await s.get(ApprovalRequest, request_id)
            ws = await s.get(Workspace, workspace_id)
            if req is None or ws is None:
                return 0
            auths = list(
                (
                    await s.execute(
                        select(AutopilotAuthorization).where(
                            AutopilotAuthorization.approval_request_id == request_id
                        )
                    )
                ).scalars()
            )
            if not auths:
                return 0
            ps = await s.get(PostSet, req.post_set_id)
            lines = [
                f"🤖 <b>Autopilot approved</b> · {render.esc(ws.name)}",
                f"{render.esc((ps.title if ps else None) or 'Post')}",
                "",
            ]
            rows: list[list[Button]] = []
            for a in auths:
                acc = await s.get(SocialAccount, a.social_account_id)
                sp = (
                    await s.execute(
                        select(ScheduledPost).where(
                            ScheduledPost.approval_request_id == request_id,
                            ScheduledPost.post_variant_id == a.post_variant_id,
                        )
                    )
                ).scalar_one_or_none()
                name = acc.nickname if acc else "account"
                when = sp.scheduled_for.strftime("%H:%M UTC") if sp else "?"
                lines.append(f"• {render.esc(name)} · {a.mode} · goes out {when}")
                if sp is not None and sp.state == "queued":
                    rows.append([Button(f"⏹ Stop {name[:20]}", f"u:{short(ws.id)}:{short(sp.id)}")])
            people = await self._recipients(s, workspace_id)
        for p in people:
            await self._reply(p.binding.external_id, NL.join(lines), rows[:8])
        return len(people)

    async def _stop_autopilot(self, cb_id: str, ws: uuid.UUID, sp_id: uuid.UUID, user: User) -> str:
        """Stop an unattended post before it goes out: the slot is cancelled and its
        authorization revoked. Anyone who could approve the account may do it."""
        from mera.domain.policies import rbac
        from mera.domain.policies.rbac import Permission
        from mera.infrastructure.db.models import AutopilotAuthorization, ScheduledPost

        async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
            sp = await s.get(ScheduledPost, sp_id, with_for_update=True)
            if sp is None:
                await self._ch.answer(cb_id, "Not found.")
                return "autopilot_missing"
            principal = await load_user_principal(s, self._who(user), ws)
            try:
                rbac.require(
                    principal, Permission.APPROVE, ws, social_account_id=sp.social_account_id
                )
            except Exception:
                await self._ch.answer(cb_id, "You can't stop posts for this account.", alert=True)
                return "autopilot_forbidden"
            if sp.state != "queued":
                await self._ch.answer(cb_id, f"Too late: it is {sp.state}.", alert=True)
                return "autopilot_too_late"
            sp.state, sp.next_attempt_at = "cancelled", None
            auth = (
                await s.execute(
                    select(AutopilotAuthorization)
                    .where(
                        AutopilotAuthorization.approval_request_id == sp.approval_request_id,
                        AutopilotAuthorization.post_variant_id == sp.post_variant_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if auth is not None and auth.revoked_at is None:
                auth.revoked_at = datetime.now(UTC)
                auth.revoked_reason = f"stopped by {user.display_name}"
            await record(
                s,
                AuditEntry(
                    action="autopilot.stopped",
                    actor_type="user",
                    actor_id=user.id,
                    actor_label=user.display_name,
                    surface=self._ct,
                    workspace_id=ws,
                    subject_type="scheduled_post",
                    subject_id=sp.id,
                    social_account_id=sp.social_account_id,
                ),
            )
        await self._ch.answer(cb_id, "⏹ Stopped. It will not be posted.")
        return "autopilot_stopped"

    async def send_digest(self, workspace_id: uuid.UUID, user_id: uuid.UUID) -> int:
        """Today's queue for one person (P3-M4.1, docs/07 §3.4): what they may decide,
        grouped by account group, sets and posts counted separately, a Review button per
        set. Nothing waiting, or no chat on this channel: nothing sent."""
        built = await self.digest(workspace_id, user_id)
        if built is None or not built[2]:
            return 0
        text, buttons, chats = built
        for chat in chats:
            await self._reply(chat, text, buttons)
        return len(chats)

    async def digest(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[str, list[list[Button]], list[str]] | None:
        """(text, buttons, this channel's chats for the person), or None when nothing waits."""
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            ws = await s.get(Workspace, workspace_id)
            user = await s.get(User, user_id)
            if ws is None or user is None:
                return None
            bindings = [
                r.binding for r in await self._recipients(s, workspace_id) if r.user.id == user_id
            ]
            principal = await load_user_principal(s, self._who(user), workspace_id)
            reqs = list(
                (
                    await s.execute(
                        select(ApprovalRequest)
                        .where(ApprovalRequest.state.in_(["pending", "awaiting_edit"]))
                        .order_by(ApprovalRequest.created_at)
                    )
                ).scalars()
            )
            groups: dict[str, list[str]] = {}
            rows: list[list[Button]] = []
            sets = posts = 0
            for r in reqs:
                view = await self._ap._view(s, principal, r)
                if not view.variants:
                    continue
                rg = await s.get(AccountGroup, r.account_group_id) if r.account_group_id else None
                sets += 1
                posts += len(view.variants)
                groups.setdefault(rg.name if rg else "Direct", []).append(
                    f" {sets}. {render.esc(view.title or 'Post')} · {len(view.variants)} posts"
                )
                rows.append(
                    [
                        Button(
                            f"👀 Review {sets}: {(view.title or 'Post')[:20]}",
                            f"q:{short(ws.id)}:{short(r.id)}",
                        )
                    ]
                )
            if not sets:
                return None
            lines = [
                f"📬 <b>{render.esc(ws.name)}</b> · today's queue · "
                f"{sets} set{'s' if sets != 1 else ''}, {posts} post{'s' if posts != 1 else ''}"
            ]
            for name, items in groups.items():
                lines += ["", f"<b>{render.esc(name)}</b>", *items]
            web = self._web(ws)
            buttons = rows[:8] + ([[Button("🌐 Open web app", url=web)]] if web else [])
            chats = [b.external_id for b in bindings]
        return NL.join(lines), buttons, chats

    async def send_cards(self, workspace_id: uuid.UUID, request_id: uuid.UUID) -> int:
        sent = 0
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            req = await s.get(ApprovalRequest, request_id)
            if req is None or not is_open(req.state):
                return 0
            # A member on digest only (P3-M4.1) gets today's list instead of each card.
            digest_only = set(
                (
                    await s.execute(
                        select(WorkspaceMember.user_id).where(
                            WorkspaceMember.digest_mode == "digest"
                        )
                    )
                ).scalars()
            )
            recipients = [
                r for r in await self._recipients(s, workspace_id) if r.user.id not in digest_only
            ]
            # Per channel: someone paired on Telegram and WhatsApp gets the card on both.
            existing = {
                m.user_id
                for m in (
                    await s.execute(
                        select(ChannelMessage).where(
                            ChannelMessage.approval_request_id == req.id,
                            ChannelMessage.message_kind == "card",
                            ChannelMessage.channel_type == self._ct,
                        )
                    )
                ).scalars()
            }
            plans: list[tuple[Recipient, ChannelMessage, OutboundMessage]] = []
            for rec in recipients:
                if rec.user.id in existing:
                    continue
                cm = ChannelMessage(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    approval_request_id=req.id,
                    approver_binding_id=rec.binding.id,
                    user_id=rec.user.id,
                    channel_type=self._ct,
                    external_chat_id=rec.binding.external_id,
                    message_kind="card",
                )
                s.add(cm)
                await s.flush()
                msg = await self._summary(s, req, rec, cm)
                if msg is None or not (cm.view or {}).get("can_decide"):
                    # Nothing here this person may decide: no card (a scoped approver gets
                    # only their own groups, docs/11 M7 acceptance).
                    await self._revoke_card_tokens(s, cm)
                    await s.delete(cm)
                    continue
                plans.append((rec, cm, msg))
        for rec, cm, msg in plans:
            await self._deliver(workspace_id, cm.id, rec.binding.external_id, msg)
            sent += 1
        return sent

    async def _deliver(
        self,
        workspace_id: uuid.UUID,
        cm_id: uuid.UUID,
        chat_id: str,
        msg: OutboundMessage,
        *,
        edit_of: str | None = None,
    ) -> None:
        state, mid = "delivered", edit_of
        try:
            if edit_of:
                await self._ch.edit(chat_id, edit_of, msg)
                state = "updated"
            else:
                mid = await self._ch.send(chat_id, msg)
        except ChannelError as exc:
            log.warning("bot.deliver_failed", error=str(exc)[:120])
            state = "failed"
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            cm = await s.get(ChannelMessage, cm_id)
            if cm is not None:
                cm.render_state = state
                cm.external_message_id = mid
                cm.sent_at = cm.sent_at or datetime.now(UTC)
                cm.updated_at = datetime.now(UTC)
            if self._capture:
                s.add(
                    SandboxCapture(
                        workspace_id=workspace_id,
                        kind="channel_message",
                        target=f"{self._ct}:{chat_id}",
                        payload={
                            "op": "edit" if edit_of else "send",
                            "message_id": mid,
                            "text": msg.text,
                            "buttons": [
                                [{"label": b.label, "data": b.data, "url": b.url} for b in row]
                                for row in msg.rows
                            ],
                            "force_reply": msg.force_reply,
                        },
                    )
                )

    async def refresh_cards(self, workspace_id: uuid.UUID, request_id: uuid.UUID) -> int:
        plans: list[tuple[uuid.UUID, str, str, OutboundMessage]] = []
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            req = await s.get(ApprovalRequest, request_id)
            if req is None:
                return 0
            cards = (
                await s.execute(
                    select(ChannelMessage).where(
                        ChannelMessage.approval_request_id == req.id,
                        ChannelMessage.message_kind == "card",
                        ChannelMessage.channel_type == self._ct,
                        ChannelMessage.external_message_id.is_not(None),
                    )
                )
            ).scalars()
            for cm in cards:
                user = await s.get(User, cm.user_id) if cm.user_id else None
                binding = (
                    await s.get(ApproverBinding, cm.approver_binding_id)
                    if cm.approver_binding_id
                    else None
                )
                if user is None or binding is None:
                    continue
                msg = await self._summary(s, req, Recipient(user, binding), cm)
                if msg is None:
                    continue
                if not is_open(req.state):
                    cm.render_state = "closed"
                plans.append((cm.id, cm.external_chat_id, str(cm.external_message_id), msg))
        for cm_id, chat, mid, msg in plans:
            await self._deliver(workspace_id, cm_id, chat, msg, edit_of=mid)
        # A request created while nobody had cards yet (or a new approver paired): send.
        if not plans:
            return await self.send_cards(workspace_id, request_id)
        return len(plans)

    async def resend_cards(self, chat_id: str) -> int:
        """Send this chat's open cards again as new messages (P2-M4): on WhatsApp a template
        opened the window, and the person asked for the full card."""
        async with channel_transaction(self._sm, self._ct, chat_id) as s:
            bindings = list(
                (
                    await s.execute(
                        select(ApproverBinding).where(
                            ApproverBinding.channel_type == self._ct,
                            ApproverBinding.external_id == chat_id,
                            ApproverBinding.status == "active",
                        )
                    )
                ).scalars()
            )
        sent = 0
        for binding in bindings:
            async with user_transaction(self._sm, binding.user_id) as s:
                user = await s.get(User, binding.user_id)
                workspaces = list(
                    (
                        await s.execute(
                            select(WorkspaceMember.workspace_id).where(
                                WorkspaceMember.user_id == binding.user_id
                            )
                        )
                    ).scalars()
                )
            if user is None:
                continue
            for ws in workspaces:
                plans: list[tuple[uuid.UUID, OutboundMessage]] = []
                async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
                    cards = (
                        await s.execute(
                            select(ChannelMessage).where(
                                ChannelMessage.approver_binding_id == binding.id,
                                ChannelMessage.message_kind == "card",
                            )
                        )
                    ).scalars()
                    for cm in cards:
                        req = (
                            await s.get(ApprovalRequest, cm.approval_request_id)
                            if cm.approval_request_id
                            else None
                        )
                        if req is None or not is_open(req.state):
                            continue
                        msg = await self._summary(s, req, Recipient(user, binding), cm)
                        if msg is not None:
                            plans.append((cm.id, msg))
                for cm_id, msg in plans:
                    await self._deliver(ws, cm_id, chat_id, msg)
                    sent += 1
        return sent

    async def send_followups(
        self, workspace_id: uuid.UUID, request_id: uuid.UUID, kind: str
    ) -> int:
        plans: list[tuple[str, OutboundMessage, Recipient]] = []
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            req = await s.get(ApprovalRequest, request_id)
            if req is None or not is_open(req.state):
                return 0
            cards = list(
                (
                    await s.execute(
                        select(ChannelMessage).where(
                            ChannelMessage.approval_request_id == req.id,
                            ChannelMessage.message_kind == "card",
                            ChannelMessage.channel_type == self._ct,
                        )
                    )
                ).scalars()
            )
            named = set((req.policy_snapshot or {}).get("escalate_to") or [])
            if kind == "escalation" and named:
                # Escalate to the people the policy names (P2-M2.8), not every card holder.
                cards = [c for c in cards if str(c.user_id) in named]
            for cm in cards:
                user = await s.get(User, cm.user_id) if cm.user_id else None
                binding = (
                    await s.get(ApproverBinding, cm.approver_binding_id)
                    if cm.approver_binding_id
                    else None
                )
                if user is None or binding is None:
                    continue
                got = await self._view_for(s, req, user)
                if got is None:
                    continue
                view, payload = got
                if kind == "reminder":
                    approve = None
                    if all(v.can_decide for v in view.variants if v.state == "pending"):
                        jti = await self._mint(s, req, user.id, "approve", None)
                        approve = f"a:{short(workspace_id)}:{jti}"
                    msg = render.reminder(
                        payload,
                        now=datetime.now(UTC),
                        approve_all=approve,
                        web_url=self._web(await s.get(Workspace, workspace_id)),
                    )
                else:
                    msg = render.escalation(
                        payload, level=req.escalation_level, now=datetime.now(UTC)
                    )
                msg = OutboundMessage(
                    text=msg.text, rows=msg.rows, reply_to_message_id=cm.external_message_id
                )
                plans.append((kind, msg, Recipient(user, binding)))
        n = 0
        for k, msg, rec in plans:
            await self._send_new(workspace_id, rec, msg, k, request_id)
            n += 1
        return n

    async def _send_new(
        self,
        workspace_id: uuid.UUID,
        rec: Recipient,
        msg: OutboundMessage,
        kind: str,
        request_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            cm = ChannelMessage(
                id=uuid7(),
                workspace_id=workspace_id,
                approval_request_id=request_id,
                approver_binding_id=rec.binding.id,
                user_id=rec.user.id,
                channel_type=self._ct,
                external_chat_id=rec.binding.external_id,
                message_kind=kind,
            )
            s.add(cm)
        await self._deliver(workspace_id, cm.id, rec.binding.external_id, msg)
        return cm.id

    async def publish_report(self, workspace_id: uuid.UUID, post_set_id: uuid.UUID) -> int:
        plans: list[tuple[Recipient, OutboundMessage]] = []
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            ps = await s.get(PostSet, post_set_id)
            ws = await s.get(Workspace, workspace_id)
            if ps is None or ws is None:
                return 0
            slots = list(
                (
                    await s.execute(
                        select(ScheduledPost)
                        .where(ScheduledPost.post_set_id == ps.id)
                        .order_by(
                            ScheduledPost.published_at.asc().nulls_last(),
                            ScheduledPost.scheduled_for,
                        )
                    )
                ).scalars()
            )
            rows = []
            group_name = None
            for sp in slots:
                if sp.state == "cancelled":
                    continue
                acct = await s.get(SocialAccount, sp.social_account_id)
                pp = (
                    await s.execute(
                        select(PublishedPost).where(PublishedPost.scheduled_post_id == sp.id)
                    )
                ).scalar_one_or_none()
                if sp.account_group_id and group_name is None:
                    g = await s.get(AccountGroup, sp.account_group_id)
                    group_name = g.name if g else None
                rows.append(
                    {
                        "nickname": acct.nickname if acct else "account",
                        "state": sp.state,
                        "published_at": pp.published_at.isoformat() if pp else None,
                        "permalink": pp.permalink if pp else None,
                        "error": sp.last_error,
                        "attempts": sp.attempt_count,
                        "max_attempts": 5,
                    }
                )
            interested = set(
                (
                    await s.execute(
                        select(ApprovalDecision.approver_user_id)
                        .join(
                            ApprovalRequest,
                            ApprovalRequest.id == ApprovalDecision.approval_request_id,
                        )
                        .where(ApprovalRequest.post_set_id == ps.id)
                    )
                ).scalars()
            ) | {ps.created_by_user_id}
            msg = render.publish_report(
                title=ps.title,
                group=group_name,
                rows=rows,
                tz=ws.timezone,
                web_url=self._web(ws, "calendar"),
            )
            for rec in await self._recipients(s, workspace_id):
                if rec.user.id in interested:
                    plans.append((rec, msg))
        for rec, msg in plans:
            await self._send_new(workspace_id, rec, msg, "publish_report")
        return len(plans)

    async def health_alert(self, workspace_id: uuid.UUID, account_id: uuid.UUID) -> int:
        plans: list[tuple[Recipient, OutboundMessage]] = []
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            acct = await s.get(SocialAccount, account_id)
            ws = await s.get(Workspace, workspace_id)
            if acct is None or ws is None:
                return 0
            held = await s.scalar(
                select(func.count())
                .select_from(ScheduledPost)
                .where(
                    ScheduledPost.social_account_id == acct.id,
                    ScheduledPost.state.in_(["queued", "failed"]),
                )
            )
            msg = render.health_alert(
                nickname=acct.nickname,
                reason=acct.status_reason,
                held=int(held or 0),
                web_url=self._web(ws, "accounts"),
            )
            groups = list(
                (
                    await s.execute(
                        select(AccountGroupMember.account_group_id).where(
                            AccountGroupMember.social_account_id == acct.id
                        )
                    )
                ).scalars()
            )
            for rec in await self._recipients(s, workspace_id):
                try:
                    principal = await load_user_principal(s, self._who(rec.user), workspace_id)
                    ok = False
                    for g in [None, *groups]:
                        try:
                            require(
                                principal,
                                Permission.APPROVE,
                                workspace_id,
                                social_account_id=acct.id,
                                account_group_id=g,
                            )
                            ok = True
                            break
                        except PermissionDenied:
                            continue
                except ApplicationError:
                    ok = False
                if ok:
                    plans.append((rec, msg))
        for rec, msg in plans:
            await self._send_new(workspace_id, rec, msg, "health_alert")
        return len(plans)

    # ------------------------------------------------------------------- inbound

    async def handle_update(self, update: dict[str, Any]) -> str:
        uid = str(update.get("update_id", ""))
        if not uid:
            return "ignored"
        async with global_transaction(self._sm) as s:
            inserted = await s.execute(
                insert(ChannelUpdate)
                .values(channel_type=self._ct, update_id=uid)
                .on_conflict_do_nothing()
                .returning(ChannelUpdate.update_id)
            )
            if inserted.scalar_one_or_none() is None:
                return "duplicate"  # at-least-once delivery: processed once (M7.14)
        if "callback_query" in update:
            return await self._callback(update["callback_query"])
        msg = update.get("message")
        if isinstance(msg, dict):
            return await self._message(msg)
        return "ignored"

    async def _binding(self, chat_id: str) -> tuple[ApproverBinding, User] | None:
        async with channel_transaction(self._sm, self._ct, chat_id) as s:
            b = (
                await s.execute(select(ApproverBinding).where(ApproverBinding.status == "active"))
            ).scalar_one_or_none()
            if b is None:
                return None
            u = await s.get(User, b.user_id)
            if u is None or u.deleted_at is not None:
                return None
            return b, u

    async def _reply(
        self, chat_id: str, text: str, rows: list[list[Button]] | None = None, **kw: Any
    ) -> str | None:
        try:
            return await self._ch.send(chat_id, OutboundMessage(text=text, rows=rows or [], **kw))
        except ChannelError:
            return None

    async def _security(
        self, action: str, chat_id: str, workspace_id: uuid.UUID | None, **after: Any
    ) -> None:
        async with (
            workspace_transaction(self._sm, WorkspaceContext(workspace_id))
            if workspace_id
            else global_transaction(self._sm)
        ) as s:
            await record(
                s,
                AuditEntry(
                    action=action,
                    actor_type="bot",
                    surface="telegram",
                    workspace_id=workspace_id,
                    after={"chat": chat_id, **after},
                ),
            )

    # ---------------------------------------------------------------- messages

    async def _message(self, msg: dict[str, Any]) -> str:
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        if not chat_id or chat.get("type", "private") != "private":
            return "ignored"
        text = str(msg.get("text") or "").strip()
        if text.startswith("/start") or text.startswith("/pair"):
            parts = text.split(maxsplit=1)
            if len(parts) == 2:
                return await self.pair(chat_id, parts[1], label=_label(msg))
            if text.startswith("/start"):
                await self._reply(chat_id, GENERIC_UNPAIRED)
                return "help"
        bound = await self._binding(chat_id)
        if bound is None:
            await self._reply(chat_id, GENERIC_UNPAIRED)
            return "unpaired"
        binding, user = bound
        if text.startswith("/"):
            return await self._command(chat_id, binding, user, text)
        return await self._edit_reply(chat_id, binding, user, msg, text)

    async def pair(self, chat_id: str, code: str, *, label: str | None = None) -> str:
        h = code_hash(code)
        now = datetime.now(UTC)
        async with channel_transaction(self._sm, self._ct, chat_id, pairing_code_hash=h) as s:
            att = await s.get(ChannelPairingAttempt, (self._ct, chat_id), with_for_update=True)
            if att is not None and att.locked_until is not None and att.locked_until > now:
                await self._reply(chat_id, "Too many attempts. Try again in an hour.")
                return "locked"
            row = (
                await s.execute(
                    select(PairingCode).where(PairingCode.code_hash == h).with_for_update()
                )
            ).scalar_one_or_none()
            if (
                row is None
                or row.used_at is not None
                or row.expires_at < now
                or row.channel_type != self._ct
            ):
                if att is None:
                    att = ChannelPairingAttempt(
                        channel_type=self._ct, external_id=chat_id, failed_count=0, window_start=now
                    )
                    s.add(att)
                if now - att.window_start > PAIRING_LOCK:
                    att.window_start, att.failed_count = now, 0
                att.failed_count += 1
                if att.failed_count >= PAIRING_MAX_FAILURES:
                    att.locked_until = now + PAIRING_LOCK
                failed = True
            else:
                failed = False
                row.used_at = now
                for old in (
                    await s.execute(
                        select(ApproverBinding).where(ApproverBinding.status == "active")
                    )
                ).scalars():
                    old.status, old.revoked_at = "revoked", now  # this chat moves to its new owner
                await s.flush()
                binding = ApproverBinding(
                    id=uuid7(),
                    user_id=row.user_id,
                    channel_type=self._ct,
                    external_id=chat_id,
                    external_label=label,
                    status="active",
                    verified_at=now,
                )
                s.add(binding)
                if att is not None:
                    att.failed_count, att.locked_until = 0, None
                user_id = row.user_id
        if failed:
            await self._security("security.pairing_failed", chat_id, None)
            await self._reply(
                chat_id, "That code is not valid or has expired. Generate a new one in the web app."
            )
            return "invalid"
        async with user_transaction(self._sm, user_id) as s:
            user = await s.get(User, user_id)
            names = list(
                (
                    await s.execute(
                        select(Workspace.name)
                        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                        .where(WorkspaceMember.user_id == user_id, Workspace.deleted_at.is_(None))
                    )
                ).scalars()
            )
        async with global_transaction(self._sm) as s:
            await record(
                s,
                AuditEntry(
                    action="approver_binding.paired",
                    actor_type="user",
                    actor_id=user_id,
                    surface="telegram",
                    after={"chat": chat_id, "label": label},
                ),
            )
        assert user is not None
        await self._reply(
            chat_id,
            f"✅ Paired with Mera as <b>{render.esc(user.email)}</b> "
            f"({render.esc(', '.join(names) or 'no workspaces yet')}).\n"
            "You will receive approval requests here. /help lists commands.",
        )
        # Anything already waiting for this person arrives now, not on the next request.
        for ws, _role in await self._workspaces(user):
            try:
                if not await self._enabled(ws.id):
                    continue
                async with workspace_transaction(self._sm, WorkspaceContext(ws.id)) as s:
                    waiting = list(
                        (
                            await s.execute(
                                select(ApprovalRequest.id).where(
                                    ApprovalRequest.state.in_(["pending", "awaiting_edit"])
                                )
                            )
                        ).scalars()
                    )
                for rid in waiting:
                    await self.send_cards(ws.id, rid)
            except ApplicationError:
                continue
        return "paired"

    # ------------------------------------------------------------------ callbacks

    async def _callback(self, cq: dict[str, Any]) -> str:
        cb_id = str(cq.get("id", ""))
        data = str(cq.get("data") or "")
        message = cq.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        bound = await self._binding(chat_id) if chat_id else None
        parts = data.split(":")
        kind = parts[0] if parts else ""
        if bound is None:
            await self._ch.answer(cb_id, "This chat isn't paired with Mera.", alert=True)
            if kind == "a":
                ws = _ws_from(parts)
                await self._security(
                    "security.unpaired_tap", chat_id, None, workspace=str(ws) if ws else None
                )
            return "unpaired"
        binding, user = bound
        try:
            ws = unshort(parts[1])
        except (IndexError, ValueError):
            await self._ch.answer(cb_id)
            return "ignored"
        if kind == "a" and len(parts) == 3:
            return await self._decide(cb_id, chat_id, ws, parts[2], binding, user)
        if kind == "q" and len(parts) == 3:
            return await self._review(cb_id, ws, unshort(parts[2]), binding, user)
        if kind == "K":
            return await self._kill_all(cb_id, chat_id, user)
        if kind in ("y", "n") and len(parts) == 4:
            return await self._reply_decision(cb_id, ws, kind, parts, user)
        if kind in ("m", "z") and len(parts) == 4:
            return await self._tool_call_decision(cb_id, ws, kind, parts, user)
        if kind == "u" and len(parts) == 3:
            return await self._stop_autopilot(cb_id, ws, unshort(parts[2]), user)
        try:
            cm_id = unshort(parts[2])
        except (IndexError, ValueError):
            await self._ch.answer(cb_id)
            return "ignored"
        async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
            cm = await s.get(ChannelMessage, cm_id)
            ok = cm is not None and cm.user_id == user.id and cm.external_chat_id == chat_id
        if not ok:
            await self._ch.answer(cb_id, "This card belongs to someone else.", alert=True)
            await self._security("security.foreign_card_tap", chat_id, ws, data=data[:64])
            return "identity_mismatch"
        assert cm is not None
        if kind == "v":
            return await self._detail(cb_id, ws, cm, int(parts[3]), user)
        if kind == "b":
            await self.refresh_one(ws, cm.id)
            await self._ch.answer(cb_id)
            return "back"
        if kind == "g" and len(parts) > 3:
            return await self._regenerate(cb_id, ws, cm, user, parts[3])
        if kind == "e":
            return await self._edit_prompt(
                cb_id, ws, cm, binding, parts[3] if len(parts) > 3 else "all"
            )
        if kind == "E":
            n = len((cm.view or {}).get("variants", []))
            ref = f"{parts[1]}:{parts[2]}"
            await self._reply(
                chat_id,
                f"✏️ Revise all {n} accounts? Each will be rewritten separately in its own voice.",
                [[Button(f"✅ All {n}", f"e:{ref}:all"), Button("✖️ Cancel", "x")]],
            )
            await self._ch.answer(cb_id)
            return "edit_all_confirm"
        if kind == "t":
            ref = f"{parts[1]}:{parts[2]}"
            await self._reply(
                chat_id,
                "🕒 Move this post to…",
                [
                    [Button("+1 hour", f"T:{ref}:1h"), Button("+3 hours", f"T:{ref}:3h")],
                    [Button("Tomorrow 09:00", f"T:{ref}:tm"), Button("✖️ Cancel", "x")],
                ],
            )
            await self._ch.answer(cb_id)
            return "reschedule_menu"
        if kind == "T" and len(parts) == 4:
            return await self._reschedule(cb_id, chat_id, ws, cm, parts[3], user)
        if kind == "c" and len(parts) == 4:
            return await self._confirm_kind(cb_id, chat_id, binding, user, parts[3])
        await self._ch.answer(cb_id)
        return "ignored"

    async def _decide(
        self,
        cb_id: str,
        chat_id: str,
        ws: uuid.UUID,
        jti: str,
        binding: ApproverBinding,
        user: User,
    ) -> str:
        async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
            tok = await s.get(ApprovalToken, jti)
            request_id = tok.approval_request_id if tok else None
            action = tok.action if tok else None
        try:
            state = await self._ap.decide_with_token(
                ws, jti, channel=self._ct, external_id=chat_id, binding_user_id=user.id
            )
        except (ApplicationError, PermissionDenied) as exc:
            code = getattr(exc, "code", "") or "forbidden"
            if code == "second_factor_required":
                return await self._ask_pin(cb_id, chat_id, ws, user, jti)
            if code == "identity_mismatch":
                await self._security("security.identity_mismatch", chat_id, ws, jti=jti[:6])
            elif code == "approval_token_used" and tok is not None and tok.used_at is not None:
                await self._security("security.token_replay", chat_id, ws, jti=jti[:6])
            text = await self._failure_text(ws, request_id, code, getattr(exc, "message", str(exc)))
            await self._ch.answer(
                cb_id, text, alert=code in ("identity_mismatch", "approver_not_authorized")
            )
            if request_id:
                await self.refresh_cards(ws, request_id)
            return code or "error"
        await self._ch.answer(cb_id, "✅ Approved" if action == "approve" else "❌ Rejected")
        if request_id:
            await self.refresh_cards(ws, request_id)
        return f"decided:{state.value}"

    # ------------------------------------------------- second factor by PIN (P2-M2.6)

    async def _ask_pin(self, cb_id: str, chat_id: str, ws: uuid.UUID, user: User, jti: str) -> str:
        # The waiting action belongs to this chat (RLS, migration 0022).
        async with channel_transaction(self._sm, self._ct, chat_id) as s:
            s.add(
                BotSecondFactor(
                    id=uuid7(),
                    user_id=user.id,
                    workspace_id=ws,
                    channel_type=self._ct,
                    chat_id=chat_id,
                    action="company_page_approve",
                    payload={"jti": jti},
                    expires_at=datetime.now(UTC) + PIN_WAIT,
                )
            )
        await self._ch.answer(cb_id, "🔐 PIN needed")
        await self._reply(
            chat_id,
            "Approving a company account needs your approval PIN. Send /pin followed by your "
            "PIN within 3 minutes. No PIN yet? Set one in the web app under Your security.",
        )
        return "pin_required"

    async def _pin(self, chat_id: str, user: User, pin: str) -> str:
        now = datetime.now(UTC)
        async with channel_transaction(self._sm, self._ct, chat_id) as s:
            pending = (
                await s.execute(
                    select(BotSecondFactor)
                    .where(
                        BotSecondFactor.channel_type == self._ct,
                        BotSecondFactor.chat_id == chat_id,
                        BotSecondFactor.user_id == user.id,
                        BotSecondFactor.used_at.is_(None),
                        BotSecondFactor.expires_at > now,
                    )
                    .order_by(BotSecondFactor.created_at.desc())
                    .limit(1)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if pending is not None:
                pending_id, ws, jti = pending.id, pending.workspace_id, pending.payload.get("jti")
        if pending is None or not jti:
            await self._reply(chat_id, "Nothing is waiting for a PIN.")
            return "pin_nothing_waiting"
        if self._security_svc is None:
            await self._reply(chat_id, "PINs are not available here; approve in the web app.")
            return "pin_unavailable"
        try:
            ok = await self._security_svc.pin_matches(user.id, pin, RequestMeta(surface=self._ct))
        except TooManyAttempts:
            await self._reply(chat_id, "Too many wrong PINs. The PIN is locked for 15 minutes.")
            return "pin_locked"
        if not ok:
            await self._reply(chat_id, "That PIN is not right.")
            return "pin_wrong"
        async with channel_transaction(self._sm, self._ct, chat_id) as s:
            row = await s.get(BotSecondFactor, pending_id, with_for_update=True)
            if row is None or row.used_at is not None:
                await self._reply(chat_id, "Nothing is waiting for a PIN.")
                return "pin_nothing_waiting"
            row.used_at = now
        async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
            tok = await s.get(ApprovalToken, jti)
            request_id = tok.approval_request_id if tok else None
        try:
            state = await self._ap.decide_with_token(
                ws,
                jti,
                channel=self._ct,
                external_id=chat_id,
                binding_user_id=user.id,
                pin_verified=True,
            )
        except (ApplicationError, PermissionDenied) as exc:
            code = getattr(exc, "code", "") or "forbidden"
            await self._reply(
                chat_id, await self._failure_text(ws, request_id, code, getattr(exc, "message", ""))
            )
            if request_id:
                await self.refresh_cards(ws, request_id)
            return code
        await self._reply(chat_id, "✅ Approved.")
        if request_id:
            await self.refresh_cards(ws, request_id)
        return f"decided:{state.value}"

    async def _failure_text(
        self, ws: uuid.UUID, request_id: uuid.UUID | None, code: str, fallback: str
    ) -> str:
        if code == "approval_already_decided" and request_id:
            async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
                row = (
                    await s.execute(
                        select(ApprovalDecision, User.display_name)
                        .outerjoin(User, User.id == ApprovalDecision.approver_user_id)
                        .where(
                            ApprovalDecision.approval_request_id == request_id,
                            ApprovalDecision.action.in_(["approve", "reject"]),
                        )
                        .order_by(ApprovalDecision.decided_at.desc())
                        .limit(1)
                    )
                ).first()
                tz = (await s.get(Workspace, ws)).timezone  # type: ignore[union-attr]
            if row:
                d, name = row
                at = d.decided_at.astimezone(zone(tz)).strftime("%H:%M")
                verb = "approved" if d.action == "approve" else "rejected"
                return f"Already {verb} by {name or 'someone'} at {at}."
        return {
            "approval_content_mismatch": (
                "This post changed after the card was sent; see the updated card."
            ),
            "approval_token_used": "This button was already used or replaced.",
            "approval_expired": "This request has expired; nothing will go out.",
            "kill_switch_engaged": "Automation is stopped for this account.",
            "identity_mismatch": "This button belongs to someone else's card.",
            "approver_not_authorized": "You can't approve that account.",
            "already_voted": "You already approved; another approver is needed.",
            "guardrail_blocked": fallback[:180],
        }.get(code, fallback[:180] or "That didn't work.")

    async def refresh_one(self, ws: uuid.UUID, cm_id: uuid.UUID) -> None:
        async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
            cm = await s.get(ChannelMessage, cm_id)
            if cm is None or cm.approval_request_id is None:
                return
            req = await s.get(ApprovalRequest, cm.approval_request_id)
            user = await s.get(User, cm.user_id) if cm.user_id else None
            binding = (
                await s.get(ApproverBinding, cm.approver_binding_id)
                if cm.approver_binding_id
                else None
            )
            if req is None or user is None or binding is None:
                return
            msg = await self._summary(s, req, Recipient(user, binding), cm)
            chat, mid = cm.external_chat_id, cm.external_message_id
        if msg is not None and mid:
            await self._deliver(ws, cm_id, chat, msg, edit_of=mid)

    async def _detail(
        self, cb_id: str, ws: uuid.UUID, cm: ChannelMessage, index: int, user: User
    ) -> str:
        async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
            cm = await s.get(ChannelMessage, cm.id)  # type: ignore[assignment]
            req = (
                await s.get(ApprovalRequest, cm.approval_request_id)
                if cm and cm.approval_request_id
                else None
            )
            if cm is None or req is None:
                await self._ch.answer(cb_id)
                return "ignored"
            got = await self._view_for(s, req, user)
            if got is None or index >= len(got[0].variants):
                await self._ch.answer(cb_id)
                return "ignored"
            view, payload = got
            v = view.variants[index]
            await self._revoke_card_tokens(s, cm)
            approve = reject = None
            if is_open(req.state) and v.state == "pending" and v.can_decide:
                approve = await self._mint(s, req, user.id, "approve", [v.post_variant_id])
                reject = await self._mint(s, req, user.id, "reject", [v.post_variant_id])
            cm.view = {
                **(cm.view or {}),
                "mode": "detail",
                "tokens": {},
                "detail": {"index": index, "approve": approve, "reject": reject},
            }
            ref = f"{short(ws)}:{short(cm.id)}"
            cost = None
            pv = payload["request"]["variants"][index]
            n_media = len(pv.get("media") or [])
            if self._media is not None and n_media:
                from mera.application.services.media_generation_service import aspect_for

                price = self._media.price(n_media, aspect_for(str(pv.get("platform"))))
                cost = f"${price}" if price is not None else None
            msg = render.detail_view(
                payload,
                index,
                approve=f"a:{short(ws)}:{approve}" if approve else None,
                reject=f"a:{short(ws)}:{reject}" if reject else None,
                message_ref=ref,
                regenerate_cost=cost,
            )
            chat, mid = cm.external_chat_id, cm.external_message_id
        await self._deliver(ws, cm.id, chat, msg, edit_of=mid)
        await self._ch.answer(cb_id)
        return "detail"

    async def _regenerate(
        self, cb_id: str, ws: uuid.UUID, cm: ChannelMessage, user: User, which: str
    ) -> str:
        """New images for one account, planned by the Media agent. When they land, the
        account's approval is asked again (the card shows it as revised). Above the cost
        ceiling the web asks for a second factor, so the bot points there."""
        from mera.application.errors import (
            ConflictError,
            InvalidInput,
            NotFoundError,
            ServiceUnavailableError,
        )
        from mera.application.second_factor import SecondFactorRequired

        if self._media is None:
            await self._ch.answer(cb_id, "Image generation is not set up here.")
            return "unavailable"
        visible = [uuid.UUID(v) for v in (cm.view or {}).get("variants", [])]
        try:
            vid = visible[int(which)]
        except (IndexError, ValueError):
            await self._ch.answer(cb_id)
            return "ignored"
        async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
            v = await s.get(PostVariant, vid)
            req = (
                await s.get(ApprovalRequest, cm.approval_request_id)
                if cm.approval_request_id
                else None
            )
            if v is None or req is None or not is_open(req.state):
                await self._ch.answer(cb_id, "This request is already decided.")
                return "closed"
            set_id = v.post_set_id
        who = self._who(user)
        try:
            jobs = await self._media.regenerate(who, ws, RequestMeta(surface=self._ct), set_id, vid)
        except SecondFactorRequired:
            await self._ch.answer(
                cb_id,
                "That is above this workspace's cost ceiling. Regenerate from the web, where "
                "you can confirm with a second factor.",
                alert=True,
            )
            return "second_factor_required"
        except (ConflictError, InvalidInput, NotFoundError, ServiceUnavailableError) as exc:
            await self._ch.answer(cb_id, str(exc)[:190], alert=True)
            return "refused"
        total = sum((j.estimated_cost_usd for j in jobs), start=Decimal(0))
        await self._ch.answer(
            cb_id,
            f"🎨 Regenerating {len(jobs)} image{'s' if len(jobs) != 1 else ''} (about ${total}). "
            "You'll get the card again when it's ready.",
        )
        return "regenerating"

    async def _edit_prompt(
        self, cb_id: str, ws: uuid.UUID, cm: ChannelMessage, binding: ApproverBinding, which: str
    ) -> str:
        async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
            req = (
                await s.get(ApprovalRequest, cm.approval_request_id)
                if cm.approval_request_id
                else None
            )
            if req is None or not is_open(req.state):
                await self._ch.answer(cb_id, "This request is already decided.")
                return "closed"
            rows = await approvals.variant_states(s, req.id)
            visible = [uuid.UUID(v) for v in (cm.view or {}).get("variants", [])]
            if which == "all":
                targets = [
                    r.post_variant_id
                    for r in rows
                    if r.state == "pending" and r.post_variant_id in visible
                ]
                label = f"all {len(targets)} accounts"
            else:
                idx = int(which)
                if idx >= len(visible):
                    await self._ch.answer(cb_id)
                    return "ignored"
                targets = [visible[idx]]
                v = await s.get(PostVariant, visible[idx])
                acct = await s.get(SocialAccount, v.social_account_id) if v else None
                label = acct.nickname if acct else "this account"
        mid = await self._reply(
            cm.external_chat_id,
            f"✏️ Editing <b>{render.esc(label)}</b>. Reply with the corrected text, "
            "or tell me what to change. Examples: <i>shorter</i>, <i>more professional</i>, "
            "<i>add a hook</i>, <i>cut the last paragraph</i>.",
            force_reply=True,
            placeholder="What should change?",
        )
        await self._set_prompt(
            binding,
            {
                "ws": str(ws),
                "cm": str(cm.id),
                "request": str(cm.approval_request_id),
                "variants": [str(t) for t in targets],
                "mid": mid,
                "at": datetime.now(UTC).isoformat(),
            },
        )
        await self._ch.answer(cb_id)
        return "edit_prompt"

    async def _set_prompt(self, binding: ApproverBinding, prompt: dict[str, Any] | None) -> None:
        async with channel_transaction(self._sm, self._ct, binding.external_id) as s:
            b = await s.get(ApproverBinding, binding.id, with_for_update=True)
            if b is None:
                return
            prefs = dict(b.preferences or {})
            if prompt is None:
                prefs.pop("edit_prompt", None)
            else:
                prefs["edit_prompt"] = prompt
            b.preferences = prefs

    async def _edit_reply(
        self, chat_id: str, binding: ApproverBinding, user: User, msg: dict[str, Any], text: str
    ) -> str:
        prompt = (binding.preferences or {}).get("edit_prompt")
        reply_to = str((msg.get("reply_to_message") or {}).get("message_id", ""))
        if not prompt or not text:
            await self._reply(chat_id, "I only understand commands here. /help lists them.")
            return "ignored"
        fresh = datetime.now(UTC) - datetime.fromisoformat(prompt["at"]) < EDIT_PROMPT_TTL
        if not fresh or (reply_to and prompt.get("mid") and reply_to != str(prompt["mid"])):
            await self._reply(chat_id, "That edit prompt has expired; tap Edit on the card again.")
            return "stale_prompt"
        ws = uuid.UUID(prompt["ws"])
        async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
            first = (
                await s.get(PostVariant, uuid.UUID(prompt["variants"][0]))
                if prompt["variants"]
                else None
            )
            ver = (
                await s.get(PostVersion, first.current_version_id)
                if first and first.current_version_id
                else None
            )
        kind = (
            classify(text, ver.body if ver else "")
            if len(prompt["variants"]) == 1
            else "instruction"
        )
        if kind == "ambiguous":
            await self._set_prompt(binding, {**prompt, "text": text[:4000]})
            ref = f"{short(ws)}:{prompt['cm'] and short(uuid.UUID(prompt['cm']))}"
            await self._reply(
                chat_id,
                "Is that an instruction for the writer, or the new text itself?",
                [
                    [
                        Button("✍️ An instruction", f"c:{ref}:i"),
                        Button("📝 The new text", f"c:{ref}:r"),
                    ]
                ],
            )
            return "ambiguous"
        return await self._apply_edit(chat_id, binding, user, prompt, text, kind)

    async def _confirm_kind(
        self, cb_id: str, chat_id: str, binding: ApproverBinding, user: User, choice: str
    ) -> str:
        prompt = (binding.preferences or {}).get("edit_prompt")
        await self._ch.answer(cb_id)
        if not prompt or not prompt.get("text"):
            return "stale_prompt"
        return await self._apply_edit(
            chat_id,
            binding,
            user,
            prompt,
            prompt["text"],
            "instruction" if choice == "i" else "replacement",
        )

    async def _apply_edit(
        self,
        chat_id: str,
        binding: ApproverBinding,
        user: User,
        prompt: dict[str, Any],
        text: str,
        kind: str,
    ) -> str:
        ws = uuid.UUID(prompt["ws"])
        variant_ids = [uuid.UUID(v) for v in prompt["variants"]]
        meta = RequestMeta(surface="telegram")
        who = self._who(user)
        await self._set_prompt(binding, None)
        try:
            if kind == "instruction":
                await self._ap.request_edit(
                    who,
                    ws,
                    uuid.UUID(prompt["request"]),
                    meta,
                    instruction=text[:1000],
                    variant_ids=variant_ids,
                    channel=self._ct,
                    external_id=chat_id,
                )
                await self._reply(chat_id, "✍️ Rewriting… a fresh card follows when it's ready.")
                return "instruction"
            if self._posts is None:
                await self._reply(
                    chat_id,
                    "Replacing text from the bot is not available here; edit it in the web app.",
                )
                return "unavailable"
            async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
                v = await s.get(PostVariant, variant_ids[0])
                ver = (
                    await s.get(PostVersion, v.current_version_id)
                    if v and v.current_version_id
                    else None
                )
                if v is None or ver is None:
                    raise ApplicationError("That post is gone.")
                set_id = v.post_set_id
                content = drafting.ContentInput(
                    body=text[:10000],
                    thread_parts=None,
                    hashtags=list(ver.hashtags),
                    link_url=ver.link_url,
                    media=list(ver.media),
                )
                based_on = ver.id
            await self._posts.create_version(
                who, ws, set_id, variant_ids[0], meta, content=content, based_on_version_id=based_on
            )
            await self._ap.resubmit_after_edit(ws, set_id, variant_ids, user.id)
            await self._reply(chat_id, "📝 Saved your text as a new version; asking again.")
            return "replacement"
        except ApplicationError as exc:
            await self._reply(
                chat_id,
                f"Couldn't apply that: {render.esc(getattr(exc, 'message', str(exc)))[:200]}",
            )
            return getattr(exc, "code", "error")

    async def _reschedule(
        self, cb_id: str, chat_id: str, ws: uuid.UUID, cm: ChannelMessage, opt: str, user: User
    ) -> str:
        async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
            w = await s.get(Workspace, ws)
            tz = zone(w.timezone if w else "UTC")
        now = datetime.now(UTC)
        if opt == "1h":
            at = now + timedelta(hours=1)
        elif opt == "3h":
            at = now + timedelta(hours=3)
        else:
            local = now.astimezone(tz).date() + timedelta(days=1)
            at = datetime.combine(local, time(9, 0), tzinfo=tz).astimezone(UTC)
        if cm.approval_request_id is None:
            await self._ch.answer(cb_id)
            return "ignored"
        try:
            await self._ap.reschedule(
                self._who(user),
                ws,
                cm.approval_request_id,
                RequestMeta(surface="telegram"),
                scheduled_for=at,
                channel=self._ct,
                external_id=chat_id,
            )
        except ApplicationError as exc:
            await self._ch.answer(
                cb_id, getattr(exc, "message", "That didn't work.")[:180], alert=True
            )
            return getattr(exc, "code", "error")
        await self._ch.answer(cb_id, "🕒 Rescheduled")
        if cm.approval_request_id:
            await self.refresh_cards(ws, cm.approval_request_id)
        return "rescheduled"

    async def _review(
        self, cb_id: str, ws: uuid.UUID, request_id: uuid.UUID, binding: ApproverBinding, user: User
    ) -> str:
        """``/queue``'s Review button: (re)send this person's card for one request."""
        async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
            existing = (
                (
                    await s.execute(
                        select(ChannelMessage).where(
                            ChannelMessage.approval_request_id == request_id,
                            ChannelMessage.user_id == user.id,
                            ChannelMessage.message_kind == "card",
                            ChannelMessage.channel_type == self._ct,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for cm in existing:
                cm.message_kind = "notice"  # the old card stays as a record; a fresh one follows
        await self._ch.answer(cb_id)
        await self.send_cards(ws, request_id)
        return "review"

    # ------------------------------------------------------------------ commands

    async def _workspaces(self, user: User) -> list[tuple[Workspace, str]]:
        async with user_transaction(self._sm, user.id) as s:
            rows = (
                await s.execute(
                    select(Workspace, WorkspaceMember.role)
                    .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                    .where(WorkspaceMember.user_id == user.id, Workspace.deleted_at.is_(None))
                    .order_by(Workspace.name)
                )
            ).all()
        return [(w, r) for w, r in rows]

    async def _active(self, binding: ApproverBinding, user: User) -> Workspace | None:
        mine = await self._workspaces(user)
        want = (binding.preferences or {}).get("active_workspace_id")
        for w, _ in mine:
            if str(w.id) == want:
                return w
        return mine[0][0] if mine else None

    async def _command(self, chat_id: str, binding: ApproverBinding, user: User, text: str) -> str:
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@", 1)[0].lower()
        arg = arg.strip()
        if cmd == "/help":
            await self._reply(chat_id, HELP)
            return "help"
        if cmd == "/whoami":
            mine = await self._workspaces(user)
            active = await self._active(binding, user)
            lines = [f"👤 <b>{render.esc(user.display_name)}</b> · {render.esc(user.email)}"]
            lines += [
                f"{'▶️' if active and w.id == active.id else '•'} "
                f"{render.esc(w.name)} · {render.esc(role)}"
                for w, role in mine
            ]
            await self._reply(chat_id, "\n".join(lines))
            return "whoami"
        if cmd == "/workspaces":
            mine = await self._workspaces(user)
            lines = []
            for i, (w, role) in enumerate(mine, start=1):
                async with workspace_transaction(self._sm, WorkspaceContext(w.id)) as s:
                    pending = await s.scalar(
                        select(func.count())
                        .select_from(ApprovalRequest)
                        .where(ApprovalRequest.state.in_(["pending", "awaiting_edit"]))
                    )
                lines.append(
                    f"{i}. {render.esc(w.name)} · {render.esc(role)} · {pending or 0} pending"
                )
            await self._reply(chat_id, "\n".join(lines) or "No workspaces yet.")
            return "workspaces"
        if cmd == "/switch":
            mine = await self._workspaces(user)
            pick = None
            if arg.isdigit() and 1 <= int(arg) <= len(mine):
                pick = mine[int(arg) - 1][0]
            else:
                pick = next(
                    (w for w, _ in mine if w.name.lower() == arg.lower() or w.slug == arg.lower()),
                    None,
                )
            if pick is None:
                await self._reply(chat_id, "No workspace by that name. /workspaces lists them.")
                return "switch_unknown"
            async with channel_transaction(self._sm, self._ct, chat_id) as s:
                b = await s.get(ApproverBinding, binding.id, with_for_update=True)
                if b is not None:
                    b.preferences = {**(b.preferences or {}), "active_workspace_id": str(pick.id)}
            await self._reply(chat_id, f"▶️ Active workspace: <b>{render.esc(pick.name)}</b>")
            return "switched"
        ws = await self._active(binding, user)
        if ws is None:
            await self._reply(chat_id, "You are not in any workspace yet.")
            return "no_workspace"
        if cmd in ("/queue", "/groups", "/accounts", "/today", "/stats"):
            text_out, rows = await self._report(cmd, ws, user)
            await self._reply(chat_id, text_out, rows)
            return cmd.strip("/")
        if cmd in ("/kill", "/pause"):
            if cmd == "/kill" and arg.lower() == "all":
                await self._reply(
                    chat_id,
                    "🛑 Stop automation in <b>every</b> workspace you can reach?",
                    [[Button("🛑 Stop everything", f"K:{short(ws.id)}"), Button("✖️ Cancel", "x")]],
                )
                return "kill_all_confirm"
            return await self._kill(chat_id, ws, user, arg, cancel_approvals=cmd == "/kill")
        if cmd == "/pin":
            return await self._pin(chat_id, user, arg)
        if cmd == "/resume":
            await self._reply(
                chat_id,
                "Resuming needs an Admin, with confirmation, in the web app (Automation → Resume). "
                "Stopping is instant from here; starting again is deliberate.",
            )
            return "resume_web_only"
        await self._reply(chat_id, "I don't know that command. /help lists them.")
        return "unknown_command"

    async def _scope(self, s: AsyncSession, arg: str) -> tuple[str, uuid.UUID | None, str]:
        if not arg:
            return "workspace", None, "everything"
        needle = arg.lower().lstrip("@")
        g = (
            await s.execute(select(AccountGroup).where(func.lower(AccountGroup.name) == needle))
        ).scalar_one_or_none()
        if g is not None:
            return "account_group", g.id, g.name
        a = (
            (
                await s.execute(
                    select(SocialAccount).where(
                        (func.lower(SocialAccount.nickname) == needle)
                        | (func.lower(SocialAccount.handle) == needle)
                    )
                )
            )
            .scalars()
            .first()
        )
        if a is not None:
            return "social_account", a.id, a.nickname
        raise LookupError(arg)

    async def _covered(
        self, s: AsyncSession, scope_type: str, scope_id: uuid.UUID | None
    ) -> tuple[set[uuid.UUID], int]:
        if scope_type == "workspace":
            accounts = set((await s.execute(select(SocialAccount.id))).scalars())
            groups = int(await s.scalar(select(func.count()).select_from(AccountGroup)) or 0)
        elif scope_type == "account_group":
            accounts = set(
                (
                    await s.execute(
                        select(AccountGroupMember.social_account_id).where(
                            AccountGroupMember.account_group_id == scope_id
                        )
                    )
                ).scalars()
            )
            groups = 1
        else:
            accounts, groups = {scope_id} if scope_id else set(), 0
        return accounts, groups

    async def _kill(
        self, chat_id: str, ws: Workspace, user: User, arg: str, *, cancel_approvals: bool
    ) -> str:
        if self._gov is None:
            await self._reply(chat_id, "Not available here; use the web app.")
            return "unavailable"
        async with workspace_transaction(self._sm, WorkspaceContext(ws.id)) as s:
            try:
                scope_type, scope_id, label = await self._scope(s, arg)
            except LookupError:
                await self._reply(
                    chat_id,
                    f"No group or account called “{render.esc(arg)}” in {render.esc(ws.name)}.",
                )
                return "scope_unknown"
            accounts, groups = await self._covered(s, scope_type, scope_id)
            held = await s.scalar(
                select(func.count())
                .select_from(ScheduledPost)
                .where(
                    ScheduledPost.social_account_id.in_(accounts or {uuid.UUID(int=0)}),
                    ScheduledPost.state.in_(["queued", "failed"]),
                )
            )
        meta = RequestMeta(surface="telegram")
        who = self._who(user)
        try:
            await self._gov.set_kill_switch(
                who,
                ws.id,
                meta,
                scope_type=scope_type,
                scope_id=scope_id,
                engaged=True,
                reason="Stopped from Telegram" if cancel_approvals else "Paused from Telegram",
            )
        except ApplicationError as exc:
            await self._reply(chat_id, render.esc(getattr(exc, "message", "That didn't work.")))
            return getattr(exc, "code", "error")
        except PermissionDenied:
            await self._reply(chat_id, "You can't stop automation there.")
            return "forbidden"
        cancelled = (
            await self._ap.cancel_for_accounts(who, ws.id, accounts, meta)
            if cancel_approvals
            else 0
        )
        verb = "KILL SWITCH ENGAGED" if cancel_approvals else "PAUSED"
        lines = [
            f"🛑 <b>{verb}</b> · {render.esc(ws.name)} · {render.esc(label)}",
            "",
            f"Automation stopped across {len(accounts)} account{'s' if len(accounts) != 1 else ''}"
            + (f" in {groups} group{'s' if groups != 1 else ''}" if groups else "")
            + ".",
            f"• {held or 0} scheduled post{'s' if held != 1 else ''} held",
        ]
        if cancel_approvals:
            lines.append(f"• {cancelled} pending approval{'s' if cancelled != 1 else ''} cancelled")
        lines += ["", "Nothing will publish there until an Admin resumes it in the web app."]
        await self._reply(chat_id, "\n".join(lines))
        return "killed" if cancel_approvals else "paused"

    async def _kill_all(self, cb_id: str, chat_id: str, user: User) -> str:
        await self._ch.answer(cb_id)
        stopped = 0
        for w, _ in await self._workspaces(user):
            try:
                if self._gov is not None:
                    await self._gov.set_kill_switch(
                        self._who(user),
                        w.id,
                        RequestMeta(surface="telegram"),
                        scope_type="workspace",
                        scope_id=None,
                        engaged=True,
                        reason="Stopped everything from Telegram",
                    )
                    stopped += 1
            except (ApplicationError, PermissionDenied):
                continue
        await self._reply(
            chat_id, f"🛑 Stopped automation in {stopped} workspace{'s' if stopped != 1 else ''}."
        )
        return "killed_all"

    async def _report(self, cmd: str, ws: Workspace, user: User) -> tuple[str, list[list[Button]]]:
        async with workspace_transaction(self._sm, WorkspaceContext(ws.id)) as s:
            principal = await load_user_principal(s, self._who(user), ws.id)
            tz = zone(ws.timezone)
            if cmd == "/groups":
                lines = [f"👥 <b>{render.esc(ws.name)}</b> · groups"]
                for g in (
                    await s.execute(select(AccountGroup).order_by(AccountGroup.name))
                ).scalars():
                    names = list(
                        (
                            await s.execute(
                                select(SocialAccount.nickname)
                                .join(
                                    AccountGroupMember,
                                    AccountGroupMember.social_account_id == SocialAccount.id,
                                )
                                .where(AccountGroupMember.account_group_id == g.id)
                            )
                        ).scalars()
                    )
                    lines.append(
                        f"• <b>{render.esc(g.name)}</b>: "
                        f"{render.esc(', '.join(names) or 'no accounts')}"
                    )
                return "\n".join(lines), []
            if cmd == "/accounts":
                lines = [f"🔗 <b>{render.esc(ws.name)}</b> · accounts"]
                for a in (
                    await s.execute(select(SocialAccount).order_by(SocialAccount.nickname))
                ).scalars():
                    icon = "✅" if a.status == "active" else "⚠️"
                    extra = "" if a.status == "active" else f" · {a.status.replace('_', ' ')}"
                    lines.append(
                        f"{icon} {render.esc(a.nickname)} "
                        f"({render.esc(render.platform_name(a.platform))}){extra}"
                    )
                return "\n".join(lines), []
            if cmd == "/queue":
                reqs = list(
                    (
                        await s.execute(
                            select(ApprovalRequest)
                            .where(ApprovalRequest.state.in_(["pending", "awaiting_edit"]))
                            .order_by(ApprovalRequest.created_at)
                        )
                    ).scalars()
                )
                by_group: dict[str, list[str]] = {}
                rows: list[list[Button]] = []
                now = datetime.now(UTC)
                for r in reqs:
                    view = await self._ap._view(s, principal, r)
                    if not view.variants:
                        continue
                    rg = (
                        await s.get(AccountGroup, r.account_group_id)
                        if r.account_group_id
                        else None
                    )
                    name = rg.name if rg else "Direct"
                    left = render.remaining(r.expires_at.isoformat(), now)
                    warn = " ⚠️" if r.expires_at - now < timedelta(hours=3) else ""
                    by_group.setdefault(name, []).append(
                        f"  • {render.esc(view.title or 'Post')} · "
                        f"{len(view.variants)} posts · expires {left}{warn}"
                    )
                    rows.append(
                        [
                            Button(
                                f"👀 Review: {(view.title or 'Post')[:24]}",
                                f"q:{short(ws.id)}:{short(r.id)}",
                            )
                        ]
                    )
                lines = [
                    f"📋 <b>{render.esc(ws.name)}</b> · "
                    f"{sum(len(v) for v in by_group.values())} pending"
                ]
                for g_name, items in by_group.items():
                    lines.append(f"\n<b>{render.esc(g_name)}</b>")
                    lines.extend(items)
                return "\n".join(
                    lines
                ) if by_group else f"📋 {render.esc(ws.name)} · nothing waiting.", rows[:8]
            if cmd == "/today":
                today = datetime.now(UTC).astimezone(tz).date()
                lo = datetime.combine(today, time(0, 0), tzinfo=tz).astimezone(UTC)
                hi = lo + timedelta(days=1)
                sps = (
                    await s.execute(
                        select(ScheduledPost)
                        .where(
                            ScheduledPost.scheduled_for >= lo,
                            ScheduledPost.scheduled_for < hi,
                            ScheduledPost.state != "cancelled",
                        )
                        .order_by(ScheduledPost.scheduled_for)
                    )
                ).scalars()
                lines = [f"🗓 <b>{render.esc(ws.name)}</b> · today"]
                for sp in sps:
                    sa = await s.get(SocialAccount, sp.social_account_id)
                    icon = {"published": "✅", "dead_lettered": "❌", "failed": "🔁"}.get(
                        sp.state, "🕒"
                    )
                    lines.append(
                        f"{icon} {sp.scheduled_for.astimezone(tz).strftime('%H:%M')} · "
                        f"{render.esc(sa.nickname if sa else '')} · "
                        f"{sp.state.replace('_', ' ')}"
                    )
                return "\n".join(lines) if len(
                    lines
                ) > 1 else f"🗓 {render.esc(ws.name)} · nothing today.", []
            # /stats
            since = datetime.now(UTC) - timedelta(days=7)
            posts = await s.scalar(
                select(func.count())
                .select_from(PublishedPost)
                .where(PublishedPost.published_at >= since)
            )
            from mera.infrastructure.db.models import MetricSnapshot

            totals = {}
            for key in ("impressions", "likes", "comments", "shares"):
                latest = (
                    select(
                        MetricSnapshot.published_post_id, func.max(MetricSnapshot.value).label("v")
                    )
                    .join(PublishedPost, PublishedPost.id == MetricSnapshot.published_post_id)
                    .where(MetricSnapshot.metric_key == key, PublishedPost.published_at >= since)
                    .group_by(MetricSnapshot.published_post_id)
                    .subquery()
                )
                totals[key] = await s.scalar(select(func.sum(latest.c.v)))

            def fmt(v: Any) -> str:
                return "not reported" if v is None else f"{int(v):,}"

            return (
                f"📊 <b>{render.esc(ws.name)}</b> · last 7 days\n"
                f"Posts published: {posts or 0}\n"
                f"Impressions: {fmt(totals['impressions'])}\n"
                f"Likes: {fmt(totals['likes'])} · Comments: {fmt(totals['comments'])} · "
                f"Shares: {fmt(totals['shares'])}",
                [],
            )


def _label(msg: dict[str, Any]) -> str | None:
    frm = msg.get("from") or {}
    name = frm.get("username") or " ".join(
        filter(None, [frm.get("first_name"), frm.get("last_name")])
    )
    return (str(name)[:64] or None) if name else None


def _ws_from(parts: list[str]) -> uuid.UUID | None:
    try:
        return unshort(parts[1])
    except (IndexError, ValueError):
        return None
