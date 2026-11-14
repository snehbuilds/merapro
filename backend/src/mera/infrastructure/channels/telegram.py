"""Telegram Bot API client (M7.2): the ``ApprovalChannel`` for Telegram.

Only what the approval loop needs: ``sendMessage`` (HTML parse mode, inline keyboards,
``ForceReply``), ``editMessageText``, ``answerCallbackQuery``, ``setWebhook`` with a secret
token, and ``getUpdates`` for local long polling (M7.16). The bot token is never logged:
errors carry Telegram's ``description`` only.
"""

from __future__ import annotations

from typing import Any

import httpx

from mera.domain.ports.approval_channel import (
    TELEGRAM,
    ChannelConstraints,
    ChannelError,
    OutboundMessage,
)

API = "https://api.telegram.org"


def keyboard(message: OutboundMessage) -> dict[str, Any] | None:
    if message.force_reply:
        out: dict[str, Any] = {"force_reply": True, "selective": True}
        if message.placeholder:
            out["input_field_placeholder"] = message.placeholder[:64]
        return out
    if not message.rows:
        return None
    return {
        "inline_keyboard": [
            [
                {
                    "text": b.label,
                    **({"url": b.url} if b.url else {"callback_data": b.data or "noop"}),
                }
                for b in row
            ]
            for row in message.rows
        ]
    }


class TelegramChannel:
    channel_type = "telegram"
    constraints: ChannelConstraints = TELEGRAM

    def __init__(self, token: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base = f"{API}/bot{token}"
        self._transport = transport

    async def call(self, method: str, payload: dict[str, Any], *, wait: float = 30.0) -> Any:
        try:
            async with httpx.AsyncClient(timeout=wait, transport=self._transport) as c:
                resp = await c.post(f"{self._base}/{method}", json=payload)
        except httpx.HTTPError as exc:
            raise ChannelError(f"could not reach Telegram ({type(exc).__name__})") from exc
        try:
            body = resp.json()
        except ValueError as exc:
            raise ChannelError(f"Telegram answered {resp.status_code}") from exc
        if not body.get("ok"):
            raise ChannelError(
                str(body.get("description") or f"Telegram answered {resp.status_code}")[:200]
            )
        return body.get("result")

    async def send(self, chat_id: str, message: OutboundMessage) -> str:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": message.text[:4096],
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
            "disable_notification": message.silent,
        }
        kb = keyboard(message)
        if kb:
            payload["reply_markup"] = kb
        if message.reply_to_message_id:
            payload["reply_parameters"] = {
                "message_id": int(message.reply_to_message_id),
                "allow_sending_without_reply": True,
            }
        result = await self.call("sendMessage", payload)
        return str(result["message_id"])

    async def edit(self, chat_id: str, message_id: str, message: OutboundMessage) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "text": message.text[:4096],
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
            "reply_markup": keyboard(message) or {"inline_keyboard": []},
        }
        try:
            await self.call("editMessageText", payload)
        except ChannelError as exc:
            if "message is not modified" in str(exc):
                return
            raise

    async def answer(
        self, callback_id: str, text: str | None = None, *, alert: bool = False
    ) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_id, "show_alert": alert}
        if text:
            payload["text"] = text[:200]
        try:
            await self.call("answerCallbackQuery", payload)
        except ChannelError:
            return  # a late answer is harmless; Telegram stops the spinner on its own

    async def set_webhook(self, url: str, secret: str) -> None:
        await self.call(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret,
                "allowed_updates": ["message", "callback_query"],
                "drop_pending_updates": False,
            },
        )

    async def delete_webhook(self) -> None:
        await self.call("deleteWebhook", {"drop_pending_updates": False})

    async def get_updates(self, offset: int | None, poll: int = 25) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": poll,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = await self.call("getUpdates", payload, wait=poll + 10)
        return list(result or [])

    async def me(self) -> dict[str, Any]:
        result = await self.call("getMe", {})
        return dict(result or {})
