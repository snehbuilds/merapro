"""The ``ApprovalChannel`` port (M6.4, M7.2; docs/07 §2).

Channels differ too much for a lowest common denominator, so the port exposes each
channel's **constraints** and the renderer adapts: Telegram edits a card in place,
WhatsApp (Phase 2) cannot and sends follow-ups instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ChannelConstraints:
    max_inline_buttons: int
    supports_button_rows: bool
    supports_list_picker: bool
    supports_message_edit: bool
    supports_media_groups: bool
    max_body_chars: int
    requires_templates_outside_window: bool
    session_window_hours: int | None
    supports_force_reply: bool
    per_message_cost: Decimal | None
    max_callback_bytes: int = 64


TELEGRAM = ChannelConstraints(
    max_inline_buttons=100,
    supports_button_rows=True,
    supports_list_picker=False,
    supports_message_edit=True,
    supports_media_groups=True,
    max_body_chars=4096,
    requires_templates_outside_window=False,
    session_window_hours=None,
    supports_force_reply=True,
    per_message_cost=None,
)


@dataclass(frozen=True, slots=True)
class Button:
    label: str
    data: str | None = None  # callback data (≤ 64 bytes on Telegram)
    url: str | None = None


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """A rendered message: HTML text (already escaped) and rows of buttons."""

    text: str
    rows: list[list[Button]] = field(default_factory=list)
    force_reply: bool = False
    reply_to_message_id: str | None = None
    placeholder: str | None = None  # ForceReply input hint
    silent: bool = False


class ChannelError(Exception):
    """The channel refused or failed (blocked by the user, network)."""


class ApprovalChannel(Protocol):
    channel_type: str
    constraints: ChannelConstraints

    async def send(self, chat_id: str, message: OutboundMessage) -> str:
        """Send; returns the channel's message id."""
        ...

    async def edit(self, chat_id: str, message_id: str, message: OutboundMessage) -> None: ...

    async def answer(
        self, callback_id: str, text: str | None = None, *, alert: bool = False
    ) -> None:
        """Acknowledge a button tap (Telegram shows ``text`` as a toast)."""
        ...
