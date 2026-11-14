"""P2-M3 (docs/13): Bluesky and Mastodon take part like the Phase 1 platforms.

Sandbox mode: the connectors and adapters are the fakes, but the Mera side (app-password
sign-in, per-instance app registration, the one-time connect code, groups, staggering,
publishing) runs for real.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.test_accounts_m3 import _setup
from tests.integration.test_drafting_m5 import (
    CapturingDeferrer,
    _group,
    _run_queued,
    _sets,
    deferrer,  # noqa: F401 - shared fixture
)
from tests.integration.test_identity_m2 import _h
from tests.integration.test_publishing_m8 import (
    _approve_all,
    _publisher,
    _slots,
    quiet,  # noqa: F401 - shared fixture
)

pytestmark = pytest.mark.integration

APP_PASSWORD = "abcd-efgh-ijkl-mnop"


async def _bluesky(api: httpx.AsyncClient, s: dict[str, Any], ws: str, handle: str) -> str:
    r = await api.post(
        f"/api/v1/workspaces/{ws}/social-accounts/connect/bluesky/sign-in",
        headers=_h(s),
        json={"identifier": handle, "secret": APP_PASSWORD},
    )
    assert r.status_code == 200, r.text
    r = await api.post(
        "/api/v1/social-accounts/connect/complete",
        headers=_h(s),
        json={"connect_code": r.json()["connect_code"]},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["account_id"])


async def _mastodon(
    api: httpx.AsyncClient, s: dict[str, Any], ws: str, instance: str, handle: str
) -> str:
    r = await api.post(
        f"/api/v1/workspaces/{ws}/social-accounts/connect/mastodon",
        headers=_h(s),
        json={"return_path": "/w/acme/accounts", "instance": instance},
    )
    assert r.status_code == 200, r.text
    state = parse_qs(urlsplit(r.json()["authorize_url"]).query)["state"][0]
    host = instance.lower().removeprefix("https://").rstrip("/")
    acct = f"{uuid.uuid4().hex[:6]}@{host}"
    r = await api.get(
        "/api/v1/social-accounts/oauth/mastodon/callback",
        params={"code": f"fake:{acct}:{handle}@{host}:personal", "state": state},
    )
    assert r.status_code == 302, r.text
    query = parse_qs(urlsplit(r.headers["location"]).query)
    assert "connect_code" in query, r.headers["location"]
    code = query["connect_code"][0]
    r = await api.post(
        "/api/v1/social-accounts/connect/complete", headers=_h(s), json={"connect_code": code}
    )
    assert r.status_code == 200, r.text
    return str(r.json()["account_id"])


async def test_bluesky_connects_with_an_app_password_only(api: httpx.AsyncClient) -> None:
    s, ws = await _setup(api, "bsky")
    # The OAuth redirect is not offered for Bluesky.
    r = await api.post(
        f"/api/v1/workspaces/{ws}/social-accounts/connect/bluesky",
        headers=_h(s),
        json={"return_path": "/"},
    )
    assert r.json()["error"]["code"] == "use_app_password"
    # An account password is refused before anything is sent.
    r = await api.post(
        f"/api/v1/workspaces/{ws}/social-accounts/connect/bluesky/sign-in",
        headers=_h(s),
        json={"identifier": "acme.bsky.social", "secret": "hunter2 my real password"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "sign_in_failed"
    assert "app password" in r.json()["error"]["message"]
    acct = await _bluesky(api, s, ws, f"acme{uuid.uuid4().hex[:4]}.bsky.social")
    detail = (
        await api.get(f"/api/v1/workspaces/{ws}/social-accounts/{acct}", headers=_h(s))
    ).json()
    assert detail["platform"] == "bluesky"
    assert detail["status"] == "active"
    # The sign-in route is for app-password platforms only.
    r = await api.post(
        f"/api/v1/workspaces/{ws}/social-accounts/connect/x/sign-in",
        headers=_h(s),
        json={"identifier": "a", "secret": APP_PASSWORD},
    )
    assert r.json()["error"]["code"] == "use_oauth"


async def test_mastodon_registers_once_per_instance(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    s, ws = await _setup(api, "masto")
    r = await api.post(
        f"/api/v1/workspaces/{ws}/social-accounts/connect/mastodon",
        headers=_h(s),
        json={"return_path": "/"},
    )
    assert r.json()["error"]["code"] == "instance_required"
    instance = f"m{uuid.uuid4().hex[:6]}.example"
    one = await _mastodon(api, s, ws, instance, "alice")
    two = await _mastodon(api, s, ws, f"https://{instance.upper()}/", "bob")
    assert one != two
    async with owner_engine.connect() as c:
        rows = (
            await c.execute(
                text("SELECT client_id, client_secret_ct FROM mastodon_app WHERE instance = :i"),
                {"i": instance},
            )
        ).all()
    assert len(rows) == 1  # registered once, reused
    assert "sandbox-secret" not in rows[0].client_secret_ct  # stored encrypted
    handles = {
        (await api.get(f"/api/v1/workspaces/{ws}/social-accounts/{a}", headers=_h(s))).json()[
            "handle"
        ]
        for a in (one, two)
    }
    assert handles == {f"alice@{instance}", f"bob@{instance}"}


async def test_a_mixed_fediverse_group_drafts_and_publishes_staggered(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "fedi")
    b1 = await _bluesky(api, s, ws, f"one{uuid.uuid4().hex[:4]}.bsky.social")
    b2 = await _bluesky(api, s, ws, f"two{uuid.uuid4().hex[:4]}.bsky.social")
    m1 = await _mastodon(api, s, ws, "fosstodon.example", f"news{uuid.uuid4().hex[:4]}")
    grp = await _group(api, s, ws, "Fediverse", [b1, b2, m1])
    r = await api.post(
        _sets(ws),
        headers=_h(s),
        json={
            "topic": "Three things we learned moving search to Postgres full text",
            "targets": {"account_group_ids": [grp]},
        },
    )
    assert r.status_code == 201, r.text
    await _run_queued(api, deferrer)
    ps = (await api.get(_sets(ws, r.json()["id"]), headers=_h(s))).json()
    assert len(ps["variants"]) == 3, (ps["variants"], ps.get("skipped"), r.json().get("skipped"))
    bodies = {v["social_account_id"]: v["current_version"]["body"] for v in ps["variants"]}
    # Same-platform siblings always differ (the duplicate guardrail is per platform; the
    # sandbox writer may repeat itself across platforms, which no platform minds).
    assert bodies[b1] != bodies[b2]
    await _approve_all(api, s, ws, ps["id"])
    slots = await _slots(api, ws, ps["id"])
    assert len({sp.scheduled_for for sp in slots}) == 3  # staggered
    bsky = sorted(sp.scheduled_for for sp in slots if str(sp.social_account_id) in (b1, b2))
    assert bsky[1] - bsky[0] >= timedelta(minutes=1)
    pub, adapters = _publisher(api)
    from mera.infrastructure.platforms.adapters.fake import FakePlatformAdapter

    adapters["bluesky"] = FakePlatformAdapter("bluesky")
    adapters["mastodon"] = FakePlatformAdapter("mastodon")
    pub._adapters.update(adapters)  # the sandbox fakes for the two new platforms
    outcomes = [
        await pub.publish(uuid.UUID(ws), sp.id, now=sp.scheduled_for + timedelta(seconds=1))
        for sp in slots
    ]
    assert outcomes == ["published"] * 3
    assert len(adapters["bluesky"].posts) == 2
    assert len(adapters["mastodon"].posts) == 1
