"""Approval channels from the web (M7; docs/04 §4.9): channel status per workspace, pairing
codes, paired approvers, revocation, and the sandbox phone.

A binding belongs to a **user** across all their workspaces (docs/07 §6). Pairing codes are
single use, hashed, and live ten minutes. Revoking a binding revokes every outstanding bot
token issued to that person, in every workspace.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from mera.application.channels.bot import PAIRING_TTL, BotRuntime, code_hash, new_code
from mera.application.channels.whatsapp_state import WhatsAppState
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import ConflictError, Forbidden, InvalidInput, NotFoundError
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.config import Settings
from mera.domain.context import WorkspaceContext
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.domain.ports.approval_channel import OutboundMessage
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.db.models import (
    ApprovalChannel,
    ApprovalToken,
    ApproverBinding,
    PairingCode,
    SandboxCapture,
    User,
    Workspace,
    WorkspaceMember,
)
from mera.infrastructure.db.repositories.audit import AuditEntry, record
from mera.infrastructure.db.session import user_transaction, workspace_transaction


@dataclass(frozen=True, slots=True)
class BindingView:
    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str
    channel_type: str
    external_label: str | None
    status: str
    verified_at: datetime
    is_me: bool


@dataclass(frozen=True, slots=True)
class PairingCodeView:
    code: str
    expires_at: datetime
    deep_link: str | None
    command: str


CHANNELS = ("telegram", "whatsapp")
_SNAPSHOT = {
    "telegram": {"max_body_chars": 4096, "supports_message_edit": True},
    "whatsapp": {"max_body_chars": 1024, "supports_message_edit": False},
}


class ChannelService(WorkspaceScopedService):
    def __init__(
        self,
        sm: Any,
        settings: Settings,
        bot: BotRuntime | None,
        whatsapp: tuple[BotRuntime, WhatsAppState] | None = None,
    ) -> None:
        super().__init__(sm)
        self._settings = settings
        self._bot = bot
        self._wa = whatsapp

    def _runtime(self, channel_type: str) -> BotRuntime | None:
        if channel_type == "whatsapp":
            return self._wa[0] if self._wa else None
        return self._bot

    @staticmethod
    def _channel(channel_type: str) -> str:
        if channel_type not in CHANNELS:
            raise InvalidInput("Unknown channel.", code="unknown_channel", field="channel")
        return channel_type

    # ----------------------------------------------------------------- channel

    async def status(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> dict[str, Any]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            rows = {r.channel_type: r for r in (await s.execute(select(ApprovalChannel))).scalars()}
        row = rows.get("telegram")
        wa_row = rows.get("whatsapp")
        configured = self._settings.telegram_bot_token is not None
        wa_usage = await self._wa[1].month_usage() if self._wa else None
        return {
            "telegram": {
                "configured": configured,
                "sandbox": not configured,
                "bot_username": self._settings.telegram_bot_username,
                "mode": self._settings.telegram_mode,
                "enabled": row is None or row.status == "active",
                "last_test_at": row.last_test_at.isoformat() if row and row.last_test_at else None,
                "last_test_result": row.last_test_result if row else None,
            },
            "whatsapp": {
                "available": True,
                "configured": self._wa is not None,
                "display_number": self._settings.whatsapp_display_number,
                "enabled": self._wa is not None and (wa_row is None or wa_row.status == "active"),
                "last_test_at": (
                    wa_row.last_test_at.isoformat() if wa_row and wa_row.last_test_at else None
                ),
                "last_test_result": wa_row.last_test_result if wa_row else None,
                # Templates are the billed messages; the cap stops a runaway loop (docs/07 §4.2).
                "usage": wa_usage,
            },
        }

    async def set_enabled(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        enabled: bool,
        channel_type: str = "telegram",
    ) -> dict[str, Any]:
        channel_type = self._channel(channel_type)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.CONNECT_ACCOUNTS, workspace_id)
            ws = await s.get(Workspace, workspace_id)
            assert ws is not None
            self._ensure_writable(ws)
            row = (
                await s.execute(
                    select(ApprovalChannel)
                    .where(ApprovalChannel.channel_type == channel_type)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                row = ApprovalChannel(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    channel_type=channel_type,
                    created_by_user_id=user.user_id,
                    constraints_snapshot=_SNAPSHOT[channel_type],
                )
                s.add(row)
            row.status = "active" if enabled else "disabled"
            row.updated_at = datetime.now(UTC)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "approval_channel.enabled" if enabled else "approval_channel.disabled",
                    workspace_id=workspace_id,
                    subject_type="approval_channel",
                    subject_id=row.id,
                ),
            )
        return await self.status(user, workspace_id, meta)

    async def test(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        channel_type: str = "telegram",
    ) -> dict[str, Any]:
        """Send a test message to the caller's own paired chats on one channel (docs/07
        §10.1 step 5)."""
        channel_type = self._channel(channel_type)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            ws = await s.get(Workspace, workspace_id)
            bindings = list(
                (
                    await s.execute(
                        select(ApproverBinding).where(
                            ApproverBinding.user_id == user.user_id,
                            ApproverBinding.status == "active",
                            ApproverBinding.channel_type == channel_type,
                        )
                    )
                ).scalars()
            )
        if not bindings:
            raise ConflictError("Pair a chat first, then send a test.", code="not_paired")
        runtime = self._runtime(channel_type)
        if runtime is None:
            raise ConflictError("The approval bot is not available.", code="channel_unavailable")
        sent, error = 0, None
        for b in bindings:
            try:
                await runtime._ch.send(
                    b.external_id,
                    OutboundMessage(
                        text=(
                            f"👋 Test from Mera · {ws.name if ws else ''}. "
                            "Approvals will arrive here."
                        )
                    ),
                )
                sent += 1
            except Exception as exc:  # report, do not raise: this is a diagnostic
                error = str(exc)[:200]
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            row = (
                await s.execute(
                    select(ApprovalChannel).where(ApprovalChannel.channel_type == channel_type)
                )
            ).scalar_one_or_none()
            if row is not None:
                row.last_test_at = datetime.now(UTC)
                row.last_test_result = "ok" if error is None else error
        return {"sent": sent, "error": error}

    # ---------------------------------------------------------------- bindings

    async def list_bindings(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> list[BindingView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            rows = (
                await s.execute(
                    select(ApproverBinding, User.display_name)
                    .join(User, User.id == ApproverBinding.user_id)
                    .join(WorkspaceMember, WorkspaceMember.user_id == ApproverBinding.user_id)
                    .where(
                        WorkspaceMember.workspace_id == workspace_id,
                        ApproverBinding.status == "active",
                    )
                    .order_by(ApproverBinding.verified_at)
                )
            ).all()
            return [
                BindingView(
                    b.id,
                    b.user_id,
                    name,
                    b.channel_type,
                    b.external_label,
                    b.status,
                    b.verified_at,
                    b.user_id == user.user_id,
                )
                for b, name in rows
            ]

    async def mint_pairing_code(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        channel_type: str = "telegram",
    ) -> PairingCodeView:
        """A code is bound to one channel: a Telegram code does not pair a WhatsApp chat."""
        self._require_human(user)
        channel_type = self._channel(channel_type)
        if channel_type == "whatsapp" and self._wa is None:
            raise ConflictError(
                "WhatsApp is not set up on this deployment.", code="channel_unavailable"
            )
        async with self._tx(user, workspace_id, meta) as s:
            await load_user_principal(s, user, workspace_id)  # a member of this workspace
        code = new_code()
        expires = datetime.now(UTC) + PAIRING_TTL
        async with user_transaction(self._sm, user.user_id) as s:
            # One live code per person: minting a new one retires the previous.
            await s.execute(
                update(PairingCode)
                .where(PairingCode.user_id == user.user_id, PairingCode.used_at.is_(None))
                .values(expires_at=datetime.now(UTC))
            )
            s.add(
                PairingCode(
                    id=uuid7(),
                    user_id=user.user_id,
                    channel_type=channel_type,
                    code_hash=code_hash(code),
                    expires_at=expires,
                )
            )
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            await record(
                s,
                self._actor_entry(
                    user, meta, "approver_binding.code_minted", workspace_id=workspace_id
                ),
            )
        if channel_type == "whatsapp":
            number = "".join(
                ch for ch in (self._settings.whatsapp_display_number or "") if ch.isdigit()
            )
            link = f"https://wa.me/{number}?text=%2Fpair%20{code}" if number else None
        else:
            bot = self._settings.telegram_bot_username
            link = f"https://t.me/{bot}?start={code}" if bot else None
        return PairingCodeView(
            code=code, expires_at=expires, deep_link=link, command=f"/pair {code}"
        )

    async def revoke_binding(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        binding_id: uuid.UUID,
        meta: RequestMeta,
    ) -> None:
        """Your own binding. Revoking kills every outstanding bot token issued to you, in
        every workspace (docs/07 §6)."""
        self._require_human(user)
        now = datetime.now(UTC)
        async with user_transaction(self._sm, user.user_id) as s:
            b = await s.get(ApproverBinding, binding_id, with_for_update=True)
            if b is None:
                raise NotFoundError("Binding not found.")
            if b.user_id != user.user_id:
                raise Forbidden("You can revoke only your own chats.", code="forbidden")
            if b.status != "active":
                return
            b.status, b.revoked_at = "revoked", now
            ws_ids = list(
                (
                    await s.execute(
                        select(WorkspaceMember.workspace_id).where(
                            WorkspaceMember.user_id == user.user_id
                        )
                    )
                ).scalars()
            )
        for ws in ws_ids:
            async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
                await s.execute(
                    update(ApprovalToken)
                    .where(
                        ApprovalToken.approver_user_id == user.user_id,
                        ApprovalToken.channel == b.channel_type,
                        ApprovalToken.used_at.is_(None),
                        ApprovalToken.revoked_at.is_(None),
                    )
                    .values(revoked_at=now)
                )
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            await record(
                s,
                AuditEntry(
                    action="approver_binding.revoked",
                    actor_type="user",
                    actor_id=user.user_id,
                    surface=meta.surface,
                    workspace_id=workspace_id,
                    subject_type="approver_binding",
                    subject_id=binding_id,
                ),
            )

    # ---------------------------------------------------------- sandbox phone

    def sandbox_chat(self, user: AuthenticatedUser) -> str:
        return f"sandbox-{str(user.user_id)[:8]}"

    async def sandbox_send(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta, *, text: str
    ) -> dict[str, Any]:
        """Type into the sandbox phone: the update goes through the real bot runtime."""
        return await self._sandbox_update(
            user,
            workspace_id,
            meta,
            {
                "message": {
                    "message_id": int(datetime.now(UTC).timestamp() * 1000) % 10**9,
                    "chat": {"id": self.sandbox_chat(user), "type": "private"},
                    "from": {"username": f"sandbox_{user.display_name[:20]}"},
                    "text": text[:4000],
                }
            },
        )

    async def sandbox_tap(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        message_id: str,
        data: str,
    ) -> dict[str, Any]:
        return await self._sandbox_update(
            user,
            workspace_id,
            meta,
            {
                "callback_query": {
                    "id": uuid.uuid4().hex,
                    "data": data[:64],
                    "message": {
                        "message_id": message_id,
                        "chat": {"id": self.sandbox_chat(user), "type": "private"},
                    },
                }
            },
        )

    async def _sandbox_update(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        if (
            not self._settings.sandbox_mode
            or self._settings.telegram_bot_token is not None
            or self._bot is None
        ):
            raise Forbidden(
                "The sandbox phone is available in sandbox mode only.", code="sandbox_only"
            )
        async with self._tx(user, workspace_id, meta) as s:
            await load_user_principal(s, user, workspace_id)
        from mera.infrastructure.channels.fake import FakeChannel

        ch = self._bot._ch
        before = len(ch.sent) if isinstance(ch, FakeChannel) else 0
        outcome = await self._bot.handle_update({"update_id": uuid.uuid4().hex, **body})
        replies = []
        if isinstance(ch, FakeChannel):
            chat = self.sandbox_chat(user)
            for m in ch.sent[before:]:
                if m["chat_id"] == chat:
                    msg = m["message"]
                    replies.append(
                        {
                            "message_id": m["message_id"],
                            "text": msg.text,
                            "buttons": [
                                [{"label": b.label, "data": b.data, "url": b.url} for b in row]
                                for row in msg.rows
                            ],
                        }
                    )
            answers = ch.answers[-1:] if "callback_query" in body else []
        else:
            answers = []
        return {
            "outcome": outcome,
            "replies": replies,
            "answer": answers[0]["text"] if answers else None,
        }

    async def sandbox_captures(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        after_id: int,
        kind: str | None,
    ) -> list[dict[str, Any]]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            q = (
                select(SandboxCapture)
                .where(SandboxCapture.id > after_id)
                .order_by(SandboxCapture.id.desc())
                .limit(200)
            )
            if kind:
                q = q.where(SandboxCapture.kind == kind)
            rows = list((await s.execute(q)).scalars())
            chat = self.sandbox_chat(user)
            return [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "target": r.target,
                    "social_account_id": str(r.social_account_id) if r.social_account_id else None,
                    "payload": r.payload,
                    "created_at": r.created_at.isoformat(),
                    "mine": r.target == f"telegram:{chat}",
                }
                for r in rows
                if r.kind != "channel_message"
                or r.target == f"telegram:{chat}"
                or rbac.allowed(principal, Permission.MANAGE_MEMBERS, workspace_id)
            ]
