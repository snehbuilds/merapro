"""Wiring the bot runtime: the real Telegram channel when a bot token is configured,
otherwise the sandbox ``FakeChannel`` (cards land in ``/sandbox`` instead of a phone)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.channels.bot import BotRuntime
from mera.application.channels.whatsapp_state import WhatsAppState
from mera.application.services.approval_service import ApprovalService
from mera.application.services.governance_service import GovernanceService
from mera.application.services.security_service import SecurityService
from mera.config import Settings
from mera.domain.ports.approval_channel import ApprovalChannel
from mera.infrastructure.channels.fake import FakeChannel
from mera.infrastructure.crypto.envelope import LocalKeyProvider
from mera.infrastructure.queue.deferrer import Deferrer

_FAKE = FakeChannel()  # one per process, so the sandbox inspector and the worker agree


def build_channel(settings: Settings) -> ApprovalChannel:
    if settings.telegram_bot_token is not None:
        from mera.infrastructure.channels.telegram import TelegramChannel

        return TelegramChannel(settings.telegram_bot_token.get_secret_value())
    return _FAKE


def build_bot(
    settings: Settings,
    sm: async_sessionmaker[AsyncSession],
    deferrer: Deferrer | None,
    *,
    posts: object | None = None,
    channel: ApprovalChannel | None = None,
    media: object | None = None,
    inbox: object | None = None,
    registry: object | None = None,
    workspace_bot_id: uuid.UUID | None = None,
) -> BotRuntime:
    ch = channel or build_channel(settings)
    return BotRuntime(
        sm,
        ch,
        ApprovalService(sm, deferrer, mfa_enforced=settings.mfa_enforced),
        public_url=settings.mera_public_url,
        posts=posts,
        governance=GovernanceService(sm),
        sandbox_capture=isinstance(ch, FakeChannel) or settings.sandbox_mode,
        security=SecurityService(
            sm, LocalKeyProvider.from_base64(settings.mera_master_key.get_secret_value())
        ),
        media=media,
        inbox=inbox,
        registry=registry,
        mfa_enforced=settings.mfa_enforced,
        workspace_bot_id=workspace_bot_id,
    )


def build_whatsapp_bot(
    settings: Settings,
    sm: async_sessionmaker[AsyncSession],
    deferrer: Deferrer | None,
    *,
    posts: object | None = None,
    channel: ApprovalChannel | None = None,
    media: object | None = None,
    inbox: object | None = None,
    registry: object | None = None,
) -> tuple[BotRuntime, WhatsAppState] | None:
    """The WhatsApp runtime (P2-M4) when the Cloud API is configured (or a channel is
    given, in tests); otherwise None and WhatsApp is simply not offered."""
    state = WhatsAppState(sm, monthly_template_cap=settings.whatsapp_monthly_template_cap)
    if channel is None:
        if settings.whatsapp_access_token is None or settings.whatsapp_phone_number_id is None:
            return None
        from mera.infrastructure.channels.whatsapp import WhatsAppChannel

        channel = WhatsAppChannel(
            phone_number_id=settings.whatsapp_phone_number_id,
            access_token=settings.whatsapp_access_token.get_secret_value(),
            window_open=state.window_open,
            on_send=state.record_send,
            reserve_template=state.reserve_template,
            language=settings.whatsapp_template_language,
            graph_url=settings.whatsapp_graph_url,
        )
    bot = build_bot(
        settings,
        sm,
        deferrer,
        posts=posts,
        channel=channel,
        media=media,
        inbox=inbox,
        registry=registry,
    )
    return bot, state
