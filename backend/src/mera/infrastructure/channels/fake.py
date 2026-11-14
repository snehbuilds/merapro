"""``FakeChannel`` (M6.4): records every rendered message, so tests assert on card content
(docs/07 §11: a regression that drops the source block must fail a test), and the
``/sandbox`` inspector can show what a phone would have received."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

from mera.domain.ports.approval_channel import TELEGRAM, ChannelConstraints, OutboundMessage

_ids = itertools.count(1000)


@dataclass
class FakeChannel:
    channel_type: str = "telegram"
    constraints: ChannelConstraints = TELEGRAM
    sent: list[dict[str, Any]] = field(default_factory=list)
    edits: list[dict[str, Any]] = field(default_factory=list)
    answers: list[dict[str, Any]] = field(default_factory=list)
    messages: dict[tuple[str, str], OutboundMessage] = field(default_factory=dict)

    async def send(self, chat_id: str, message: OutboundMessage) -> str:
        mid = str(next(_ids))
        self.messages[(chat_id, mid)] = message
        self.sent.append({"chat_id": chat_id, "message_id": mid, "message": message})
        return mid

    async def edit(self, chat_id: str, message_id: str, message: OutboundMessage) -> None:
        self.messages[(chat_id, message_id)] = message
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "message": message})

    async def answer(
        self, callback_id: str, text: str | None = None, *, alert: bool = False
    ) -> None:
        self.answers.append({"callback_id": callback_id, "text": text, "alert": alert})

    def last(self, chat_id: str) -> OutboundMessage:
        for m in reversed(self.sent):
            if m["chat_id"] == chat_id:
                current = self.messages[(chat_id, m["message_id"])]
                return current
        raise KeyError(chat_id)

    def buttons(self, chat_id: str, message_id: str) -> list[tuple[str, str | None]]:
        msg = self.messages[(chat_id, message_id)]
        return [(b.label, b.data) for row in msg.rows for b in row]
