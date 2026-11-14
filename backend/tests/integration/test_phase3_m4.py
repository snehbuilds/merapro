"""P3-M4 daily digests and outbound webhooks against real Postgres."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from mera.application.digests import Digests
from mera.application.services.webhook_service import WebhookService
from tests.integration.test_accounts_m3 import _app, _setup
from tests.integration.test_approvals_m6 import _submit
from tests.integration.test_drafting_m5 import CapturingDeferrer, deferrer  # noqa: F401
from tests.integration.test_identity_m2 import _h
from tests.integration.test_ingest_m4 import _sm
from tests.integration.test_publishing_m8 import _four, quiet  # noqa: F401
from tests.integration.test_telegram_m7 import _bot, _pair

pytestmark = pytest.mark.integration


async def test_digest_only_members_get_one_list_a_day_instead_of_cards(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "digest")
    bot, ch = _bot(api)
    chat = f"c{uuid.uuid4().hex[:8]}"
    await _pair(api, bot, s, ws, chat)
    r = await api.put(
        f"/api/v1/workspaces/{ws}/members/me/digest",
        headers=_h(s),
        json={"mode": "digest", "time": "09:00"},
    )
    assert r.json() == {"mode": "digest", "time": "09:00"}
    bad = await api.put(
        f"/api/v1/workspaces/{ws}/members/me/digest", headers=_h(s), json={"mode": "hourly"}
    )
    assert bad.json()["error"]["code"] == "invalid_mode"
    ps, _acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    assert await bot.dispatch("card", uuid.UUID(ws), uuid.UUID(req["id"])) == 0  # no card
    digests = Digests(_sm(api), [bot])
    tz = ZoneInfo((await api.get(f"/api/v1/workspaces/{ws}", headers=_h(s))).json()["timezone"])
    early = datetime(2026, 11, 13, 7, 0, tzinfo=tz)  # before the member's 09:00
    assert (await digests.run_workspace(uuid.UUID(ws), early)).get("members", 0) == 0
    at_nine = early + timedelta(hours=2)
    out = await digests.run_workspace(uuid.UUID(ws), at_nine)
    assert out["members"] == 1
    assert out["chats"] == 1
    msg = ch.last(chat)
    assert msg.text.startswith("📬")
    assert "1 set, 4 posts" in msg.text
    assert any(b.data and b.data.startswith("q:") for row in msg.rows for b in row)
    # Once a day.
    assert (await digests.run_workspace(uuid.UUID(ws), at_nine + timedelta(hours=1))).get(
        "members", 0
    ) == 0


async def test_webhooks_are_signed_retried_and_switched_off(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "hooks")
    seen: list[httpx.Request] = []
    status = {"code": 200}

    def receiver(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status["code"])

    svc: WebhookService = _app(api).state.webhook_service
    saved = svc._t
    svc._t = httpx.MockTransport(receiver)
    try:
        r = await api.post(
            f"/api/v1/workspaces/{ws}/webhooks",
            headers=_h(s),
            json={"url": "https://hooks.example/mera", "events": ["approval.requested"]},
        )
        assert r.status_code == 201, r.text
        hook = r.json()
        secret = hook["secret"]
        assert secret.startswith("whsec_")
        listed = (await api.get(f"/api/v1/workspaces/{ws}/webhooks", headers=_h(s))).json()
        assert all(x["secret"] is None for x in listed["data"])  # shown once only
        bad = await api.post(
            f"/api/v1/workspaces/{ws}/webhooks",
            headers=_h(s),
            json={"url": "https://hooks.example/y", "events": ["everything.ever"]},
        )
        assert bad.json()["error"]["code"] == "invalid_events"
        ps, _acc, _ = await _four(api, s, ws, deferrer)
        await _submit(api, s, ws, ps["id"])
        now = datetime.now(UTC)
        queued, sent, _failed = await svc.run_workspace(uuid.UUID(ws), now)
        assert (queued, sent) == (1, 1)
        req = seen[-1]
        body = req.content
        t, v1 = (p.split("=", 1)[1] for p in req.headers["mera-signature"].split(","))
        expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
        assert hmac.compare_digest(v1, expected)
        payload = json.loads(body)
        assert payload["type"] == "approval.requested"
        assert payload["workspace_id"] == ws
        assert req.headers["mera-event"] == "approval.requested"
        # A failing receiver: retried with backoff, then the endpoint is switched off.
        status["code"] = 500
        ping = await api.post(f"/api/v1/workspaces/{ws}/webhooks/{hook['id']}/test", headers=_h(s))
        assert ping.json()["state"] == "failed"
        assert ping.json()["last_status"] == 500
        from mera.application.services import webhook_service as wh

        for _ in range(wh.DISABLE_AFTER):
            await api.post(f"/api/v1/workspaces/{ws}/webhooks/{hook['id']}/test", headers=_h(s))
        after = (await api.get(f"/api/v1/workspaces/{ws}/webhooks", headers=_h(s))).json()
        assert after["data"][0]["active"] is False
        assert "in a row failed" in after["data"][0]["disabled_reason"]
        deliveries = (
            await api.get(
                f"/api/v1/workspaces/{ws}/webhooks/{hook['id']}/deliveries", headers=_h(s)
            )
        ).json()["data"]
        assert deliveries
        assert deliveries[-1]["state"] == "delivered"
    finally:
        svc._t = saved
