"""Sprint 3 (M2 part 2): API keys, invitations, workspace lifecycle, rate limiting,
idempotency, OAuth, and the audit gaps carried forward from Sprint 2."""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from typing import Any
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

pytestmark = pytest.mark.integration


async def verify_email(api: httpx.AsyncClient, s: dict[str, Any]) -> None:
    """Sandbox email verification: the link comes back in the response."""
    r = await api.post("/api/v1/auth/email/verification", headers=_h(s))
    assert r.status_code == 200, r.text
    token = r.json()["verify_url"].rsplit("/", 1)[1]
    assert (await api.post("/api/v1/auth/email/verify", json={"token": token})).status_code == 204


def _kh(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


async def _key(
    api: httpx.AsyncClient, s: dict[str, Any], ws: str, scopes: list[str]
) -> dict[str, Any]:
    r = await api.post(
        f"/api/v1/workspaces/{ws}/api-keys", headers=_h(s), json={"name": "ci", "scopes": scopes}
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- API keys


async def test_api_key_is_bound_to_workspace_scopes_and_users_only_endpoints(
    api: httpx.AsyncClient,
) -> None:
    owner = await _register_login(api, "keyown")
    ws = await _create_ws(api, owner, "Keys")
    other = await _create_ws(api, owner, "Other")
    created = await _key(api, owner, ws, ["analytics:read"])
    secret = created["secret"]
    assert secret.startswith("mera_sk_test_")  # sandbox mode
    assert created["key"]["key_prefix"] == secret[:16]

    # Reads inside its workspace work; the listing shows only that workspace.
    assert (await api.get(f"/api/v1/workspaces/{ws}", headers=_kh(secret))).status_code == 200
    r = await api.get("/api/v1/workspaces", headers=_kh(secret))
    assert [w["id"] for w in r.json()["data"]] == [ws]
    # Another workspace of the same owner is invisible to the key.
    assert (await api.get(f"/api/v1/workspaces/{other}", headers=_kh(secret))).status_code == 404
    # Scope caps the owner's role: no member management, no audit log.
    me = await _me(api, owner)
    r = await api.patch(
        f"/api/v1/workspaces/{ws}/members/{me['id']}", headers=_kh(secret), json={"role": "owner"}
    )
    assert r.status_code == 403
    # User-only endpoints refuse keys; keys cannot mint keys or workspaces.
    for r in (
        await api.get("/api/v1/auth/me", headers=_kh(secret)),
        await api.post(
            "/api/v1/workspaces", headers=_kh(secret), json={"name": "x", "slug": _uniq("k")}
        ),
        await api.post(
            f"/api/v1/workspaces/{ws}/api-keys",
            headers=_kh(secret),
            json={"name": "n", "scopes": ["admin"]},
        ),
    ):
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "user_credentials_required"

    # The secret is never listed again; last use is recorded.
    r = await api.get(f"/api/v1/workspaces/{ws}/api-keys", headers=_h(owner))
    listed = r.json()["data"][0]
    assert "secret" not in listed
    assert listed["last_used_at"] is not None


async def test_api_key_follows_its_creator(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    owner = await _register_login(api, "kfo")
    admin = await _register_login(api, "kfa")
    admin_id = (await _me(api, admin))["id"]
    ws = await _create_ws(api, owner, "Follow")
    await _add_member(owner_engine, ws, admin_id, "admin")
    key = (await _key(api, admin, ws, ["admin"]))["secret"]
    assert (
        await api.get(f"/api/v1/workspaces/{ws}/audit-log", headers=_kh(key))
    ).status_code == 200

    # Demote the creator: the key loses the capability immediately.
    await api.patch(
        f"/api/v1/workspaces/{ws}/members/{admin_id}", headers=_h(owner), json={"role": "viewer"}
    )
    assert (
        await api.get(f"/api/v1/workspaces/{ws}/audit-log", headers=_kh(key))
    ).status_code == 403
    # Remove the creator: the key cannot reach the workspace at all.
    r = await api.delete(f"/api/v1/workspaces/{ws}/members/{admin_id}", headers=_h(owner))
    assert r.status_code == 204
    # ...and the removal revoked the key outright (Sprint 3 review M4).
    assert (await api.get(f"/api/v1/workspaces/{ws}", headers=_kh(key))).status_code == 401


async def test_api_key_revocation_expiry_and_validation(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    owner = await _register_login(api, "krev")
    viewer = await _register_login(api, "krevv")
    ws = await _create_ws(api, owner, "Revoke")
    await _add_member(owner_engine, ws, (await _me(api, viewer))["id"], "viewer")
    url = f"/api/v1/workspaces/{ws}/api-keys"

    r = await api.post(url, headers=_h(owner), json={"name": "x", "scopes": ["everything"]})
    assert r.json()["error"]["code"] == "invalid_scopes"
    r = await api.post(
        url, headers=_h(owner), json={"name": "x", "scopes": ["admin"], "expires_in_days": 0}
    )
    assert r.json()["error"]["code"] == "invalid_expiry"
    r = await api.post(url, headers=_h(viewer), json={"name": "x", "scopes": ["analytics:read"]})
    assert r.status_code == 403

    a = await _key(api, owner, ws, ["analytics:read"])
    assert (await api.delete(f"{url}/{a['key']['id']}", headers=_h(owner))).status_code == 204
    assert (await api.get(f"/api/v1/workspaces/{ws}", headers=_kh(a["secret"]))).status_code == 401
    assert (await api.delete(f"{url}/{uuid.uuid4()}", headers=_h(owner))).status_code == 404

    b = await _key(api, owner, ws, ["analytics:read"])
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE api_key SET expires_at = now() - interval '1 second' WHERE id = :i"),
            {"i": b["key"]["id"]},
        )
    assert (await api.get(f"/api/v1/workspaces/{ws}", headers=_kh(b["secret"]))).status_code == 401
    assert (
        await api.get("/api/v1/workspaces", headers=_kh("mera_sk_test_unknown"))
    ).status_code == 401


# ---------------------------------------------------------------- invitations


async def test_invitation_lifecycle(api: httpx.AsyncClient, owner_engine: AsyncEngine) -> None:
    owner = await _register_login(api, "inv")
    editor = await _register_login(api, "inved")
    ws = await _create_ws(api, owner, "Invites")
    await _add_member(owner_engine, ws, (await _me(api, editor))["id"], "editor")
    invitee = await _register_login(api, "invitee")
    stranger = await _register_login(api, "strange")
    await verify_email(api, stranger)  # verified, so this tests "wrong person", not "unverified"
    url = f"/api/v1/workspaces/{ws}/members/invitations"

    # Editors cannot invite; ownership is never invited.
    assert (
        await api.post(url, headers=_h(editor), json={"email": invitee["email"], "role": "viewer"})
    ).status_code == 403
    r = await api.post(url, headers=_h(owner), json={"email": invitee["email"], "role": "owner"})
    assert r.json()["error"]["code"] == "invalid_role"
    # Already a member.
    r = await api.post(url, headers=_h(owner), json={"email": editor["email"], "role": "viewer"})
    assert r.json()["error"]["code"] == "already_member"

    r = await api.post(url, headers=_h(owner), json={"email": invitee["email"], "role": "approver"})
    assert r.status_code == 201
    token = r.json()["token"]
    assert r.json()["accept_url"].endswith(f"/invite/{token}")
    r = await api.post(url, headers=_h(owner), json={"email": invitee["email"], "role": "viewer"})
    assert r.json()["error"]["code"] == "invitation_pending"
    assert len((await api.get(url, headers=_h(owner))).json()["data"]) == 1

    # Only the addressee can accept; anyone else sees an invalid link.
    accept = "/api/v1/invitations/accept"
    r = await api.post(accept, headers=_h(stranger), json={"token": token})
    assert r.status_code == 404
    r = await api.post(accept, headers=_h(invitee), json={"token": token})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "email_unverified"
    await verify_email(api, invitee)
    r = await api.post(accept, headers=_h(invitee), json={"token": token})
    assert r.status_code == 200
    assert r.json()["workspace_id"] == ws
    roles = {
        w["id"]: w["role"]
        for w in (await api.get("/api/v1/workspaces", headers=_h(invitee))).json()["data"]
    }
    assert roles[ws] == "approver"
    # Single use.
    assert (await api.post(accept, headers=_h(invitee), json={"token": token})).status_code == 404


async def test_revoked_invitation_cannot_be_accepted(api: httpx.AsyncClient) -> None:
    owner = await _register_login(api, "invr")
    invitee = await _register_login(api, "invre")
    ws = await _create_ws(api, owner, "Revoked invite")
    url = f"/api/v1/workspaces/{ws}/members/invitations"
    r = await api.post(url, headers=_h(owner), json={"email": invitee["email"], "role": "viewer"})
    inv_id, token = r.json()["invitation"]["id"], r.json()["token"]
    assert (await api.delete(f"{url}/{inv_id}", headers=_h(owner))).status_code == 204
    await verify_email(api, invitee)
    r = await api.post("/api/v1/invitations/accept", headers=_h(invitee), json={"token": token})
    assert r.status_code == 404
    assert (await api.delete(f"{url}/{uuid.uuid4()}", headers=_h(owner))).status_code == 404


# ---------------------------------------------------------------- workspace lifecycle


async def test_update_archive_and_delete(api: httpx.AsyncClient, owner_engine: AsyncEngine) -> None:
    owner = await _register_login(api, "life")
    admin = await _register_login(api, "lifea")
    editor = await _register_login(api, "lifee")
    admin_id = (await _me(api, admin))["id"]
    editor_id = (await _me(api, editor))["id"]
    ws = await _create_ws(api, owner, "Life")
    await _add_member(owner_engine, ws, admin_id, "admin")
    await _add_member(owner_engine, ws, editor_id, "editor")
    base = f"/api/v1/workspaces/{ws}"

    r = await api.patch(
        base, headers=_h(admin), json={"name": "Life 2", "timezone": "Europe/Berlin"}
    )
    assert r.status_code == 200
    assert (r.json()["name"], r.json()["timezone"]) == ("Life 2", "Europe/Berlin")
    assert (await api.patch(base, headers=_h(editor), json={"name": "no"})).status_code == 403
    assert (
        await api.patch(base, headers=_h(admin), json={"timezone": "Nowhere/Land"})
    ).status_code == 422

    # Archived: read-only, but shrinking access still works.
    assert (await api.post(f"{base}/archive", headers=_h(editor))).status_code == 403
    r = await api.post(f"{base}/archive", headers=_h(admin))
    assert r.json()["archived"] is True
    for r in (
        await api.patch(base, headers=_h(admin), json={"name": "x"}),
        await api.patch(f"{base}/members/{editor_id}", headers=_h(owner), json={"role": "viewer"}),
        await api.post(
            f"{base}/api-keys", headers=_h(owner), json={"name": "x", "scopes": ["admin"]}
        ),
        await api.post(
            f"{base}/members/invitations",
            headers=_h(owner),
            json={"email": "a@example.com", "role": "viewer"},
        ),
    ):
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "workspace_archived"
    assert (await api.get(base, headers=_h(editor))).status_code == 200  # still readable
    assert (await api.delete(f"{base}/members/{editor_id}", headers=_h(admin))).status_code == 204
    r = await api.post(f"{base}/unarchive", headers=_h(admin))
    assert r.json()["archived"] is False

    # Only the owner deletes; a deleted workspace disappears for everyone.
    assert (await api.delete(base, headers=_h(admin))).status_code == 403
    assert (await api.delete(base, headers=_h(owner))).status_code == 204
    assert (await api.get(base, headers=_h(owner))).status_code == 404
    assert ws not in [
        w["id"] for w in (await api.get("/api/v1/workspaces", headers=_h(owner))).json()["data"]
    ]


async def test_member_removal_rules(api: httpx.AsyncClient, owner_engine: AsyncEngine) -> None:
    owner = await _register_login(api, "rm")
    admin = await _register_login(api, "rma")
    owner_id = (await _me(api, owner))["id"]
    admin_id = (await _me(api, admin))["id"]
    ws = await _create_ws(api, owner, "Removal")
    await _add_member(owner_engine, ws, admin_id, "admin")
    base = f"/api/v1/workspaces/{ws}/members"
    assert (await api.delete(f"{base}/{owner_id}", headers=_h(admin))).status_code == 403
    r = await api.delete(f"{base}/{owner_id}", headers=_h(owner))
    assert r.json()["error"]["code"] == "last_owner"
    assert (await api.delete(f"{base}/{uuid.uuid4()}", headers=_h(owner))).status_code == 404


# ---------------------------------------------------------------- rate limiting


async def test_auth_endpoints_are_rate_limited_per_ip(postgres_urls: tuple[str, str]) -> None:
    ip = f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"
    async with make_api(postgres_urls, rate_limit_enabled=True, client_ip=ip) as api:
        statuses = []
        for _ in range(11):
            r = await api.post(
                "/api/v1/auth/login",
                json={"email": f"{_uniq('nobody')}@example.com", "password": "x" * 14},
            )
            statuses.append(r.status_code)
        assert statuses[:10] == [401] * 10
        assert statuses[10] == 429
        assert int(r.headers["retry-after"]) >= 1
        assert r.json()["error"]["code"] == "rate_limited"
        assert r.headers["x-request-id"]
        # Reads are a separate, larger bucket and carry the headers.
        r = await api.get("/api/v1/auth/me")
        assert r.headers["x-ratelimit-limit"] == "600"


async def test_login_is_throttled_per_account(postgres_urls: tuple[str, str]) -> None:
    async with make_api(postgres_urls, rate_limit_enabled=True, client_ip="10.250.1.1") as api:
        s = await _register_login(api, "throttle")
    # 20 failures per account from anywhere (each IP stays under its own 5-per-pair limit).
    for i in range(20):
        async with make_api(
            postgres_urls, rate_limit_enabled=True, client_ip=f"10.251.{i}.1"
        ) as api:
            r = await api.post(
                "/api/v1/auth/login", json={"email": s["email"], "password": "wrong" * 3}
            )
            assert r.status_code == 401
    # Even the right password is refused now, from a fresh IP.
    async with make_api(postgres_urls, rate_limit_enabled=True, client_ip="10.252.0.1") as api:
        r = await api.post("/api/v1/auth/login", json={"email": s["email"], "password": PASSWORD})
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "rate_limited"


# ---------------------------------------------------------------- idempotency


async def test_idempotency_replay_reuse_and_validation(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    s = await _register_login(api, "idem")
    key = _uniq("idem-key")
    body = {"name": "Idem", "slug": _uniq("idem")}
    headers = {**_h(s), "Idempotency-Key": key}
    first = await api.post("/api/v1/workspaces", headers=headers, json=body)
    second = await api.post("/api/v1/workspaces", headers=headers, json=body)
    assert first.status_code == second.status_code == 201
    assert second.headers["idempotency-replayed"] == "true"
    assert first.json()["id"] == second.json()["id"]
    async with owner_engine.connect() as conn:
        n = (
            await conn.execute(
                text("SELECT count(*) FROM workspace WHERE slug = :s"), {"s": body["slug"]}
            )
        ).scalar_one()
    assert n == 1

    r = await api.post("/api/v1/workspaces", headers=headers, json={**body, "name": "Different"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "idempotency_key_reuse"
    r = await api.post(
        "/api/v1/workspaces", headers={**_h(s), "Idempotency-Key": "bad key!"}, json=body
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_idempotency_key"

    # Keys are per principal: another user's identical key is independent.
    other = await _register_login(api, "idem2")
    r = await api.post(
        "/api/v1/workspaces",
        headers={**_h(other), "Idempotency-Key": key},
        json={"name": "B", "slug": _uniq("idemb")},
    )
    assert r.status_code == 201
    assert "idempotency-replayed" not in r.headers


# ---------------------------------------------------------------- OAuth


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


async def _oauth(
    api: httpx.AsyncClient,
    provider: str,
    identity: str,
    *,
    redirect: str = "http://localhost:3000/auth/callback",
) -> tuple[httpx.Response, str]:
    verifier, challenge = _pkce()
    r = await api.get(
        f"/api/v1/auth/oauth/{provider}/authorize",
        params={
            "redirect_uri": redirect,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert r.status_code == 302, r.text
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    r = await api.get(
        f"/api/v1/auth/oauth/{provider}/callback", params={"code": identity, "state": state}
    )
    return r, verifier


async def test_oauth_signup_exchange_and_single_use(api: httpx.AsyncClient) -> None:
    email = f"{_uniq('oauth')}@example.com"
    r, verifier = await _oauth(api, "google", f"fake:{_uniq('sub')}:{email}:1")
    assert r.status_code == 302
    loc = urlsplit(r.headers["location"])
    assert (loc.scheme, loc.netloc, loc.path) == ("http", "localhost:3000", "/auth/callback")
    code = parse_qs(loc.query)["code"][0]

    # Wrong verifier fails; the right one signs in; the code is single use.
    bad = await api.post(
        "/api/v1/auth/oauth/exchange", json={"code": code, "code_verifier": "x" * 43}
    )
    assert bad.status_code == 401
    ok = await api.post(
        "/api/v1/auth/oauth/exchange", json={"code": code, "code_verifier": verifier}
    )
    assert ok.status_code == 200, ok.text
    me = await api.get("/api/v1/auth/me", headers=_h(ok.json()))
    assert me.json()["email"] == email
    again = await api.post(
        "/api/v1/auth/oauth/exchange", json={"code": code, "code_verifier": verifier}
    )
    assert again.status_code == 401


async def test_oauth_state_is_single_use_and_redirects_are_pinned(api: httpx.AsyncClient) -> None:
    _verifier, challenge = _pkce()
    r = await api.get(
        "/api/v1/auth/oauth/google/authorize",
        params={"redirect_uri": "https://evil.example/steal", "code_challenge": challenge},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_redirect_uri"

    r = await api.get(
        "/api/v1/auth/oauth/google/authorize",
        params={"redirect_uri": "http://localhost:3000/cb", "code_challenge": challenge},
    )
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    identity = f"fake:{_uniq('s')}:{_uniq('o')}@example.com:1"
    first = await api.get(
        "/api/v1/auth/oauth/google/callback", params={"code": identity, "state": state}
    )
    assert first.status_code == 302
    replay = await api.get(
        "/api/v1/auth/oauth/google/callback", params={"code": identity, "state": state}
    )
    assert replay.status_code == 422
    assert replay.json()["error"]["code"] == "invalid_state"
    # Provider denial goes back to the client as an error, never a code.
    r, _ = await _oauth(api, "google", "")
    assert "error=provider_denied" in r.headers["location"]


async def test_oauth_account_linking_rules(api: httpx.AsyncClient) -> None:
    existing = await _register_login(api, "link")
    await verify_email(api, existing)
    # Microsoft cannot vouch for the address: no takeover of the existing account.
    r, _ = await _oauth(api, "microsoft", f"fake:{_uniq('ms')}:{existing['email']}:1")
    assert "error=account_exists" in r.headers["location"]
    # Google with an unverified address: also refused.
    r, _ = await _oauth(api, "google", f"fake:{_uniq('g')}:{existing['email']}:0")
    assert "error=account_exists" in r.headers["location"]
    # Google with a verified address links to the same user.
    sub = _uniq("gv")
    r, verifier = await _oauth(api, "google", f"fake:{sub}:{existing['email']}:1")
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    tokens = (
        await api.post(
            "/api/v1/auth/oauth/exchange", json={"code": code, "code_verifier": verifier}
        )
    ).json()
    assert (await _me(api, tokens))["id"] == (await _me(api, existing))["id"]
    # And a second sign-in with the same subject resolves straight to that user.
    r, verifier = await _oauth(api, "google", f"fake:{sub}:changed@example.com:1")
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    tokens = (
        await api.post(
            "/api/v1/auth/oauth/exchange", json={"code": code, "code_verifier": verifier}
        )
    ).json()
    assert (await _me(api, tokens))["email"] == existing["email"]


async def test_unknown_provider_is_rejected(api: httpx.AsyncClient) -> None:
    _, challenge = _pkce()
    r = await api.get(
        "/api/v1/auth/oauth/github/authorize",
        params={"redirect_uri": "http://localhost:3000/cb", "code_challenge": challenge},
    )
    assert r.status_code == 400  # not a known provider name: request validation


# ---------------------------------------------------------------- audit gaps (Sprint 2 L1)


async def test_previously_unaudited_failures_are_now_audited(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    probe = await _register_login(api, "probe")
    victim = await _register_login(api, "victim")
    ws = await _create_ws(api, victim, "Probed")
    marker = _uniq("audit")

    await api.post("/api/v1/auth/refresh", json={"refresh_token": f"mera_rt_{marker}"})
    await api.post(
        "/api/v1/auth/register",
        json={"email": f"{marker}@example.com", "password": "short", "display_name": "x"},
    )
    assert (await api.get(f"/api/v1/workspaces/{ws}", headers=_h(probe))).status_code == 404

    async with owner_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT action, workspace_id, subject_id FROM audit_log "
                    "WHERE action IN ('auth.refresh_failed', 'auth.register_rejected', "
                    "'security.workspace_probe') AND created_at > now() - interval '1 minute'"
                )
            )
        ).all()
    actions = {a for a, _, _ in rows}
    assert {"auth.refresh_failed", "auth.register_rejected", "security.workspace_probe"} <= actions
    probe_rows = [r for r in rows if r[0] == "security.workspace_probe" and str(r[2]) == ws]
    assert probe_rows
    assert probe_rows[0][1] is None  # not written into the probed tenant's log
