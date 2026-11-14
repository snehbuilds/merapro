"""WhatsApp Cloud API channel (P2-M4, docs/07 §4): the ``ApprovalChannel`` for WhatsApp.

The bot runtime renders the same messages for every channel (HTML text and rows of
buttons, made for Telegram). This adapter translates them both ways under WhatsApp's
rules, verified against Meta's Cloud API docs (messages, interactive, templates,
webhooks); re-verify on change:

- **Buttons**: up to three become reply buttons; more become one list (up to ten rows,
  "Choose" as its button). URL buttons become links in the text. Titles are cut to the
  platform's 20 (buttons) and 24 (rows) characters.
- **No edits**: WhatsApp cannot change a sent message. A card the runtime would edit to a
  final state ("Approved", "Expired") becomes a short follow-up; intermediate edits are
  dropped, since a fresh tap always re-checks the state server-side.
- **The 24-hour window**: outside it only a pre-approved template may be sent. The
  adapter asks ``window_open`` before every send; when closed, it sends the
  ``mera_approval_waiting`` (cards) or ``mera_update`` (everything else) template with the
  first lines as its one variable and a "Show me" quick reply. Tapping it opens the window,
  and the runtime then sends the full card (docs/07 §4.2).
- **Inbound**: ``updates_from_webhook`` turns a webhook payload into the update shape the
  runtime handles (message / callback_query), so pairing, decisions, ``/pin``, edit by
  reply and commands are the same code as Telegram's.
- **Signatures**: ``valid_signature`` checks ``X-Hub-Signature-256`` (HMAC-SHA256 of the
  raw body with the app secret) in constant time, before anything is parsed.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import re
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import httpx

from mera.domain.ports.approval_channel import (
    Button,
    ChannelConstraints,
    ChannelError,
    OutboundMessage,
)

GRAPH = "https://graph.facebook.com/v23.0"
REOPEN = "o"  # the quick reply on templates: "send me the full card"
CARD_TEMPLATE = "mera_approval_waiting"
UPDATE_TEMPLATE = "mera_update"
DIGEST_TEMPLATE = "mera_daily_digest"  # P3-M4.2

WHATSAPP = ChannelConstraints(
    max_inline_buttons=3,
    supports_button_rows=False,
    supports_list_picker=True,
    supports_message_edit=False,
    supports_media_groups=False,
    max_body_chars=1024,
    requires_templates_outside_window=True,
    session_window_hours=24,
    supports_force_reply=False,
    per_message_cost=Decimal("0.01"),
    max_callback_bytes=256,
)

_TAG = re.compile(r"<[^>]+>")
_A = re.compile(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_FINAL = ("✅", "❌", "⏳", "🛑", "🚫", "🗑")


def to_whatsapp_text(text: str) -> str:
    """Telegram HTML → WhatsApp's markdown-ish formatting, entities unescaped."""
    out = _A.sub(lambda m: f"{m.group(2)} ({m.group(1)})", text)
    for tag, mark in (("b", "*"), ("strong", "*"), ("i", "_"), ("em", "_"), ("s", "~")):
        out = re.sub(rf"</?{tag}>", mark, out, flags=re.IGNORECASE)
    out = re.sub(r"</?code>", "```", out, flags=re.IGNORECASE)
    out = _TAG.sub("", out)
    return html.unescape(out).strip()


def one_line(text: str, limit: int = 900) -> str:
    """A template variable: no newlines, tabs or runs of four spaces (Meta refuses them)."""
    flat = re.sub(r"\s+", " ", to_whatsapp_text(text)).strip()
    return flat[: limit - 1] + "…" if len(flat) > limit else flat


INTERACTIVE_BODY = 1024


def interactive(message: OutboundMessage) -> dict[str, Any] | None:
    """The interactive part for a message's buttons, or None for plain text."""
    buttons = [b for row in message.rows for b in row if b.data]
    if not buttons:
        return None
    body = {"text": to_whatsapp_text(message.text)[:INTERACTIVE_BODY] or "…"}
    if len(buttons) <= 3:
        return {
            "type": "button",
            "body": body,
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b.data, "title": _title(b.label, 20)}}
                    for b in buttons
                ]
            },
        }
    rows = [{"id": b.data, "title": _title(b.label, 24)} for b in buttons[:10]]
    return {
        "type": "list",
        "body": body,
        "action": {"button": "Choose", "sections": [{"title": "Options", "rows": rows}]},
    }


def _title(label: str, limit: int) -> str:
    clean = to_whatsapp_text(label)
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def links(message: OutboundMessage) -> str:
    urls = [f"{to_whatsapp_text(b.label)}: {b.url}" for row in message.rows for b in row if b.url]
    return ("\n\n" + "\n".join(urls)) if urls else ""


def valid_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    # Bytes, not str: compare_digest refuses non-ASCII text, and a header is attacker input.
    return hmac.compare_digest(expected.encode(), header.removeprefix("sha256=").encode())


def updates_from_webhook(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """WhatsApp webhook → runtime updates. Delivery statuses are ignored."""
    out: list[dict[str, Any]] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for m in value.get("messages") or []:
                wa, mid = str(m.get("from") or ""), str(m.get("id") or "")
                if not wa or not mid:
                    continue
                context = str((m.get("context") or {}).get("id") or "")
                kind = m.get("type")
                data: str | None = None
                if kind == "interactive":
                    inter = m.get("interactive") or {}
                    picked = inter.get("button_reply") or inter.get("list_reply") or {}
                    data = picked.get("id")
                elif kind == "button":
                    data = (m.get("button") or {}).get("payload")
                if data:
                    out.append(
                        {
                            "update_id": mid,
                            "callback_query": {
                                "id": f"{wa}|{mid}",
                                "data": str(data)[:256],
                                "message": {"message_id": context, "chat": {"id": wa}},
                            },
                        }
                    )
                elif kind == "text":
                    msg: dict[str, Any] = {
                        "message_id": mid,
                        "chat": {"id": wa, "type": "private"},
                        "text": str((m.get("text") or {}).get("body") or "")[:4096],
                    }
                    if context:
                        msg["reply_to_message"] = {"message_id": context}
                    out.append({"update_id": mid, "message": msg})
    return out


WindowCheck = Callable[[str], Awaitable[bool]]
SendHook = Callable[[str, str, str | None], Awaitable[None]]  # chat, kind, template


class WhatsAppChannel:
    channel_type = "whatsapp"
    constraints = WHATSAPP

    def __init__(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        window_open: WindowCheck,
        on_send: SendHook | None = None,
        reserve_template: Callable[[str, str], Awaitable[bool]] | None = None,
        language: str = "en",
        transport: httpx.AsyncBaseTransport | None = None,
        graph_url: str = GRAPH,
    ) -> None:
        self._url = f"{graph_url.rstrip('/')}/{phone_number_id}/messages"
        self._token = access_token
        self._window = window_open
        self._on_send = on_send
        self._reserve_template = reserve_template
        self._lang = language
        self._t = transport

    async def _post(self, body: dict[str, Any]) -> str:
        try:
            async with httpx.AsyncClient(timeout=20, transport=self._t) as c:
                resp = await c.post(
                    self._url,
                    json={"messaging_product": "whatsapp", **body},
                    headers={"Authorization": f"Bearer {self._token}"},
                )
        except httpx.HTTPError as exc:
            raise ChannelError(f"could not reach WhatsApp ({type(exc).__name__})") from exc
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            err = (data.get("error") or {}) if isinstance(data, dict) else {}
            raise ChannelError(
                f"WhatsApp refused the message ({err.get('code', resp.status_code)})"
            )
        ids = data.get("messages") or [{}]
        return str(ids[0].get("id") or "")

    async def _template(self, chat_id: str, message: OutboundMessage) -> str:
        head = message.text.lstrip()
        name = (
            CARD_TEMPLATE
            if head.startswith("📋")
            else DIGEST_TEMPLATE
            if head.startswith("📬")
            else UPDATE_TEMPLATE
        )
        # Counted before it is sent, atomically, so concurrent sends cannot overshoot.
        if self._reserve_template is not None and not await self._reserve_template(chat_id, name):
            raise ChannelError("the monthly WhatsApp template budget is spent")
        mid = await self._post(
            {
                "to": chat_id,
                "type": "template",
                "template": {
                    "name": name,
                    "language": {"code": self._lang},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": one_line(message.text)}],
                        },
                        {
                            "type": "button",
                            "sub_type": "quick_reply",
                            "index": "0",
                            "parameters": [{"type": "payload", "payload": REOPEN}],
                        },
                    ],
                },
            }
        )
        if self._on_send:
            await self._on_send(chat_id, "template", name)
        return mid

    async def send(self, chat_id: str, message: OutboundMessage) -> str:
        if not await self._window(chat_id):
            return await self._template(chat_id, message)
        inter = interactive(message)
        if inter is not None:
            full = to_whatsapp_text(message.text) + links(message)
            if len(full) > INTERACTIVE_BODY:
                # An interactive body holds 1024 characters: the card goes first, whole, as
                # text, and the buttons follow it. A cut card is never what is approved.
                await self._post(
                    {
                        "to": chat_id,
                        "type": "text",
                        "text": {"body": full[:4096], "preview_url": False},
                    }
                )
                inter["body"]["text"] = "⬆️ Choose for the message above."
            else:
                inter["body"]["text"] = full or "…"
            body: dict[str, Any] = {"to": chat_id, "type": "interactive", "interactive": inter}
        else:
            text = (to_whatsapp_text(message.text) + links(message))[:4096] or "…"
            body = {"to": chat_id, "type": "text", "text": {"body": text, "preview_url": False}}
        if message.reply_to_message_id:
            body["context"] = {"message_id": message.reply_to_message_id}
        mid = await self._post(body)
        if self._on_send:
            await self._on_send(chat_id, "session", None)
        return mid

    async def edit(self, chat_id: str, message_id: str, message: OutboundMessage) -> None:
        # No edits on WhatsApp: only a final state is worth a follow-up message.
        if not any(
            b.data for row in message.rows for b in row
        ) and message.text.lstrip().startswith(_FINAL):
            head = to_whatsapp_text(message.text).splitlines()[0] if message.text else ""
            if head:
                await self.send(
                    chat_id,
                    OutboundMessage(text=head, reply_to_message_id=message_id or None),
                )

    async def answer(
        self, callback_id: str, text: str | None = None, *, alert: bool = False
    ) -> None:
        # No toasts on WhatsApp: a meaningful acknowledgement becomes a short message.
        if not text:
            return
        chat_id = callback_id.split("|", 1)[0]
        if chat_id:
            await self.send(chat_id, OutboundMessage(text=text))


def reopen_button() -> Button:
    return Button("Show me", REOPEN)
