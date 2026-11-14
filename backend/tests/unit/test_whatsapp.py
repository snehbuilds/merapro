"""WhatsApp adapter translations (P2-M4, docs/07 §4): buttons, lists, text, signatures and
webhook normalisation. No network; the channel's sends go to an httpx MockTransport."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest

from mera.domain.ports.approval_channel import Button, ChannelError, OutboundMessage
from mera.infrastructure.channels.whatsapp import (
    CARD_TEMPLATE,
    REOPEN,
    UPDATE_TEMPLATE,
    WhatsAppChannel,
    interactive,
    one_line,
    to_whatsapp_text,
    updates_from_webhook,
    valid_signature,
)


def test_text_translation() -> None:
    assert to_whatsapp_text("<b>Bold</b> and <i>it</i> &amp; <s>no</s>") == "*Bold* and _it_ & ~no~"
    assert (
        to_whatsapp_text('<a href="https://x.example/a">Open</a>') == "Open (https://x.example/a)"
    )
    assert one_line("a\n\nb\tc    d") == "a b c d"
    assert len(one_line("x" * 2000)) == 900


def test_three_buttons_are_reply_buttons_more_become_a_list() -> None:
    three = OutboundMessage(
        text="📋 <b>Approval needed</b>",
        rows=[[Button("✅ Approve all", "a"), Button("❌ Reject", "r")], [Button("✏️ Edit", "e")]],
    )
    out = interactive(three)
    assert out is not None
    assert out["type"] == "button"
    assert [b["reply"]["id"] for b in out["action"]["buttons"]] == ["a", "r", "e"]
    many = OutboundMessage(
        text="Pick",
        rows=[[Button(f"Account number {i} with a long name", f"d{i}")] for i in range(12)],
    )
    out = interactive(many)
    assert out is not None
    assert out["type"] == "list"
    rows = out["action"]["sections"][0]["rows"]
    assert len(rows) == 10  # WhatsApp's list ceiling
    assert all(len(r["title"]) <= 24 for r in rows)
    assert interactive(OutboundMessage(text="plain")) is None


def test_signature_is_checked_over_the_raw_body() -> None:
    body = b'{"object":"whatsapp_business_account"}'
    sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert valid_signature("secret", body, sig)
    assert not valid_signature("secret", body + b" ", sig)
    assert not valid_signature("other", body, sig)
    assert not valid_signature("secret", body, None)
    assert not valid_signature("secret", body, sig.removeprefix("sha256="))


def _payload(*messages: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {"messages": list(messages), "statuses": [{}]}}]}],
    }


def test_webhook_becomes_runtime_updates() -> None:
    ups = updates_from_webhook(
        _payload(
            {"from": "4915", "id": "wamid.1", "type": "text", "text": {"body": "/queue"}},
            {
                "from": "4915",
                "id": "wamid.2",
                "type": "interactive",
                "context": {"id": "wamid.card"},
                "interactive": {"type": "button_reply", "button_reply": {"id": "v:a:b:1"}},
            },
            {"from": "4915", "id": "wamid.3", "type": "button", "button": {"payload": REOPEN}},
            {"from": "4915", "id": "wamid.4", "type": "image"},  # ignored
            {"id": "wamid.5", "type": "text"},  # no sender: ignored
        )
    )
    assert [u["update_id"] for u in ups] == ["wamid.1", "wamid.2", "wamid.3"]
    assert ups[0]["message"]["text"] == "/queue"
    cq = ups[1]["callback_query"]
    assert cq["id"] == "4915|wamid.2"
    assert cq["data"] == "v:a:b:1"
    assert cq["message"] == {"message_id": "wamid.card", "chat": {"id": "4915"}}
    assert ups[2]["callback_query"]["data"] == REOPEN
    assert updates_from_webhook({"entry": [{"changes": [{"value": {"statuses": [{}]}}]}]}) == []


class _Graph:
    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok"
        self.bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"messages": [{"id": f"wamid.{len(self.bodies)}"}]})


def _channel(graph: _Graph, *, open_: bool, budget: bool = True) -> tuple[WhatsAppChannel, list]:
    log: list[tuple[str, str, str | None]] = []

    async def window(_chat: str) -> bool:
        return open_

    async def on_send(chat: str, kind: str, template: str | None) -> None:
        log.append((chat, kind, template))

    async def can_template(_chat: str, _name: str) -> bool:
        return budget

    ch = WhatsAppChannel(
        phone_number_id="123",
        access_token="tok",
        window_open=window,
        on_send=on_send,
        reserve_template=can_template,
        transport=httpx.MockTransport(graph),
    )
    return ch, log


async def test_inside_the_window_sends_interactive_outside_sends_a_template() -> None:
    graph = _Graph()
    ch, log = _channel(graph, open_=True)
    card = OutboundMessage(
        text="📋 <b>Approval needed</b>\nTech", rows=[[Button("✅ Approve", "a")]]
    )
    assert await ch.send("4915", card) == "wamid.1"
    assert graph.bodies[0]["type"] == "interactive"
    assert graph.bodies[0]["messaging_product"] == "whatsapp"
    assert log == [("4915", "session", None)]

    graph2 = _Graph()
    closed, log2 = _channel(graph2, open_=False)
    await closed.send("4915", card)
    await closed.send("4915", OutboundMessage(text="✅ Published 3 of 3"))
    names = [b["template"]["name"] for b in graph2.bodies]
    assert names == [CARD_TEMPLATE, UPDATE_TEMPLATE]
    params = graph2.bodies[0]["template"]["components"]
    assert "\n" not in params[0]["parameters"][0]["text"]
    assert params[1]["parameters"][0]["payload"] == REOPEN
    assert [k for _, k, _ in log2] == ["template", "template"]


async def test_a_card_too_long_for_buttons_goes_first_in_full() -> None:
    """An interactive body holds 1024 characters; the card is never cut to fit it: the
    whole card goes as text, then the buttons (release review #5)."""
    graph = _Graph()
    ch, _log = _channel(graph, open_=True)
    long_text = "📋 <b>Approval needed</b>\n" + "word " * 400 + "THE-END"
    card = OutboundMessage(text=long_text, rows=[[Button("✅ Approve", "a")]])
    await ch.send("4915", card)
    first, second = graph.bodies
    assert first["type"] == "text"
    assert first["text"]["body"].endswith("THE-END")
    assert second["type"] == "interactive"
    assert len(second["interactive"]["body"]["text"]) < 100
    assert second["interactive"]["action"]["buttons"][0]["reply"]["id"] == "a"


def test_cut_cards_offer_no_approve() -> None:
    from mera.application.channels import render

    assert render.reply_fits("x" * render.REPLY_SHOWN)
    assert not render.reply_fits("x" * (render.REPLY_SHOWN + 1))
    long = render.reply_card(
        workspace="Acme",
        platform="threads",
        account="Tech",
        handle="tech",
        author="sam",
        comment="hi",
        reply="y" * 2000,
    )
    assert "open it in the web to approve" in long
    big = {"text": "z" * 3000}
    assert not render.tool_args_fit(big)
    assert "open it in the web to approve" in render.tool_call_card(
        workspace="Acme",
        server="Notion",
        tool="create_page",
        risk="write",
        agent="x",
        arguments=big,
    )
    assert render.tool_args_fit({"text": "short"})


async def test_spent_budget_refuses_templates() -> None:
    ch, _ = _channel(_Graph(), open_=False, budget=False)
    with pytest.raises(ChannelError, match="budget"):
        await ch.send("4915", OutboundMessage(text="📋 Approval needed"))


async def test_edits_become_follow_ups_only_for_final_states() -> None:
    graph = _Graph()
    ch, _ = _channel(graph, open_=True)
    await ch.edit(
        "4915", "wamid.card", OutboundMessage(text="Choose an account", rows=[[Button("x", "y")]])
    )
    assert graph.bodies == []
    await ch.edit("4915", "wamid.card", OutboundMessage(text="✅ <b>Approved</b> by Ana\nmore"))
    assert graph.bodies[0]["text"]["body"] == "✅ *Approved* by Ana"
    assert graph.bodies[0]["context"] == {"message_id": "wamid.card"}
    await ch.answer("4915|wamid.9", "Approved.")
    assert graph.bodies[-1]["to"] == "4915"
    await ch.answer("4915|wamid.9", None)
    assert len(graph.bodies) == 2


async def test_graph_errors_become_channel_errors() -> None:
    async def window(_c: str) -> bool:
        return True

    ch = WhatsAppChannel(
        phone_number_id="1",
        access_token="tok",
        window_open=window,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(400, json={"error": {"code": 131047}})
        ),
    )
    with pytest.raises(ChannelError, match="131047"):
        await ch.send("4915", OutboundMessage(text="hi"))
