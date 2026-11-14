"""P2-M4 WhatsApp approvals against real Postgres (docs/07 §4, docs/13 P2-M4 acceptance).

The WhatsApp runtime is the same ``BotRuntime`` as Telegram's over a recording
``FakeChannel`` with WhatsApp's constraints; updates go in through ``handle_whatsapp``,
the worker's entry point, in the webhook's own shape."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pydantic
import pytest
from sqlalchemy import select

from mera.application.channels.bot import BotRuntime
from mera.application.channels.whatsapp_state import WhatsAppState
from mera.bots.main import create_bots_app
from mera.domain.context import WorkspaceContext
from mera.infrastructure.channels.fake import FakeChannel
from mera.infrastructure.channels.whatsapp import REOPEN, WHATSAPP
from mera.infrastructure.db.models import ApprovalRequest
from mera.infrastructure.db.session import workspace_transaction
from mera.workers.tasks.approvals import handle_whatsapp
from tests.integration.test_accounts_m3 import _app, _setup
from tests.integration.test_approvals_m6 import _submit
from tests.integration.test_drafting_m5 import CapturingDeferrer, deferrer  # noqa: F401
from tests.integration.test_identity_m2 import _h
from tests.integration.test_ingest_m4 import _sm
from tests.integration.test_publishing_m8 import _four, quiet  # noqa: F401
from tests.integration.test_telegram_m7 import _bot, _pair

pytestmark = pytest.mark.integration


@pytest.fixture
def wa(api: httpx.AsyncClient) -> Iterator[tuple[BotRuntime, FakeChannel, WhatsAppState]]:
    """A WhatsApp runtime over a FakeChannel, plugged into the API's channel service."""
    app = _app(api)
    ch = FakeChannel(channel_type="whatsapp", constraints=WHATSAPP)
    bot = BotRuntime(
        _sm(api),
        ch,
        app.state.approval_service,
        public_url="https://mera.example",
        posts=app.state.post_service,
        governance=app.state.governance_service,
    )
    state = WhatsAppState(_sm(api), monthly_template_cap=1000)
    svc = app.state.channel_service
    original = svc._wa
    svc._wa = (bot, state)
    try:
        yield bot, ch, state
    finally:
        svc._wa = original


def _wa_number() -> str:
    return "49" + str(uuid.uuid4().int)[:10]


def _payload(*messages: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {"messages": list(messages)}}]}],
    }


def _text(chat: str, body: str) -> dict[str, Any]:
    return {"from": chat, "id": f"wamid.{uuid.uuid4().hex}", "type": "text", "text": {"body": body}}


def _tap(chat: str, card_id: str, data: str) -> dict[str, Any]:
    return {
        "from": chat,
        "id": f"wamid.{uuid.uuid4().hex}",
        "type": "interactive",
        "context": {"id": card_id},
        "interactive": {"type": "button_reply", "button_reply": {"id": data}},
    }


async def _wa_pair(
    api: httpx.AsyncClient,
    wa: tuple[BotRuntime, FakeChannel, WhatsAppState],
    s: dict[str, Any],
    ws: str,
    chat: str,
) -> None:
    bot, _ch, state = wa
    r = await api.post(
        f"/api/v1/workspaces/{ws}/approver-bindings/pairing-codes",
        headers=_h(s),
        json={"channel_type": "whatsapp"},
    )
    assert r.status_code == 201, r.text
    assert await handle_whatsapp(
        bot, state, _payload(_text(chat, f"/pair {r.json()['code']}"))
    ) == ["paired"]


async def test_pairing_is_bound_to_its_channel(
    api: httpx.AsyncClient, wa: tuple[BotRuntime, FakeChannel, WhatsAppState]
) -> None:
    bot, ch, state = wa
    s, ws = await _setup(api, "wapair")
    chat = _wa_number()
    # A Telegram code does not pair a WhatsApp chat.
    r = await api.post(f"/api/v1/workspaces/{ws}/approver-bindings/pairing-codes", headers=_h(s))
    code = r.json()["code"]
    assert await bot.pair(chat, code) == "invalid"
    await _wa_pair(api, wa, s, ws, chat)
    assert "Paired with Mera" in ch.last(chat).text
    r = await api.get(f"/api/v1/workspaces/{ws}/approver-bindings", headers=_h(s))
    assert [b["channel_type"] for b in r.json()["data"]] == ["whatsapp"]
    # The inbound message opened the 24-hour window.
    assert await state.window_open(chat)
    assert not await state.window_open(_wa_number())
    # Channel status reports WhatsApp as configured, with usage.
    r = await api.get(f"/api/v1/workspaces/{ws}/approval-channels", headers=_h(s))
    body = r.json()["whatsapp"]
    assert body["configured"] is True
    assert body["enabled"] is True
    assert set(body["usage"]) == {"template", "session", "cap"}
    # Unknown channels are refused at the edge.
    r = await api.post(
        f"/api/v1/workspaces/{ws}/approver-bindings/pairing-codes",
        headers=_h(s),
        json={"channel_type": "sms"},
    )
    assert r.status_code == 400


async def test_whatsapp_unavailable_when_not_configured(api: httpx.AsyncClient) -> None:
    s, ws = await _setup(api, "wanone")
    r = await api.post(
        f"/api/v1/workspaces/{ws}/approver-bindings/pairing-codes",
        headers=_h(s),
        json={"channel_type": "whatsapp"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "channel_unavailable"
    r = await api.get(f"/api/v1/workspaces/{ws}/approval-channels", headers=_h(s))
    assert r.json()["whatsapp"]["configured"] is False


async def test_card_and_decision_over_whatsapp(
    api: httpx.AsyncClient,
    wa: tuple[BotRuntime, FakeChannel, WhatsAppState],
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
) -> None:
    bot, ch, state = wa
    s, ws = await _setup(api, "wacard")
    chat = _wa_number()
    await _wa_pair(api, wa, s, ws, chat)
    # Paired on Telegram too: each channel's runtime reaches only its own chats.
    tg, tg_ch = _bot(api)
    tg_chat = f"c{uuid.uuid4().hex[:8]}"
    await _pair(api, tg, s, ws, tg_chat)
    ps, _acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    # The worker dispatches to Telegram first, then WhatsApp: a person paired on both gets
    # the card on both (a Telegram card must not count as "already sent" on WhatsApp).
    assert await tg.dispatch("card", uuid.UUID(ws), uuid.UUID(req["id"])) == 1
    assert await bot.dispatch("card", uuid.UUID(ws), uuid.UUID(req["id"])) == 1
    card = next(
        m for m in ch.sent if m["chat_id"] == chat and "Approval needed" in m["message"].text
    )
    assert all(m["chat_id"] != tg_chat for m in ch.sent)
    assert all(m["chat_id"] != chat for m in tg_ch.sent)
    approve = next(b.data for row in card["message"].rows for b in row if b.label.startswith("✅"))
    out = await handle_whatsapp(bot, state, _payload(_tap(chat, card["message_id"], approve)))
    assert out, out
    assert out[0] != "unpaired", out
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as sx:
        row = await sx.scalar(
            select(ApprovalRequest).where(ApprovalRequest.id == uuid.UUID(req["id"]))
        )
    assert row is not None
    assert row.state == "approved", row.state
    # Each runtime refreshes only its own channel's cards.
    await tg.dispatch("refresh", uuid.UUID(ws), uuid.UUID(req["id"]))
    await bot.dispatch("refresh", uuid.UUID(ws), uuid.UUID(req["id"]))
    assert all(m["chat_id"] != tg_chat for m in ch.sent)
    assert all(m["chat_id"] != chat for m in tg_ch.sent)
    assert any(m["chat_id"] == tg_chat for m in tg_ch.sent)
    # The same tap again is refused server-side; a duplicate delivery is processed once.
    tap = _tap(chat, card["message_id"], approve)
    first = await handle_whatsapp(bot, state, _payload(tap))
    assert await handle_whatsapp(bot, state, _payload(tap)) == ["duplicate"]
    assert first != ["duplicate"]


async def test_show_me_resends_the_open_cards(
    api: httpx.AsyncClient,
    wa: tuple[BotRuntime, FakeChannel, WhatsAppState],
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
) -> None:
    bot, ch, state = wa
    s, ws = await _setup(api, "wareopen")
    chat = _wa_number()
    await _wa_pair(api, wa, s, ws, chat)
    ps, _acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    await bot.dispatch("card", uuid.UUID(ws), uuid.UUID(req["id"]))
    before = sum(1 for m in ch.sent if "Approval needed" in m["message"].text)
    tapped = {
        "from": chat,
        "id": f"wamid.{uuid.uuid4().hex}",
        "type": "button",
        "button": {"payload": REOPEN},
    }
    assert await handle_whatsapp(bot, state, _payload(tapped)) == ["resent:1"]
    after = sum(1 for m in ch.sent if "Approval needed" in m["message"].text)
    assert after == before + 1


async def test_template_budget_is_counted(api: httpx.AsyncClient) -> None:
    sm = _sm(api)
    chat = _wa_number()
    state = WhatsAppState(sm, monthly_template_cap=0)
    assert not await state.can_send_template()
    assert not await state.reserve_template(chat, "mera_update")
    roomy = WhatsAppState(sm, monthly_template_cap=10_000)
    before = (await roomy.month_usage())["template"]
    assert await roomy.reserve_template(chat, "mera_update")
    await roomy.record_send(chat, "template", "mera_update")  # already counted
    await roomy.record_send(chat, "session", None)
    usage = await roomy.month_usage()
    assert usage["template"] == before + 1
    assert usage["cap"] == 10_000
    # Reservations are atomic: twenty at once against a budget with three left.
    import asyncio

    tight = WhatsAppState(sm, monthly_template_cap=usage["template"] + 3)
    granted = await asyncio.gather(
        *(tight.reserve_template(chat, "mera_update") for _ in range(20))
    )
    assert sum(granted) == 3


def _sig(secret: str, raw: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


async def test_webhook_handshake_signature_and_queueing() -> None:
    from mera.config import get_settings

    sink = CapturingDeferrer()
    settings = get_settings().model_copy(
        update={
            "whatsapp_app_secret": pydantic.SecretStr("app-secret"),
            "whatsapp_verify_token": pydantic.SecretStr("verify-me"),
        }
    )
    app = create_bots_app(settings, sink)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://bots"
    ) as c:
        url = "/api/v1/webhooks/whatsapp"
        q = {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "1158"}
        r = await c.get(url, params=q)
        assert r.status_code == 200
        assert r.text == "1158"
        r = await c.get(url, params={**q, "hub.verify_token": "nope"})
        assert r.status_code == 403
        raw = json.dumps(_payload(_text("4915", "/queue"))).encode()
        r = await c.post(url, content=raw, headers={"X-Hub-Signature-256": _sig("wrong", raw)})
        assert r.status_code == 401
        r = await c.post(url, content=raw)
        assert r.status_code == 401
        assert sink.calls == []
        r = await c.post(url, content=raw, headers={"X-Hub-Signature-256": _sig("app-secret", raw)})
        assert r.status_code == 200
        assert sink.calls[0]["task"] == "approvals:whatsapp_update"
        assert sink.calls[0]["queueing_lock"].startswith("wa:wamid.")
        # Delivery statuses are acknowledged and dropped.
        statuses = json.dumps(
            {
                "object": "whatsapp_business_account",
                "entry": [{"changes": [{"value": {"statuses": [{}]}}]}],
            }
        ).encode()
        r = await c.post(
            url, content=statuses, headers={"X-Hub-Signature-256": _sig("app-secret", statuses)}
        )
        assert r.status_code == 200
        assert len(sink.calls) == 1
