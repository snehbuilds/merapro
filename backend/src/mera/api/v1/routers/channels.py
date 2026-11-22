"""Approval channels, approver bindings and pairing (docs/04 §4.9), plus the sandbox
inspector's phone and capture log (M9.17)."""

from __future__ import annotations

import hmac
import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from mera.api.deps import Channels, CurrentUser, Meta
from mera.api.v1.routers.posts import WS
from mera.application.channels.workspace_telegram import credentials
from mera.infrastructure.crypto.envelope import LocalKeyProvider

router = APIRouter(tags=["approval-channels"])
sandbox_router = APIRouter(tags=["sandbox"])
webhook_router = APIRouter(tags=["approval-channels"])


@webhook_router.post("/webhooks/telegram/{workspace_id}", include_in_schema=False)
async def workspace_telegram_webhook(workspace_id: uuid.UUID, request: Request) -> Response:
    from mera.bots.main import bounded_body

    settings = request.app.state.settings
    own = await credentials(
        request.app.state.sessionmaker,
        LocalKeyProvider.from_base64(settings.mera_master_key.get_secret_value()),
        workspace_id,
    )
    given = request.headers.get("x-telegram-bot-api-secret-token", "")
    if own is None or not hmac.compare_digest(given, own["webhook_secret"]):
        return Response(status_code=401)
    raw = await bounded_body(request)
    if raw is None:
        return Response(status_code=413)
    try:
        update = json.loads(raw)
    except ValueError:
        return Response(status_code=200)
    if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
        return Response(status_code=200)
    await request.app.state.deferrer.defer(
        "approvals:telegram_update",
        queue="approvals",
        queueing_lock=f"tg:{workspace_id}:{update['update_id']}",
        update=update,
        workspace_id=str(workspace_id),
    )
    return Response(status_code=200)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ChannelStatus(BaseModel):
    telegram: dict[str, Any]
    whatsapp: dict[str, Any]


class ChannelIn(BaseModel):
    enabled: bool


class TelegramBotIn(BaseModel):
    token: str = Field(min_length=20, max_length=256)


class PairingCodeIn(BaseModel):
    channel_type: Literal["telegram", "whatsapp"] = "telegram"


class BindingOut(_Out):
    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str
    channel_type: str
    external_label: str | None
    status: str
    verified_at: datetime
    is_me: bool


class BindingList(BaseModel):
    data: list[BindingOut]


class PairingCodeOut(_Out):
    code: str = Field(description="Shown once; single use; expires in 10 minutes")
    expires_at: datetime
    deep_link: str | None = Field(description="t.me or wa.me link that pairs in one tap")
    command: str


class TestOut(BaseModel):
    sent: int
    error: str | None


class SandboxSendIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class SandboxTapIn(BaseModel):
    message_id: str = Field(max_length=32)
    data: str = Field(max_length=64)


class SandboxResult(BaseModel):
    outcome: str
    replies: list[dict[str, Any]]
    answer: str | None


class CaptureList(BaseModel):
    data: list[dict[str, Any]]
    chat_id: str


@router.get(f"{WS}/approval-channels", response_model=ChannelStatus)
async def channel_status(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Channels, meta: Meta
) -> ChannelStatus:
    """Telegram and WhatsApp (P2-M4): configuration, switch, last test, template usage."""
    return ChannelStatus(**await svc.status(user, workspace_id, meta))


@router.put(f"{WS}/approval-channels/telegram", response_model=ChannelStatus)
async def set_telegram(
    workspace_id: uuid.UUID, body: ChannelIn, user: CurrentUser, svc: Channels, meta: Meta
) -> ChannelStatus:
    """Turn Telegram cards on or off for this workspace (Admins)."""
    return ChannelStatus(**await svc.set_enabled(user, workspace_id, meta, enabled=body.enabled))


@router.put(f"{WS}/approval-channels/telegram/bot", response_model=ChannelStatus)
async def save_telegram_bot(
    workspace_id: uuid.UUID, body: TelegramBotIn, user: CurrentUser, svc: Channels, meta: Meta
) -> ChannelStatus:
    return ChannelStatus(**await svc.save_telegram_bot(user, workspace_id, meta, body.token))


@router.post(f"{WS}/approval-channels/telegram/test", response_model=TestOut)
async def test_telegram(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Channels, meta: Meta
) -> TestOut:
    return TestOut(**await svc.test(user, workspace_id, meta))


@router.put(f"{WS}/approval-channels/whatsapp", response_model=ChannelStatus)
async def set_whatsapp(
    workspace_id: uuid.UUID, body: ChannelIn, user: CurrentUser, svc: Channels, meta: Meta
) -> ChannelStatus:
    """Turn WhatsApp cards on or off for this workspace (Admins)."""
    return ChannelStatus(
        **await svc.set_enabled(
            user, workspace_id, meta, enabled=body.enabled, channel_type="whatsapp"
        )
    )


@router.post(f"{WS}/approval-channels/whatsapp/test", response_model=TestOut)
async def test_whatsapp(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Channels, meta: Meta
) -> TestOut:
    return TestOut(**await svc.test(user, workspace_id, meta, channel_type="whatsapp"))


@router.get(f"{WS}/approver-bindings", response_model=BindingList)
async def list_bindings(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Channels, meta: Meta
) -> BindingList:
    rows = await svc.list_bindings(user, workspace_id, meta)
    return BindingList(data=[BindingOut.model_validate(r) for r in rows])


@router.post(
    f"{WS}/approver-bindings/pairing-codes",
    response_model=PairingCodeOut,
    status_code=status.HTTP_201_CREATED,
)
async def mint_pairing_code(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Channels,
    meta: Meta,
    body: PairingCodeIn | None = None,
) -> PairingCodeOut:
    """A one-time code for *you*, for one channel. One pairing serves every workspace you
    belong to."""
    channel = body.channel_type if body else "telegram"
    return PairingCodeOut.model_validate(
        await svc.mint_pairing_code(user, workspace_id, meta, channel_type=channel)
    )


@router.post(
    f"{WS}/approver-bindings/{{binding_id}}/revoke", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_binding(
    workspace_id: uuid.UUID, binding_id: uuid.UUID, user: CurrentUser, svc: Channels, meta: Meta
) -> Response:
    """Unpair one of your chats; every outstanding button issued to you stops working."""
    await svc.revoke_binding(user, workspace_id, binding_id, meta)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@sandbox_router.get(f"{WS}/sandbox/captures", response_model=CaptureList)
async def sandbox_captures(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Channels,
    meta: Meta,
    after_id: Annotated[int, Query(ge=0)] = 0,
    kind: Annotated[str | None, Query(pattern="^(platform_call|channel_message)$")] = None,
) -> CaptureList:
    """Every simulated platform call and channel message (sandbox mode)."""
    rows = await svc.sandbox_captures(user, workspace_id, meta, after_id=after_id, kind=kind)
    return CaptureList(data=rows, chat_id=svc.sandbox_chat(user))


@sandbox_router.post(f"{WS}/sandbox/telegram/send", response_model=SandboxResult)
async def sandbox_send(
    workspace_id: uuid.UUID, body: SandboxSendIn, user: CurrentUser, svc: Channels, meta: Meta
) -> SandboxResult:
    """Type a message into your sandbox phone (``/pair CODE``, ``/queue``, an edit reply)."""
    return SandboxResult(**await svc.sandbox_send(user, workspace_id, meta, text=body.text))


@sandbox_router.post(f"{WS}/sandbox/telegram/tap", response_model=SandboxResult)
async def sandbox_tap(
    workspace_id: uuid.UUID, body: SandboxTapIn, user: CurrentUser, svc: Channels, meta: Meta
) -> SandboxResult:
    """Tap a button on a card in your sandbox phone."""
    return SandboxResult(
        **await svc.sandbox_tap(
            user, workspace_id, meta, message_id=body.message_id, data=body.data
        )
    )
