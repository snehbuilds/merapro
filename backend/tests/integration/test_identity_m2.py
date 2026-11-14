"""M2 acceptance (docs/11 §M2), Sprint 2 subset: auth cycle, refresh reuse, roles, two
workspaces, scoped denial, audit append-only, and membership discovery across RLS (C8)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from mera.infrastructure.db.session import user_transaction

pytestmark = pytest.mark.integration

PASSWORD = "a perfectly fine passphrase"


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _register_login(api: httpx.AsyncClient, name: str) -> dict[str, Any]:
    email = f"{_uniq(name)}@example.com"
    r = await api.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD, "display_name": name}
    )
    assert r.status_code == 201, r.text
    r = await api.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"id": None, "email": email, **r.json()}


def _h(session: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {session['access_token']}"}


async def _me(api: httpx.AsyncClient, s: dict[str, Any]) -> dict[str, Any]:
    r = await api.get("/api/v1/auth/me", headers=_h(s))
    assert r.status_code == 200, r.text
    return r.json()


async def _create_ws(api: httpx.AsyncClient, s: dict[str, Any], name: str) -> str:
    r = await api.post(
        "/api/v1/workspaces",
        headers=_h(s),
        json={
            "name": name,
            "slug": _uniq(name.lower().replace(" ", "-")),
            "timezone": "Asia/Kolkata",
        },
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _add_member(
    owner_engine: AsyncEngine, ws: str, user_id: str, role: str, *, restricted: bool = False
) -> None:
    # Test setup only: invitations arrive in Sprint 3. The owner role bypasses RLS.
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO workspace_member (id, workspace_id, user_id, role, scope_restricted) "
                "VALUES (gen_random_uuid(), :ws, :u, :r, :x)"
            ),
            {"ws": ws, "u": user_id, "r": role, "x": restricted},
        )


# ---- auth cycle ----


async def test_full_auth_cycle(api: httpx.AsyncClient) -> None:
    s = await _register_login(api, "cycle")
    me = await _me(api, s)
    assert me["email"] == s["email"]
    assert me["workspace_memberships"] == []

    r = await api.post("/api/v1/auth/refresh", json={"refresh_token": s["refresh_token"]})
    assert r.status_code == 200
    rotated = r.json()
    assert rotated["refresh_token"] != s["refresh_token"]
    await _me(api, rotated)

    r = await api.post("/api/v1/auth/logout", json={"refresh_token": rotated["refresh_token"]})
    assert r.status_code == 204
    r = await api.post("/api/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
    assert r.status_code == 401


async def test_rotated_refresh_token_reuse_revokes_the_chain(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    s = await _register_login(api, "reuse")
    r = await api.post("/api/v1/auth/refresh", json={"refresh_token": s["refresh_token"]})
    second = r.json()
    # Age the rotation past the grace window, as if the replay came from a thief later.
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE refresh_token SET revoked_at = revoked_at - interval '1 minute' "
                "WHERE revoked_reason = 'rotated' "
                'AND user_id = (SELECT id FROM "user" WHERE email = :e)'
            ),
            {"e": s["email"]},
        )

    # The stolen first token is replayed: rejected, and the legitimate successor dies too...
    r = await api.post("/api/v1/auth/refresh", json={"refresh_token": s["refresh_token"]})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_refresh_token"
    r = await api.post("/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert r.status_code == 401
    # ...and so does every live access token, via token_version.
    assert (await api.get("/api/v1/auth/me", headers=_h(second))).status_code == 401


async def test_concurrent_refresh_within_grace_is_not_treated_as_theft(
    api: httpx.AsyncClient,
) -> None:
    s = await _register_login(api, "grace")
    r = await api.post("/api/v1/auth/refresh", json={"refresh_token": s["refresh_token"]})
    second = r.json()
    # A second tab presents the same token moments later.
    r = await api.post("/api/v1/auth/refresh", json={"refresh_token": s["refresh_token"]})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "refresh_in_progress"
    # The winning session is untouched.
    assert (await api.get("/api/v1/auth/me", headers=_h(second))).status_code == 200
    r = await api.post("/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert r.status_code == 200


async def test_session_ends_at_the_absolute_cap(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    s = await _register_login(api, "cap")
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE refresh_token SET family_expires_at = now() - interval '1 second' "
                'WHERE user_id = (SELECT id FROM "user" WHERE email = :e)'
            ),
            {"e": s["email"]},
        )
    r = await api.post("/api/v1/auth/refresh", json={"refresh_token": s["refresh_token"]})
    assert r.status_code == 401


async def test_login_failure_is_identical_for_unknown_email_and_wrong_password(
    api: httpx.AsyncClient,
) -> None:
    s = await _register_login(api, "enum")
    wrong = await api.post("/api/v1/auth/login", json={"email": s["email"], "password": "x" * 14})
    unknown = await api.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x" * 14}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"] == "invalid_credentials"
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


@pytest.mark.parametrize(
    ("password", "code"),
    [("short", "password_too_short"), ("password1234", "password_breached")],
)
async def test_weak_passwords_are_rejected(
    api: httpx.AsyncClient, password: str, code: str
) -> None:
    r = await api.post(
        "/api/v1/auth/register",
        json={"email": f"{_uniq('weak')}@example.com", "password": password, "display_name": "W"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == code
    assert r.json()["error"]["field_errors"][0]["field"] == "password"


async def test_bad_access_tokens_are_rejected(api: httpx.AsyncClient) -> None:
    assert (await api.get("/api/v1/auth/me")).status_code == 401
    r = await api.get("/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


async def test_token_version_bump_invalidates_access_tokens(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    s = await _register_login(api, "tv")
    async with owner_engine.begin() as conn:
        await conn.execute(
            text('UPDATE "user" SET token_version = token_version + 1 WHERE email = :e'),
            {"e": s["email"]},
        )
    assert (await api.get("/api/v1/auth/me", headers=_h(s))).status_code == 401


# ---- workspaces, roles, two workspaces ----


async def test_two_workspaces_two_roles_no_leak(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    alice = await _register_login(api, "alice")
    bob = await _register_login(api, "bob")
    bob_id = (await _me(api, bob))["id"]
    ws_alice = await _create_ws(api, alice, "Acme")
    ws_bob = await _create_ws(api, bob, "Personal")
    await _add_member(owner_engine, ws_alice, bob_id, "approver")

    r = await api.get("/api/v1/workspaces", headers=_h(bob))
    roles = {w["id"]: w["role"] for w in r.json()["data"]}
    assert roles == {ws_alice: "approver", ws_bob: "owner"}

    # Alice sees only her workspace and gets 404 (not 403) for Bob's.
    r = await api.get("/api/v1/workspaces", headers=_h(alice))
    assert [w["id"] for w in r.json()["data"]] == [ws_alice]
    r = await api.get(f"/api/v1/workspaces/{ws_bob}", headers=_h(alice))
    assert r.status_code == 404
    r = await api.get(f"/api/v1/workspaces/{ws_bob}/members", headers=_h(alice))
    assert r.status_code == 404

    # Bob as approver in Acme cannot manage members; as owner in Personal he can read audit.
    r = await api.patch(
        f"/api/v1/workspaces/{ws_alice}/members/{bob_id}", headers=_h(bob), json={"role": "admin"}
    )
    assert r.status_code == 403
    assert (
        await api.get(f"/api/v1/workspaces/{ws_bob}/audit-log", headers=_h(bob))
    ).status_code == 200
    assert (
        await api.get(f"/api/v1/workspaces/{ws_alice}/audit-log", headers=_h(bob))
    ).status_code == 403


async def test_role_change_rules(api: httpx.AsyncClient, owner_engine: AsyncEngine) -> None:
    owner = await _register_login(api, "own")
    admin = await _register_login(api, "adm")
    viewer = await _register_login(api, "vw")
    owner_id = (await _me(api, owner))["id"]
    admin_id = (await _me(api, admin))["id"]
    viewer_id = (await _me(api, viewer))["id"]
    ws = await _create_ws(api, owner, "Roles")
    await _add_member(owner_engine, ws, admin_id, "admin")
    await _add_member(owner_engine, ws, viewer_id, "viewer")
    url = f"/api/v1/workspaces/{ws}/members"

    # Viewer reads the member list but cannot change roles.
    r = await api.get(url, headers=_h(viewer))
    assert r.status_code == 200
    assert len(r.json()["data"]) == 3
    assert (
        await api.patch(f"{url}/{viewer_id}", headers=_h(viewer), json={"role": "editor"})
    ).status_code == 403

    # Admin promotes the viewer, but cannot mint an owner or demote one.
    r = await api.patch(f"{url}/{viewer_id}", headers=_h(admin), json={"role": "editor"})
    assert r.status_code == 200
    assert r.json()["role"] == "editor"
    assert (
        await api.patch(f"{url}/{viewer_id}", headers=_h(admin), json={"role": "owner"})
    ).status_code == 403
    assert (
        await api.patch(f"{url}/{owner_id}", headers=_h(admin), json={"role": "viewer"})
    ).status_code == 403

    # The last owner cannot step down.
    r = await api.patch(f"{url}/{owner_id}", headers=_h(owner), json={"role": "admin"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "last_owner"

    # Role changes take effect immediately, not at token expiry.
    await api.patch(f"{url}/{admin_id}", headers=_h(owner), json={"role": "viewer"})
    assert (
        await api.patch(f"{url}/{viewer_id}", headers=_h(admin), json={"role": "viewer"})
    ).status_code == 403


async def test_two_owners_demoting_each_other_concurrently_leave_one_owner(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    a = await _register_login(api, "racea")
    b = await _register_login(api, "raceb")
    a_id = (await _me(api, a))["id"]
    b_id = (await _me(api, b))["id"]
    ws = await _create_ws(api, a, "Race")
    await _add_member(owner_engine, ws, b_id, "owner")
    url = f"/api/v1/workspaces/{ws}/members"

    r1, r2 = await asyncio.gather(
        api.patch(f"{url}/{b_id}", headers=_h(a), json={"role": "admin"}),
        api.patch(f"{url}/{a_id}", headers=_h(b), json={"role": "admin"}),
    )
    # Serialised by the workspace lock: the loser is either no longer an owner (403) or
    # the last owner (409). Never both succeed.
    assert sorted([r1.status_code, r2.status_code])[0] == 200
    assert sorted([r1.status_code, r2.status_code])[1] in (403, 409)
    async with owner_engine.connect() as conn:
        owners = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM workspace_member "
                    "WHERE workspace_id = :w AND role = 'owner'"
                ),
                {"w": ws},
            )
        ).scalar_one()
    assert owners == 1


async def test_scoped_member_is_denied_workspace_level_actions(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    owner = await _register_login(api, "sco")
    scoped = await _register_login(api, "scoped")
    scoped_id = (await _me(api, scoped))["id"]
    viewer = await _register_login(api, "tgt")
    viewer_id = (await _me(api, viewer))["id"]
    ws = await _create_ws(api, owner, "Scoped")
    await _add_member(owner_engine, ws, scoped_id, "admin", restricted=True)
    await _add_member(owner_engine, ws, viewer_id, "viewer")

    # A scope-restricted admin can still read (lists filter by scope later)...
    assert (
        await api.get(f"/api/v1/workspaces/{ws}/members", headers=_h(scoped))
    ).status_code == 200
    # ...but workspace-level administration is outside any group scope.
    r = await api.patch(
        f"/api/v1/workspaces/{ws}/members/{viewer_id}", headers=_h(scoped), json={"role": "editor"}
    )
    assert r.status_code == 403


# ---- audit ----


async def test_actions_and_denials_are_audited(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    owner = await _register_login(api, "aud")
    viewer = await _register_login(api, "audv")
    viewer_id = (await _me(api, viewer))["id"]
    ws = await _create_ws(api, owner, "Audited")
    await _add_member(owner_engine, ws, viewer_id, "viewer")
    await api.patch(
        f"/api/v1/workspaces/{ws}/members/{viewer_id}", headers=_h(viewer), json={"role": "admin"}
    )
    await api.patch(
        f"/api/v1/workspaces/{ws}/members/{viewer_id}", headers=_h(owner), json={"role": "editor"}
    )

    r = await api.get(f"/api/v1/workspaces/{ws}/audit-log", headers=_h(owner))
    entries = r.json()["data"]
    actions = [e["action"] for e in entries]
    assert {"workspace.created", "permission.denied", "member.role_changed"} <= set(actions)
    change = next(e for e in entries if e["action"] == "member.role_changed")
    assert change["before"] == {"role": "viewer"}
    assert change["after"] == {"role": "editor"}
    assert change["request_id"]
    denial = next(e for e in entries if e["action"] == "permission.denied")
    assert denial["after"]["permission"] == "manage_members"

    async with owner_engine.connect() as conn:
        n = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM audit_log "
                    "WHERE action = 'auth.login_succeeded' AND actor_label = 'aud'"
                )
            )
        ).scalar_one()
    assert n >= 1


async def test_audit_log_is_append_only_even_for_the_owner(
    owner_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    for engine in (owner_engine, app_engine):
        for stmt in ("UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log"):
            with pytest.raises(DBAPIError):
                async with engine.begin() as conn:
                    await conn.execute(text(stmt))
    with pytest.raises(DBAPIError, match="append-only"):
        async with owner_engine.begin() as conn:
            await conn.execute(text("TRUNCATE audit_log"))


# ---- C8: membership discovery across RLS ----


async def test_user_sees_own_memberships_across_workspaces_and_nothing_else(
    api: httpx.AsyncClient, app_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    a = await _register_login(api, "c8a")
    b = await _register_login(api, "c8b")
    a_id = uuid.UUID((await _me(api, a))["id"])
    await _create_ws(api, a, "C8 one")
    await _create_ws(api, a, "C8 two")
    await _create_ws(api, b, "C8 other")

    async with user_transaction(app_sessionmaker, a_id) as s:
        members = (await s.execute(text("SELECT user_id FROM workspace_member"))).scalars().all()
        names = (await s.execute(text("SELECT name FROM workspace"))).scalars().all()
    assert set(members) == {a_id}
    assert sorted(names) == ["C8 one", "C8 two"]

    # The widened read never permits writes.
    with pytest.raises(DBAPIError, match="row-level security"):
        async with user_transaction(app_sessionmaker, a_id) as s:
            await s.execute(text("UPDATE workspace SET name = 'pwned'"))
            await s.execute(
                text(
                    "INSERT INTO workspace (id, name, slug) VALUES (gen_random_uuid(), 'x', :slug)"
                ),
                {"slug": _uniq("x")},
            )
