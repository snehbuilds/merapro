"""P2-M5 media generation against real Postgres with the sandbox generator (docs/13 P2-M5
acceptance): costs known in advance, ceilings refuse before any provider call, a result
attached to an approved post invalidates that account's approval only."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from mera.application.services.media_generation_service import MediaGenerationService
from mera.domain.context import WorkspaceContext
from mera.infrastructure.db.models import MediaAsset, MediaJob
from mera.infrastructure.db.session import workspace_transaction
from mera.infrastructure.media.generators import FakeMediaGenerator
from tests.integration.test_accounts_m3 import _app, _setup
from tests.integration.test_approvals_m6 import _decide, _publishable, _sets, _submit
from tests.integration.test_drafting_m5 import CapturingDeferrer, deferrer  # noqa: F401
from tests.integration.test_identity_m2 import PASSWORD, _h
from tests.integration.test_ingest_m4 import _sm
from tests.integration.test_publishing_m8 import _four, quiet  # noqa: F401

pytestmark = pytest.mark.integration
PIN = "482916"


class Media:
    def __init__(self, svc: MediaGenerationService, sink: CapturingDeferrer) -> None:
        self.svc = svc
        self.sink = sink

    @property
    def gen(self) -> FakeMediaGenerator:
        assert isinstance(self.svc._gen, FakeMediaGenerator)
        return self.svc._gen

    async def run_all(self) -> list[str]:
        """Play the media worker: run every queued job once (retries included)."""
        out = []
        while True:
            pending = [c for c in self.sink.calls if not c.get("ran")]
            if not pending:
                return out
            for c in pending:
                c["ran"] = True
                out.append(
                    await self.svc.run_job(uuid.UUID(c["workspace_id"]), uuid.UUID(c["job_id"]))
                )


@pytest.fixture
async def media(api: httpx.AsyncClient) -> AsyncIterator[Media]:
    svc: MediaGenerationService = _app(api).state.media_generation_service
    original_def, original_gen = svc._deferrer, svc._gen
    sink = CapturingDeferrer()
    svc._deferrer = sink
    svc._gen = FakeMediaGenerator()
    try:
        yield Media(svc, sink)
    finally:
        svc._deferrer, svc._gen = original_def, original_gen


def _ws_url(ws: str, path: str) -> str:
    return f"/api/v1/workspaces/{ws}/{path}"


async def _limits(api: httpx.AsyncClient, s: dict[str, Any], ws: str, **body: str) -> None:
    r = await api.put(_ws_url(ws, "media/limits"), headers=_h(s), json=body)
    assert r.status_code == 200, r.text


async def test_estimate_generate_run_and_spend(api: httpx.AsyncClient, media: Media) -> None:
    s, ws = await _setup(api, "mgen")
    r = await api.post(_ws_url(ws, "media/generate/estimate"), headers=_h(s), json={"count": 2})
    assert r.status_code == 200, r.text
    est = r.json()
    assert Decimal(est["cost_usd"]) == Decimal("0.084")
    assert est["needs_second_factor"] is False
    assert est["over_budget"] is False
    wide = (
        await api.post(
            _ws_url(ws, "media/generate/estimate"), headers=_h(s), json={"aspect": "landscape"}
        )
    ).json()
    assert Decimal(wide["cost_usd"]) == Decimal("0.063")

    r = await api.post(
        _ws_url(ws, "media/generate"),
        headers=_h(s),
        json={"prompt": "A lighthouse at dawn over a calm sea, watercolour"},
    )
    assert r.status_code == 202, r.text
    job = r.json()["data"][0]
    assert job["state"] == "queued"
    assert media.sink.calls[0]["task"] == "media:generate"
    assert media.gen.calls == []  # nothing is spent until the worker runs it
    assert await media.run_all() == ["succeeded"]
    done = (await api.get(_ws_url(ws, f"media/jobs/{job['id']}"), headers=_h(s))).json()
    assert done["state"] == "succeeded"
    assert Decimal(done["actual_cost_usd"]) == Decimal("0.042")
    asset = (await api.get(_ws_url(ws, f"media/{done['result_asset_id']}"), headers=_h(s))).json()
    assert asset["origin"] == "generated"
    spend = (await api.get(_ws_url(ws, "media/spend"), headers=_h(s))).json()
    assert Decimal(spend["month_spent_usd"]) == Decimal("0.042")
    assert spend["jobs"] == {"succeeded": 1}
    listed = (await api.get(_ws_url(ws, "media/jobs?state=succeeded"), headers=_h(s))).json()
    assert [j["id"] for j in listed["data"]] == [job["id"]]


async def test_ceiling_and_budget_refuse_before_any_provider_call(
    api: httpx.AsyncClient, media: Media
) -> None:
    s, ws = await _setup(api, "mceil")
    await _limits(api, s, ws, request_ceiling_usd="0.05", monthly_budget_usd="0.10")
    body = {"prompt": "A paper boat on a puddle", "count": 2}  # 0.084 > 0.05
    r = await api.post(_ws_url(ws, "media/generate"), headers=_h(s), json=body)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "second_factor_required"
    assert media.sink.calls == []
    # With a second factor, above the ceiling is allowed.
    assert (
        await api.put("/api/v1/auth/pin", headers=_h(s), json={"pin": PIN, "proof": PASSWORD})
    ).status_code == 204
    grant = (
        await api.post(
            _ws_url(ws, "second-factor"),
            headers=_h(s),
            json={"action": "video_generate", "method": "pin", "value": PIN},
        )
    ).json()["token"]
    r = await api.post(
        _ws_url(ws, "media/generate"),
        headers={**_h(s), "x-mera-second-factor": grant},
        json=body,
    )
    assert r.status_code == 202, r.text
    # The queued jobs count against the month: the next request would exceed 0.10.
    r = await api.post(
        _ws_url(ws, "media/generate"), headers=_h(s), json={"prompt": "A paper boat"}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "media_budget_spent"
    assert len(media.sink.calls) == 2
    assert media.gen.calls == []


async def test_screening_video_cancel_and_refusals(api: httpx.AsyncClient, media: Media) -> None:
    s, ws = await _setup(api, "mscreen")
    r = await api.post(
        _ws_url(ws, "media/generate"), headers=_h(s), json={"prompt": "a nude portrait"}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unsafe_prompt"
    r = await api.post(
        _ws_url(ws, "media/generate"), headers=_h(s), json={"prompt": "a cat", "kind": "video"}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "video_disabled"
    # A cancelled job is never sent.
    job = (
        await api.post(_ws_url(ws, "media/generate"), headers=_h(s), json={"prompt": "a fox"})
    ).json()["data"][0]
    r = await api.post(_ws_url(ws, f"media/jobs/{job['id']}/cancel"), headers=_h(s))
    assert r.json()["state"] == "cancelled"
    assert await media.run_all() == ["cancelled"]
    assert media.gen.calls == []
    # A provider refusal fails at once; it is not retried.
    job = (
        await api.post(
            _ws_url(ws, "media/generate"), headers=_h(s), json={"prompt": "a fox [[refuse]]"}
        )
    ).json()["data"][0]
    assert await media.run_all() == ["failed"]
    failed = (await api.get(_ws_url(ws, f"media/jobs/{job['id']}"), headers=_h(s))).json()
    assert failed["attempt_count"] == 1
    assert "refused" in failed["error"]


async def test_regenerating_an_approved_post_invalidates_only_that_account(
    api: httpx.AsyncClient,
    media: Media,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
) -> None:
    s, ws = await _setup(api, "mregen")
    ps, _acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    r = await _decide(api, s, ws, req, "approve", None)
    assert r.json()["state"] == "approved"
    first, other = req["variants"][0]["post_variant_id"], req["variants"][1]["post_variant_id"]
    assert await _publishable(api, ws, first) is None

    r = await api.post(
        _sets(ws, ps["id"], f"/variants/{first}/media/regenerate"),
        headers=_h(s),
        json={"instruction": "warmer colours"},
    )
    assert r.status_code == 202, r.text
    job = r.json()["data"][0]
    assert job["post_variant_id"] == first
    assert job["alt_text"]  # the Media agent wrote the prompt and the alt text
    assert await media.run_all() == ["succeeded"]
    now = (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()
    v = next(x for x in now["variants"] if x["id"] == first)
    assert len(v["current_version"]["media"]) == 1
    assert v["current_version"]["media"][0]["alt_text"] == job["alt_text"]
    # That account's approval no longer holds; the others' still do.
    assert await _publishable(api, ws, first) is not None
    assert await _publishable(api, ws, other) is None


async def test_carousel_attaches_once_in_order_and_never_over_newer_work(
    api: httpx.AsyncClient,
    media: Media,
    deferrer: CapturingDeferrer,
) -> None:
    s, ws = await _setup(api, "mcarousel")
    ps, _acc, _ = await _four(api, s, ws, deferrer)
    vid = ps["variants"][0]["id"]
    r = await api.post(
        _ws_url(ws, "media/generate"),
        headers=_h(s),
        json={"post_variant_id": vid, "count": 3, "attach": True},
    )
    assert r.status_code == 202, r.text
    jobs = r.json()["data"]
    assert [j["slot"] for j in jobs] == [0, 1, 2]
    assert len({j["batch_id"] for j in jobs}) == 1
    assert len({j["prompt"] for j in jobs}) >= 1
    assert await media.run_all() == ["succeeded"] * 3
    now = (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()
    v = next(x for x in now["variants"] if x["id"] == vid)
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        results = {
            str(j.result_asset_id): j.slot
            for j in (
                await tx.execute(
                    select(MediaJob).where(MediaJob.batch_id == uuid.UUID(jobs[0]["batch_id"]))
                )
            ).scalars()
        }
        masters = {
            str(a.id): str(a.parent_asset_id)
            for a in (await tx.execute(select(MediaAsset))).scalars()
            if a.parent_asset_id is not None
        }
    order = [results[masters[m["asset_id"]]] for m in v["current_version"]["media"]]
    assert order == [0, 1, 2]
    versions_after = v["current_version"]["version_no"]

    # A result whose post changed in the meantime stays in the library.
    r = await api.post(
        _ws_url(ws, "media/generate"),
        headers=_h(s),
        json={"post_variant_id": vid, "prompt": "a red kite", "attach": True},
    )
    late = r.json()["data"][0]
    r = await api.put(
        _sets(ws, ps["id"], f"/variants/{vid}/media"),
        headers=_h(s),
        json={"media": [], "based_on_version_id": v["current_version"]["id"]},
    )
    assert r.status_code == 200, r.text
    assert await media.run_all() == ["succeeded"]
    again = (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()
    v2 = next(x for x in again["variants"] if x["id"] == vid)
    assert v2["current_version"]["media"] == []
    assert v2["current_version"]["version_no"] == versions_after + 1
    kept = (await api.get(_ws_url(ws, f"media/jobs/{late['id']}"), headers=_h(s))).json()
    assert kept["state"] == "succeeded"
    assert "not attached" in (kept["error"] or "")


async def test_regenerate_from_the_card_asks_again(
    api: httpx.AsyncClient,
    media: Media,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
) -> None:
    from mera.application.channels.bot import BotRuntime
    from mera.infrastructure.channels.fake import FakeChannel
    from tests.integration.test_telegram_m7 import _card, _pair, _tap

    s, ws = await _setup(api, "mbot")
    ps, _acc, _ = await _four(api, s, ws, deferrer)
    vid = ps["variants"][0]["id"]
    # Give the first account an image, then ask for approval.
    await api.post(
        _ws_url(ws, "media/generate"),
        headers=_h(s),
        json={"post_variant_id": vid, "prompt": "a lighthouse", "attach": True},
    )
    assert await media.run_all() == ["succeeded"]
    app = _app(api)
    ch = FakeChannel()
    bot = BotRuntime(
        _sm(api),
        ch,
        app.state.approval_service,
        public_url="https://mera.example",
        posts=app.state.post_service,
        governance=app.state.governance_service,
        media=media.svc,
    )
    chat = f"c{uuid.uuid4().hex[:8]}"
    await _pair(api, bot, s, ws, chat)
    req = await _submit(api, s, ws, ps["id"])
    await bot.dispatch("card", uuid.UUID(ws), uuid.UUID(req["id"]))
    mid, card = _card(ch, chat)
    index = next(i for i, v in enumerate(req["variants"]) if v["post_variant_id"] == vid)
    detail = next(
        b.data
        for row in card.rows
        for b in row
        if b.data and b.data.startswith("v:") and b.data.endswith(f":{index}")
    )
    assert await bot.handle_update(_tap(chat, mid, detail)) == "detail"
    shown = ch.messages[(chat, mid)]
    regen = next(
        (b for row in shown.rows for b in row if b.label.startswith("🎨 Regenerate image")), None
    )
    assert regen is not None, [b.label for row in shown.rows for b in row]
    assert "$0.06" in regen.label  # one landscape image
    assert await bot.handle_update(_tap(chat, mid, regen.data or "")) == "regenerating"
    assert "Regenerating 1 image" in (ch.answers[-1]["text"] or "")
    assert await media.run_all() == ["succeeded"]
    # The new image made a new version, and that account was asked again.
    r = await api.get(f"/api/v1/workspaces/{ws}/approval-requests", headers=_h(s))
    assert r.status_code == 200, r.text
    open_reqs = [x for x in r.json()["data"] if x["state"] == "pending"]
    assert any([v["post_variant_id"] for v in x["variants"]] == [vid] for x in open_reqs), open_reqs
