"""Regression tests for the Sprint 3 security review: each test names the finding it pins."""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import make_api
from tests.integration.test_identity_m2 import (
    PASSWORD,
    _add_member,
    _create_ws,
    _h,
    _me,
    _register_login,
    _uniq,
)
from tests.integration.test_sprint3 import _key, _kh, _oauth, verify_email

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------- H1 email ownership


async def test_email_verification_flow(api: httpx.AsyncClient) -> None:
    s = await _register_login(api, "ev")
    assert (await _me(api, s))["email_verified"] is False
    r = await api.post("/api/v1/auth/email/verification", headers=_h(s))
    token = r.json()["verify_url"].rsplit("/", 1)[1]
    assert (await api.post("/api/v1/auth/email/verify", json={"token": token})).status_code == 204
    assert (await _me(api, s))["email_verified"] is True
    # Single use, and a second request is refused once verified.
    assert (await api.post("/api/v1/auth/email/verify", json={"token": token})).status_code == 404
    r = await api.post("/api/v1/auth/email/verification", headers=_h(s))
    assert r.json()["error"]["code"] == "already_verified"


async def test_verification_needs_email_delivery_outside_sandbox(
    postgres_urls: tuple[str, str],
) -> None:
    async with make_api(postgres_urls, rate_limit_enabled=False, sandbox_mode=False) as api:
        s = await _register_login(api, "nosandbox")
        r = await api.post("/api/v1/auth/email/verification", headers=_h(s))
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "email_delivery_unavailable"


async def test_verified_google_signin_evicts_an_unverified_squatter(api: httpx.AsyncClient) -> None:
    # The attacker registers the victim's address with their own password...
    squatter = await _register_login(api, "victim")
    # ...the real owner later signs in with Google, which proves the address.
    r, verifier = await _oauth(api, "google", f"fake:{_uniq('real')}:{squatter['email']}:1")
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    owner = (
        await api.post(
            "/api/v1/auth/oauth/exchange", json={"code": code, "code_verifier": verifier}
        )
    ).json()
    assert (await _me(api, owner))["email_verified"] is True
    # The squatter's live session, refresh token and password are all gone.
    assert (await api.get("/api/v1/auth/me", headers=_h(squatter))).status_code == 401
    r = await api.post("/api/v1/auth/refresh", json={"refresh_token": squatter["refresh_token"]})
    assert r.status_code == 401
    r = await api.post(
        "/api/v1/auth/login", json={"email": squatter["email"], "password": PASSWORD}
    )
    assert r.status_code == 401


async def test_unverified_microsoft_identity_cannot_accept_invitations(
    api: httpx.AsyncClient,
) -> None:
    owner = await _register_login(api, "msinv")
    ws = await _create_ws(api, owner, "MS invite")
    email = f"{_uniq('target')}@example.com"
    r = await api.post(
        f"/api/v1/workspaces/{ws}/members/invitations",
        headers=_h(owner),
        json={"email": email, "role": "admin"},
    )
    token = r.json()["token"]
    # Attacker signs in through a tenant they control, claiming the invited address.
    r, verifier = await _oauth(api, "microsoft", f"fake:{_uniq('ms')}:{email}:1")
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    attacker = (
        await api.post(
            "/api/v1/auth/oauth/exchange", json={"code": code, "code_verifier": verifier}
        )
    ).json()
    r = await api.post("/api/v1/invitations/accept", headers=_h(attacker), json={"token": token})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "email_unverified"


# ---------------------------------------------------------------- H2 keys cannot move power


async def test_admin_key_cannot_change_membership(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    owner = await _register_login(api, "h2")
    viewer = await _register_login(api, "h2v")
    viewer_id = (await _me(api, viewer))["id"]
    ws = await _create_ws(api, owner, "H2")
    await _add_member(owner_engine, ws, viewer_id, "viewer")
    key = (await _key(api, owner, ws, ["admin"]))["secret"]
    base = f"/api/v1/workspaces/{ws}/members"
    for r in (
        await api.post(
            f"{base}/invitations",
            headers=_kh(key),
            json={"email": "x@example.com", "role": "admin"},
        ),
        await api.patch(f"{base}/{viewer_id}", headers=_kh(key), json={"role": "owner"}),
        await api.delete(f"{base}/{viewer_id}", headers=_kh(key)),
    ):
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "user_credentials_required"


# ---------------------------------------------------------------- H3 / M1 idempotency


async def test_secret_bearing_responses_are_encrypted_at_rest_and_replayable(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    owner = await _register_login(api, "h3")
    ws = await _create_ws(api, owner, "H3")
    headers = {**_h(owner), "Idempotency-Key": _uniq("h3-key")}
    body = {"name": "ci", "scopes": ["analytics:read"]}
    first = await api.post(f"/api/v1/workspaces/{ws}/api-keys", headers=headers, json=body)
    second = await api.post(f"/api/v1/workspaces/{ws}/api-keys", headers=headers, json=body)
    assert second.headers["idempotency-replayed"] == "true"
    assert first.json()["secret"] == second.json()["secret"]
    async with owner_engine.connect() as conn:
        stored = (
            await conn.execute(
                text("SELECT response_body FROM idempotency_key WHERE key = :k"),
                {"k": headers["Idempotency-Key"]},
            )
        ).scalar_one()
    assert first.json()["secret"].encode() not in stored
    assert stored.startswith(b"mera:enc:1:")


async def test_auth_responses_are_never_cached(api: httpx.AsyncClient) -> None:
    s = await _register_login(api, "h3auth")
    headers = {"Idempotency-Key": _uniq("login-key")}
    body = {"email": s["email"], "password": PASSWORD}
    a = await api.post("/api/v1/auth/login", headers=headers, json=body)
    b = await api.post("/api/v1/auth/login", headers=headers, json=body)
    assert "idempotency-replayed" not in b.headers
    assert a.json()["refresh_token"] != b.json()["refresh_token"]


async def test_revoked_key_cannot_replay_a_cached_response(api: httpx.AsyncClient) -> None:
    owner = await _register_login(api, "m1")
    ws = await _create_ws(api, owner, "M1")
    created = await _key(api, owner, ws, ["admin"])
    secret = created["secret"]
    headers = {**_kh(secret), "Idempotency-Key": _uniq("m1-key")}
    r = await api.patch(f"/api/v1/workspaces/{ws}", headers=headers, json={"name": "M1 renamed"})
    assert r.status_code == 200
    await api.delete(f"/api/v1/workspaces/{ws}/api-keys/{created['key']['id']}", headers=_h(owner))
    r = await api.patch(f"/api/v1/workspaces/{ws}", headers=headers, json={"name": "M1 renamed"})
    assert r.status_code == 401
    assert "idempotency-replayed" not in r.headers


async def test_unauthorised_attempt_does_not_poison_the_key(api: httpx.AsyncClient) -> None:
    s = await _register_login(api, "m1p")
    key = _uniq("poison")
    body = {"name": "Poison", "slug": _uniq("poison")}
    # A forged-looking but non-authenticating credential claims the key first...
    bad = {"Authorization": "Bearer mera_sk_test_forged", "Idempotency-Key": key}
    assert (await api.post("/api/v1/workspaces", headers=bad, json=body)).status_code == 401
    # ...the owner's real request with the same key still executes.
    r = await api.post("/api/v1/workspaces", headers={**_h(s), "Idempotency-Key": key}, json=body)
    assert r.status_code == 201
    assert "idempotency-replayed" not in r.headers


# ---------------------------------------------------------------- M3 / M4 authority


async def test_invitation_dies_with_its_inviters_authority(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    owner = await _register_login(api, "m3")
    admin = await _register_login(api, "m3a")
    admin_id = (await _me(api, admin))["id"]
    invitee = await _register_login(api, "m3i")
    await verify_email(api, invitee)
    ws = await _create_ws(api, owner, "M3")
    await _add_member(owner_engine, ws, admin_id, "admin")
    r = await api.post(
        f"/api/v1/workspaces/{ws}/members/invitations",
        headers=_h(admin),
        json={"email": invitee["email"], "role": "admin"},
    )
    token = r.json()["token"]
    await api.patch(
        f"/api/v1/workspaces/{ws}/members/{admin_id}", headers=_h(owner), json={"role": "viewer"}
    )
    listed = await api.get(f"/api/v1/workspaces/{ws}/members/invitations", headers=_h(owner))
    assert listed.json()["data"] == []
    r = await api.post("/api/v1/invitations/accept", headers=_h(invitee), json={"token": token})
    assert r.status_code == 404


async def test_removed_members_keys_stay_dead_after_re_adding(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    owner = await _register_login(api, "m4")
    admin = await _register_login(api, "m4a")
    admin_id = (await _me(api, admin))["id"]
    ws = await _create_ws(api, owner, "M4")
    await _add_member(owner_engine, ws, admin_id, "admin")
    key = (await _key(api, admin, ws, ["analytics:read"]))["secret"]
    await api.delete(f"/api/v1/workspaces/{ws}/members/{admin_id}", headers=_h(owner))
    await _add_member(owner_engine, ws, admin_id, "admin")
    assert (await api.get(f"/api/v1/workspaces/{ws}", headers=_kh(key))).status_code == 401


# ---------------------------------------------------------------- M2 / M5 limits


async def test_concurrent_guesses_cannot_exceed_the_login_budget(
    postgres_urls: tuple[str, str],
) -> None:
    async with make_api(postgres_urls, rate_limit_enabled=False) as setup:
        s = await _register_login(setup, "burst")
    async with make_api(postgres_urls, rate_limit_enabled=True, client_ip="10.253.0.1") as api:
        results = await asyncio.gather(
            *(
                api.post("/api/v1/auth/login", json={"email": s["email"], "password": "wrong" * 3})
                for _ in range(8)
            )
        )
        codes = sorted(r.status_code for r in results)
        assert codes.count(401) == 5
        assert codes.count(429) == 3
    # The account is not locked out from everywhere: another IP still signs in.
    async with make_api(postgres_urls, rate_limit_enabled=True, client_ip="10.253.0.2") as api:
        r = await api.post("/api/v1/auth/login", json={"email": s["email"], "password": PASSWORD})
        assert r.status_code == 200


async def test_invented_keys_do_not_get_fresh_buckets(
    postgres_urls: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from mera.api.middleware import rate_limit
    from mera.infrastructure.db.rate_limit import Limit

    # Shrink the per-IP read ceiling so the test does not race its own refill.
    monkeypatch.setitem(rate_limit.IP_CEILING, "read", Limit(capacity=5, per_seconds=3600))
    async with make_api(postgres_urls, rate_limit_enabled=True, client_ip="10.254.0.1") as api:
        statuses = [
            (await api.get("/api/v1/workspaces", headers=_kh(f"mera_sk_test_{i}"))).status_code
            for i in range(6)
        ]
    # Every invented key had a full bucket of its own, yet the sixth request is refused.
    assert statuses == [401] * 5 + [429]


async def test_purge_removes_expired_operational_rows(
    postgres_urls: tuple[str, str], owner_engine: AsyncEngine
) -> None:
    from mera.workers.tasks.system import purge

    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO rate_limit_bucket (key, tokens, refilled_at) "
                "VALUES (:k, 1, now() - interval '2 hours')"
            ),
            {"k": _uniq("stale")},
        )
    counts = await purge(postgres_urls[1])
    assert counts["rate_limit_bucket"] >= 1
    assert set(counts) == {
        "rate_limit_bucket",
        "idempotency_key",
        "oauth_flow",
        "email_verification",
    }
