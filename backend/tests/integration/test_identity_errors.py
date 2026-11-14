"""Service error paths for auth and workspaces (services target 90%, docs/10 §6.2)."""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.test_identity_m2 import _create_ws, _h, _me, _register_login, _uniq

pytestmark = pytest.mark.integration


async def test_duplicate_email_is_a_conflict(api: httpx.AsyncClient) -> None:
    s = await _register_login(api, "dup")
    r = await api.post(
        "/api/v1/auth/register",
        json={"email": s["email"], "password": "a perfectly fine passphrase", "display_name": "D"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "email_taken"


async def test_workspace_input_validation(api: httpx.AsyncClient) -> None:
    s = await _register_login(api, "wsv")
    url = "/api/v1/workspaces"
    r = await api.post(url, headers=_h(s), json={"name": "X", "slug": "Bad Slug"})
    assert r.json()["error"]["code"] == "invalid_slug"
    r = await api.post(
        url, headers=_h(s), json={"name": "X", "slug": _uniq("ok"), "timezone": "Mars/Base"}
    )
    assert r.json()["error"]["code"] == "invalid_timezone"
    slug = _uniq("taken")
    assert (await api.post(url, headers=_h(s), json={"name": "X", "slug": slug})).status_code == 201
    r = await api.post(url, headers=_h(s), json={"name": "Y", "slug": slug})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "slug_taken"


async def test_unknown_member_and_unknown_workspace_are_404(api: httpx.AsyncClient) -> None:
    s = await _register_login(api, "nf")
    ws = await _create_ws(api, s, "NotFound")
    r = await api.patch(
        f"/api/v1/workspaces/{ws}/members/{uuid.uuid4()}", headers=_h(s), json={"role": "viewer"}
    )
    assert r.status_code == 404
    assert (await api.get(f"/api/v1/workspaces/{uuid.uuid4()}", headers=_h(s))).status_code == 404


async def test_same_role_change_is_a_noop_and_get_workspace_works(api: httpx.AsyncClient) -> None:
    s = await _register_login(api, "noop")
    me = await _me(api, s)
    ws = await _create_ws(api, s, "Noop")
    r = await api.get(f"/api/v1/workspaces/{ws}", headers=_h(s))
    assert r.json()["role"] == "owner"
    r = await api.patch(
        f"/api/v1/workspaces/{ws}/members/{me['id']}", headers=_h(s), json={"role": "owner"}
    )
    assert r.status_code == 200


async def test_expired_unknown_and_double_logout_refresh_paths(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    s = await _register_login(api, "exp")
    r = await api.post("/api/v1/auth/refresh", json={"refresh_token": "mera_rt_unknown"})
    assert r.status_code == 401
    # Logging out twice, or with an unknown token, is harmless.
    assert (
        await api.post("/api/v1/auth/logout", json={"refresh_token": "mera_rt_unknown"})
    ).status_code == 204

    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE refresh_token SET expires_at = now() - interval '1 minute' "
                'WHERE user_id = (SELECT id FROM "user" WHERE email = :e)'
            ),
            {"e": s["email"]},
        )
    r = await api.post("/api/v1/auth/refresh", json={"refresh_token": s["refresh_token"]})
    assert r.status_code == 401


async def test_deleted_user_cannot_refresh_or_authenticate(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    s = await _register_login(api, "del")
    async with owner_engine.begin() as conn:
        await conn.execute(
            text('UPDATE "user" SET deleted_at = now() WHERE email = :e'), {"e": s["email"]}
        )
    assert (
        await api.post("/api/v1/auth/refresh", json={"refresh_token": s["refresh_token"]})
    ).status_code == 401
    assert (await api.get("/api/v1/auth/me", headers=_h(s))).status_code == 401
    r = await api.post(
        "/api/v1/auth/login", json={"email": s["email"], "password": "a perfectly fine passphrase"}
    )
    assert r.status_code == 401
