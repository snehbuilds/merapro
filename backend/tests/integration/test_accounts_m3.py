"""M3 acceptance (docs/11 §M3) plus the Sprint 4 carry-overs."""

from __future__ import annotations

import math
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from mera.application.services.account_service import AccountMaintenance
from mera.domain.context import WorkspaceContext
from mera.domain.ports.key_provider import DecryptionError
from mera.domain.value_objects.platform import Platform
from mera.infrastructure.db.models import Workspace, WorkspaceMember
from mera.infrastructure.db.session import set_workspace, workspace_transaction
from mera.workers.tasks.accounts import sweep
from tests.integration.conftest import make_api
from tests.integration.test_identity_m2 import (
    _add_member,
    _create_ws,
    _h,
    _me,
    _register_login,
    _uniq,
)

pytestmark = pytest.mark.integration


def _app(api: httpx.AsyncClient) -> Any:
    return api._transport.app  # type: ignore[attr-defined]


async def _connect(
    api: httpx.AsyncClient,
    s: dict[str, Any],
    ws: str,
    platform: str,
    *,
    account_id: str | None = None,
    handle: str | None = None,
    kind: str = "personal",
    reauthorize: str | None = None,
    redeem: bool = True,
    redeemer: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Run the redirect round trip; return the query params the web app would receive.
    Bluesky connects with an app password (P2-M3), so it takes the sign-in route and returns
    the same shapes; Mastodon names a server."""
    if platform == "bluesky" and not reauthorize:
        r = await api.post(
            f"/api/v1/workspaces/{ws}/social-accounts/connect/bluesky/sign-in",
            headers=_h(s),
            json={
                "identifier": handle or account_id or _uniq("bsky"),
                "secret": "abcd-efgh-ijkl-mnop",
            },
        )
        if r.status_code != 200:
            return {"error": r.json()["error"]["code"], "status": str(r.status_code)}
        if not redeem:
            return {"connect_code": r.json()["connect_code"]}
        r = await api.post(
            "/api/v1/social-accounts/connect/complete",
            headers=_h(redeemer or s),
            json={"connect_code": r.json()["connect_code"]},
        )
        if r.status_code == 200:
            return {"connected": r.json()["account_id"], "outcome": r.json()["outcome"]}
        return {"error": r.json()["error"]["code"], "status": str(r.status_code)}
    url = (
        f"/api/v1/workspaces/{ws}/social-accounts/{reauthorize}/reauthorize"
        if reauthorize
        else f"/api/v1/workspaces/{ws}/social-accounts/connect/{platform}"
    )
    body = {"return_path": "/w/acme/accounts"}
    if platform == "mastodon":
        body["instance"] = "mastodon.example"
    r = await api.post(url, headers=_h(s), json=body)
    assert r.status_code == 200, r.text
    state = parse_qs(urlsplit(r.json()["authorize_url"]).query)["state"][0]
    account_id = account_id or _uniq(platform)
    code = f"fake:{account_id}:{handle or account_id}:{kind}"
    r = await api.get(
        f"/api/v1/social-accounts/oauth/{platform}/callback", params={"code": code, "state": state}
    )
    assert r.status_code == 302, r.text
    loc = urlsplit(r.headers["location"])
    assert loc.path == "/w/acme/accounts"
    params = {k: v[0] for k, v in parse_qs(loc.query).items()}
    if "connect_code" not in params or not redeem:
        return params
    # The web app redeems the one-time code as the signed-in user (review M2).
    r = await api.post(
        "/api/v1/social-accounts/connect/complete",
        headers=_h(redeemer or s),
        json={"connect_code": params["connect_code"]},
    )
    if r.status_code == 200:
        return {"connected": r.json()["account_id"], "outcome": r.json()["outcome"]}
    return {"error": r.json()["error"]["code"], "status": str(r.status_code)}


async def _setup(
    api: httpx.AsyncClient, name: str, *, second_factors: bool = False
) -> tuple[dict[str, Any], str]:
    """A user and their workspace. Second factors (P2-M2) are switched off unless asked
    for, so the Phase 1 suites keep testing Phase 1 behaviour; the P2-M2 tests turn them
    on."""
    s = await _register_login(api, name)
    ws = await _create_ws(api, s, name.capitalize())
    if not second_factors:
        r = await api.put(
            f"/api/v1/workspaces/{ws}/second-factor-policy",
            headers=_h(s),
            json={
                "changes": {
                    a: False
                    for a in (
                        "kill_switch_resume",
                        "account_disconnect",
                        "company_page_approve",
                        "owner_remove",
                    )
                }
            },
        )
        assert r.status_code == 200, r.text
    return s, ws


def _acc(ws: str, account_id: str = "") -> str:
    return f"/api/v1/workspaces/{ws}/social-accounts" + (f"/{account_id}" if account_id else "")


def _grp(ws: str, group_id: str = "") -> str:
    return f"/api/v1/workspaces/{ws}/account-groups" + (f"/{group_id}" if group_id else "")


# ------------------------------------------------------------------ connecting


async def test_workspace_oauth_app_is_private_and_used_for_new_connections(
    api: httpx.AsyncClient,
    owner_engine: AsyncEngine,
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    owner, ws = await _setup(api, "ownoauth")
    editor = await _register_login(api, "editoauth")
    await _add_member(owner_engine, ws, (await _me(api, editor))["id"], "editor")
    endpoint = f"/api/v1/workspaces/{ws}/social-oauth-apps"
    payload = {"client_id": "test-workspace-client", "client_secret": "test-workspace-secret"}
    assert (await api.put(f"{endpoint}/x", headers=_h(editor), json=payload)).status_code == 403
    assert (await api.put(f"{endpoint}/x", headers=_h(owner), json=payload)).status_code == 204
    status = await api.get(endpoint, headers=_h(owner))
    assert status.status_code == 200
    x_app = next(app for app in status.json()["data"] if app["provider"] == "x")
    assert x_app["source"] == "workspace"
    assert all(value not in status.text for value in payload.values())
    detail = await api.get(f"/api/v1/workspaces/{ws}", headers=_h(owner))
    assert "oauth_apps_ct" not in detail.json()
    async with workspace_transaction(app_sessionmaker, WorkspaceContext(uuid.UUID(ws))) as session:
        workspace = await session.get(Workspace, uuid.UUID(ws))
        assert workspace is not None
        assert workspace.oauth_apps_ct["x"].startswith("mera:enc:")
        assert all(value not in workspace.oauth_apps_ct["x"] for value in payload.values())

    # Remove the sandbox connector for X to exercise the workspace OAuth path with no
    # deployment-wide X credentials. Building the consent URL does not call the platform.
    connectors = _app(api).state.connectors
    fake = connectors.pop(Platform.X)
    try:
        response = await api.post(f"{_acc(ws)}/connect/x", headers=_h(owner), json={})
        assert response.status_code == 200, response.text
        query = parse_qs(urlsplit(response.json()["authorize_url"]).query)
        assert query["client_id"] == [payload["client_id"]]
        assert "client_secret" not in query
    finally:
        connectors[Platform.X] = fake


async def test_five_accounts_coexist_with_separately_bound_credentials(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    s, ws = await _setup(api, "multi")
    ids = []
    for platform, kind in [
        ("linkedin_member", "personal"),
        ("linkedin_member", "personal"),
        ("x", "personal"),
        ("x", "business"),
        ("facebook", "page"),
    ]:
        result = await _connect(api, s, ws, platform, kind=kind)
        ids.append(result["connected"])
    r = await api.get(_acc(ws), headers=_h(s))
    listed = r.json()["data"]
    assert sorted(a["id"] for a in listed) == sorted(ids)
    assert all("credentials" not in str(a) for a in listed)  # never returned
    assert {a["platform"] for a in listed} == {"linkedin_member", "x", "facebook"}

    # Each ciphertext is bound to its own row: swapped onto another account it won't open.
    async with owner_engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT id, credentials_ct FROM social_account WHERE workspace_id = :w"),
                {"w": ws},
            )
        ).all()
    assert len({ct for _, ct in rows}) == 5
    kp = _app(api).state.key_provider
    (a_id, a_ct), (b_id, _) = rows[0], rows[1]
    assert kp.decrypt(
        a_ct, workspace_id=uuid.UUID(ws), purpose="social_account.credentials", record_id=a_id
    )
    with pytest.raises(DecryptionError):
        kp.decrypt(
            a_ct, workspace_id=uuid.UUID(ws), purpose="social_account.credentials", record_id=b_id
        )


async def test_duplicate_connection_returns_the_existing_account(api: httpx.AsyncClient) -> None:
    s, ws = await _setup(api, "dupe")
    first = await _connect(api, s, ws, "x", account_id="x-123")
    second = await _connect(api, s, ws, "x", account_id="x-123")
    assert second["error"] == "account_already_connected"
    assert second["status"] == "409"
    assert first["outcome"] == "connected"
    assert len((await api.get(_acc(ws), headers=_h(s))).json()["data"]) == 1


async def test_connect_state_is_single_use_and_platform_bound(api: httpx.AsyncClient) -> None:
    s, ws = await _setup(api, "state")
    r = await api.post(
        f"{_acc(ws)}/connect/threads", headers=_h(s), json={"return_path": "/w/x/accounts"}
    )
    state = parse_qs(urlsplit(r.json()["authorize_url"]).query)["state"][0]
    # Presented to the wrong platform's callback: unknown.
    r = await api.get(
        "/api/v1/social-accounts/oauth/x/callback",
        params={"code": "fake:a:b:personal", "state": state},
    )
    assert r.status_code == 422
    ok = await api.get(
        "/api/v1/social-accounts/oauth/threads/callback",
        params={"code": "fake:th-1:th:personal", "state": state},
    )
    assert ok.status_code == 302
    again = await api.get(
        "/api/v1/social-accounts/oauth/threads/callback",
        params={"code": "fake:th-1:th:personal", "state": state},
    )
    assert again.status_code == 422
    # Open redirects are refused up front.
    r = await api.post(
        f"{_acc(ws)}/connect/threads", headers=_h(s), json={"return_path": "//evil.example"}
    )
    assert r.json()["error"]["code"] == "invalid_return_path"


async def test_authority_is_rechecked_when_the_platform_returns(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    owner, ws = await _setup(api, "recheck")
    admin = await _register_login(api, "recheckadm")
    admin_id = (await _me(api, admin))["id"]
    await _add_member(owner_engine, ws, admin_id, "admin")
    r = await api.post(
        f"{_acc(ws)}/connect/x", headers=_h(admin), json={"return_path": "/w/a/accounts"}
    )
    state = parse_qs(urlsplit(r.json()["authorize_url"]).query)["state"][0]
    # Demoted while away at the platform.
    await api.patch(
        f"/api/v1/workspaces/{ws}/members/{admin_id}", headers=_h(owner), json={"role": "editor"}
    )
    r = await api.get(
        "/api/v1/social-accounts/oauth/x/callback",
        params={"code": "fake:x-9:x9:personal", "state": state},
    )
    code = parse_qs(urlsplit(r.headers["location"]).query)["connect_code"][0]
    r = await api.post(
        "/api/v1/social-accounts/connect/complete", headers=_h(admin), json={"connect_code": code}
    )
    assert r.status_code == 403
    assert (await api.get(_acc(ws), headers=_h(owner))).json()["data"] == []


async def test_only_admins_connect_and_the_limit_holds(
    postgres_urls: tuple[str, str], owner_engine: AsyncEngine
) -> None:
    async with make_api(
        postgres_urls, rate_limit_enabled=False, max_accounts_per_workspace=2
    ) as api:
        owner, ws = await _setup(api, "limit")
        editor = await _register_login(api, "limited")
        await _add_member(owner_engine, ws, (await _me(api, editor))["id"], "editor")
        r = await api.post(f"{_acc(ws)}/connect/x", headers=_h(editor), json={})
        assert r.status_code == 403
        await _connect(api, owner, ws, "x")
        await _connect(api, owner, ws, "bluesky")
        r = await api.post(f"{_acc(ws)}/connect/mastodon", headers=_h(owner), json={})
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "account_limit_reached"


# ------------------------------------------------------------------ groups


async def test_overlapping_groups_and_group_deletion(api: httpx.AsyncClient) -> None:
    s, ws = await _setup(api, "groups")
    page = (await _connect(api, s, ws, "linkedin_member"))["connected"]
    x1 = (await _connect(api, s, ws, "x"))["connected"]
    tech = (
        await api.post(
            _grp(ws), headers=_h(s), json={"name": "Tech News", "social_account_ids": [page, x1]}
        )
    ).json()
    corp = (
        await api.post(
            _grp(ws), headers=_h(s), json={"name": "Company Official", "social_account_ids": [page]}
        )
    ).json()
    assert [m["social_account_id"] for m in tech["members"]] == [page, x1]
    account = (await api.get(_acc(ws, page), headers=_h(s))).json()
    assert sorted(account["group_ids"]) == sorted([tech["id"], corp["id"]])

    # Remove from one group: the other membership is intact.
    r = await api.delete(f"{_grp(ws, tech['id'])}/members/{page}", headers=_h(s))
    assert [m["social_account_id"] for m in r.json()["members"]] == [x1]
    assert (await api.get(_acc(ws, page), headers=_h(s))).json()["group_ids"] == [corp["id"]]

    # Delete a group: its accounts stay connected.
    assert (await api.delete(_grp(ws, corp["id"]), headers=_h(s))).status_code == 204
    account = (await api.get(_acc(ws, page), headers=_h(s))).json()
    assert account["status"] == "active"
    assert account["group_ids"] == []
    r = await api.post(_grp(ws), headers=_h(s), json={"name": "Tech News"})
    assert r.json()["error"]["code"] == "group_name_taken"


async def test_member_order_drives_position(api: httpx.AsyncClient) -> None:
    s, ws = await _setup(api, "order")
    a, b, c = [(await _connect(api, s, ws, p))["connected"] for p in ("x", "bluesky", "mastodon")]
    g = (
        await api.post(_grp(ws), headers=_h(s), json={"name": "G", "social_account_ids": [a, b, c]})
    ).json()
    r = await api.patch(f"{_grp(ws, g['id'])}/members/{c}", headers=_h(s), json={"position": 0})
    assert [m["social_account_id"] for m in r.json()["members"]] == [c, a, b]
    assert [m["position"] for m in r.json()["members"]] == [0, 1, 2]
    r = await api.patch(
        f"{_grp(ws, g['id'])}/members/{a}", headers=_h(s), json={"enabled_by_default": False}
    )
    assert [m["enabled_by_default"] for m in r.json()["members"]] == [True, False, True]
    r = await api.put(
        f"{_grp(ws, g['id'])}/members", headers=_h(s), json={"social_account_ids": [b, a]}
    )
    assert [m["social_account_id"] for m in r.json()["members"]] == [b, a]
    r = await api.put(
        f"{_grp(ws, g['id'])}/members", headers=_h(s), json={"social_account_ids": [b, b]}
    )
    assert r.json()["error"]["code"] == "duplicate_account"


async def test_stagger_is_required_with_two_accounts_on_one_platform(
    api: httpx.AsyncClient,
) -> None:
    s, ws = await _setup(api, "stagger")
    x1 = (await _connect(api, s, ws, "x"))["connected"]
    x2 = (await _connect(api, s, ws, "x"))["connected"]
    g = (
        await api.post(
            _grp(ws), headers=_h(s), json={"name": "Two X", "social_account_ids": [x1, x2]}
        )
    ).json()
    r = await api.patch(_grp(ws, g["id"]), headers=_h(s), json={"stagger_mode": "none"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "stagger_required"
    assert "2 X accounts" in r.json()["error"]["message"]
    # The other path to the same state is refused too.
    solo = (
        await api.post(
            _grp(ws),
            headers=_h(s),
            json={"name": "Solo", "stagger_mode": "none", "social_account_ids": [x1]},
        )
    ).json()
    r = await api.post(
        f"{_grp(ws, solo['id'])}/members", headers=_h(s), json={"social_account_ids": [x2]}
    )
    assert r.json()["error"]["code"] == "stagger_required"
    r = await api.patch(
        _grp(ws, g["id"]),
        headers=_h(s),
        json={"stagger_min_seconds": 1000, "stagger_max_seconds": 10},
    )
    assert r.json()["error"]["code"] == "invalid_stagger_bounds"


# ------------------------------------------------------------------ settings resolution


async def test_resolved_settings_report_value_and_level(api: httpx.AsyncClient) -> None:
    s, ws = await _setup(api, "resolve")
    page = (await _connect(api, s, ws, "linkedin_member"))["connected"]
    g = (
        await api.post(_grp(ws), headers=_h(s), json={"name": "G", "social_account_ids": [page]})
    ).json()
    await api.patch(
        f"/api/v1/workspaces/{ws}",
        headers=_h(s),
        json={"settings": {"daily_post_cap": 4, "required_approvers": 1}},
    )
    await api.patch(
        _grp(ws, g["id"]),
        headers=_h(s),
        json={"settings": {"required_approvers": 2, "daily_post_cap": 6}},
    )
    await api.patch(
        _acc(ws, page),
        headers=_h(s),
        json={"settings": {"daily_post_cap": 3, "required_approvers": 1}},
    )

    r = await api.get(f"{_acc(ws, page)}/settings", headers=_h(s), params={"via_group_id": g["id"]})
    got = {row["key"]: row for row in r.json()["data"]}
    assert (got["daily_post_cap"]["value"], got["daily_post_cap"]["source_level"]) == (3, "account")
    # Strictest wins: the group's two approvers survive the account's one.
    assert (got["required_approvers"]["value"], got["required_approvers"]["source_level"]) == (
        2,
        "group",
    )
    assert got["required_approvers"]["rule"] == "strictest"
    assert (got["autopilot_mode"]["value"], got["autopilot_mode"]["source_level"]) == (
        "manual",
        "system",
    )

    # Without the targeting group, the group level does not apply.
    r = await api.get(f"{_acc(ws, page)}/settings", headers=_h(s))
    got = {row["key"]: row for row in r.json()["data"]}
    assert got["required_approvers"]["value"] == 1
    r = await api.get(f"{_grp(ws, g['id'])}/settings", headers=_h(s))
    got = {row["key"]: row for row in r.json()["data"]}
    # Strictest wins for caps too (Sprint 8): the workspace's 4 beats the group's 6.
    assert (got["daily_post_cap"]["value"], got["daily_post_cap"]["source_level"]) == (
        4,
        "workspace",
    )

    # Clearing an override falls back; Phase 1 refuses non-manual autopilot.
    await api.patch(_acc(ws, page), headers=_h(s), json={"settings": {"daily_post_cap": None}})
    r = await api.get(f"{_acc(ws, page)}/settings", headers=_h(s), params={"via_group_id": g["id"]})
    assert {row["key"]: row for row in r.json()["data"]}["daily_post_cap"][
        "source_level"
    ] == "workspace"
    r = await api.patch(
        _acc(ws, page), headers=_h(s), json={"settings": {"autopilot_mode": "full"}}
    )
    assert r.status_code == 400


# ------------------------------------------------------------------ health, reauth, reconnect


async def test_revoked_token_pauses_only_that_account_and_reconnect_preserves_it(
    api: httpx.AsyncClient,
) -> None:
    s, ws = await _setup(api, "reauth")
    bad = (await _connect(api, s, ws, "x", handle="revoked_soon"))["connected"]
    good = (await _connect(api, s, ws, "x", handle="fine"))["connected"]
    g = (
        await api.post(
            _grp(ws), headers=_h(s), json={"name": "Pair", "social_account_ids": [bad, good]}
        )
    ).json()

    r = await api.post(f"{_acc(ws, bad)}/health/check", headers=_h(s))
    assert r.json()["status"] == "needs_reauth"
    assert (await api.post(f"{_acc(ws, good)}/health/check", headers=_h(s))).json()[
        "status"
    ] == "active"
    notes = (await api.get(f"/api/v1/workspaces/{ws}/notifications", headers=_h(s))).json()["data"]
    assert [n["kind"] for n in notes] == ["account.needs_reauth"]
    assert notes[0]["social_account_id"] == bad
    assert (
        await api.post(
            f"/api/v1/workspaces/{ws}/notifications/{notes[0]['id']}/read", headers=_h(s)
        )
    ).status_code == 204
    unread = await api.get(
        f"/api/v1/workspaces/{ws}/notifications", headers=_h(s), params={"unread_only": True}
    )
    assert unread.json()["data"] == []

    # Reconnecting a *different* account than the one being repaired is refused...
    r = await _connect(api, s, ws, "x", account_id="someone-else", reauthorize=bad)
    assert r["error"] == "account_mismatch"
    platform_id = (await api.get(_acc(ws, bad), headers=_h(s))).json()["platform_account_id"]
    # ...the right one keeps its id, groups and history.
    r = await _connect(api, s, ws, "x", account_id=platform_id, handle="restored", reauthorize=bad)
    assert r["connected"] == bad
    account = (await api.get(_acc(ws, bad), headers=_h(s))).json()
    assert account["status"] == "active"
    assert account["group_ids"] == [g["id"]]


async def test_sweep_refreshes_due_tokens(
    api: httpx.AsyncClient,
    owner_engine: AsyncEngine,
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    s, ws = await _setup(api, "sweep")
    acct = (await _connect(api, s, ws, "mastodon"))["connected"]
    async with owner_engine.begin() as conn:
        before = (
            await conn.execute(
                text(
                    "UPDATE social_account SET token_issued_at = now() - interval '2 hours', "
                    "token_expires_at = now() + interval '10 minutes', "
                    "last_health_check_at = now() - interval '7 hours' "
                    "WHERE id = :i RETURNING credentials_ct"
                ),
                {"i": acct},
            )
        ).scalar_one()
    app = _app(api)
    maintenance = AccountMaintenance(app_sessionmaker, app.state.connectors, app.state.key_provider)
    outcomes = await sweep(app_sessionmaker, maintenance)
    assert outcomes.get("refresh:refreshed", 0) >= 1
    assert outcomes.get("health:checked", 0) >= 1
    async with owner_engine.connect() as conn:
        after, expires_ok = (
            await conn.execute(
                text(
                    "SELECT credentials_ct, token_expires_at > now() + interval '30 minutes' "
                    "FROM social_account WHERE id = :i"
                ),
                {"i": acct},
            )
        ).one()
    assert after != before
    assert expires_ok


async def test_disconnect_revokes_at_the_platform_first(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    s, ws = await _setup(api, "disc")
    acct = (await _connect(api, s, ws, "bluesky", handle="gone_handle"))["connected"]
    assert (await api.delete(_acc(ws, acct), headers=_h(s))).status_code == 204
    assert "gone_handle" in _app(api).state.connectors["bluesky"].revoked
    account = (await api.get(_acc(ws, acct), headers=_h(s))).json()
    assert account["status"] == "disconnected"
    async with owner_engine.connect() as conn:
        ct = (
            await conn.execute(
                text("SELECT credentials_ct FROM social_account WHERE id = :i"), {"i": acct}
            )
        ).scalar_one()
    assert ct is None


# ------------------------------------------------------------------ member scopes (2.9b)


async def test_scoped_editor_sees_and_manages_only_their_group(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    owner, ws = await _setup(api, "scopes")
    editor = await _register_login(api, "scoped_ed")
    editor_id = (await _me(api, editor))["id"]
    await _add_member(owner_engine, ws, editor_id, "editor")
    a = (await _connect(api, owner, ws, "x"))["connected"]
    b = (await _connect(api, owner, ws, "bluesky"))["connected"]
    ga = (
        await api.post(_grp(ws), headers=_h(owner), json={"name": "A", "social_account_ids": [a]})
    ).json()
    gb = (
        await api.post(_grp(ws), headers=_h(owner), json={"name": "B", "social_account_ids": [b]})
    ).json()

    url = f"/api/v1/workspaces/{ws}/members/{editor_id}/scopes"
    r = await api.put(url, headers=_h(owner), json={"account_group_ids": [ga["id"]]})
    assert r.json() == {
        "restricted": True,
        "account_group_ids": [ga["id"]],
        "social_account_ids": [],
    }
    assert [x["id"] for x in (await api.get(_acc(ws), headers=_h(editor))).json()["data"]] == [a]
    assert [x["id"] for x in (await api.get(_grp(ws), headers=_h(editor))).json()["data"]] == [
        ga["id"]
    ]
    assert (await api.get(_acc(ws, b), headers=_h(editor))).status_code == 403
    assert (
        await api.patch(_grp(ws, ga["id"]), headers=_h(editor), json={"description": "ok"})
    ).status_code == 200
    assert (
        await api.patch(_grp(ws, gb["id"]), headers=_h(editor), json={"description": "no"})
    ).status_code == 403
    # Creating a group is outside any group scope.
    assert (await api.post(_grp(ws), headers=_h(editor), json={"name": "C"})).status_code == 403

    r = await api.put(url, headers=_h(owner), json={"account_group_ids": [str(uuid.uuid4())]})
    assert r.json()["error"]["code"] == "unknown_scope_subject"
    owner_id = (await _me(api, owner))["id"]
    r = await api.put(
        f"/api/v1/workspaces/{ws}/members/{owner_id}/scopes",
        headers=_h(owner),
        json={"social_account_ids": [a]},
    )
    assert r.json()["error"]["code"] == "invalid_scope_target"
    # Clearing restores the whole workspace.
    r = await api.put(url, headers=_h(owner), json={})
    assert r.json()["restricted"] is False
    assert len((await api.get(_acc(ws), headers=_h(editor))).json()["data"]) == 2


# ------------------------------------------------------------------ carry-overs


async def test_web_surface_needs_the_proxy_secret(
    postgres_urls: tuple[str, str], owner_engine: AsyncEngine
) -> None:
    async with make_api(
        postgres_urls, rate_limit_enabled=False, web_proxy_secret="proxy-shared-secret"
    ) as api:
        s, ws = await _setup(api, "surface")
        url = f"/api/v1/workspaces/{ws}"
        await api.patch(
            url,
            headers={**_h(s), "X-Mera-Proxy-Secret": "proxy-shared-secret"},
            json={"name": "Web"},
        )
        await api.patch(
            url, headers={**_h(s), "X-Mera-Proxy-Secret": "guess"}, json={"name": "Spoof"}
        )
        await api.patch(url, headers={**_h(s), "X-Mera-Client": "web"}, json={"name": "Old header"})
    async with owner_engine.connect() as conn:
        surfaces = (
            (
                await conn.execute(
                    text(
                        "SELECT surface FROM audit_log WHERE workspace_id = :w "
                        "AND action = 'workspace.updated' ORDER BY id"
                    ),
                    {"w": ws},
                )
            )
            .scalars()
            .all()
        )
    assert surfaces == ["web", "api", "api"]


async def test_denials_cannot_flood_the_audit_log(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    _owner, ws = await _setup(api, "flood")
    viewer = await _register_login(api, "flooder")
    await _add_member(owner_engine, ws, (await _me(api, viewer))["id"], "viewer")
    started = time.monotonic()
    for _ in range(35):
        assert (
            await api.get(f"/api/v1/workspaces/{ws}/audit-log", headers=_h(viewer))
        ).status_code == 403
    elapsed = time.monotonic() - started
    async with owner_engine.connect() as conn:
        n = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM audit_log "
                    "WHERE workspace_id = :w AND action = 'permission.denied'"
                ),
                {"w": ws},
            )
        ).scalar_one()
    # 30 a minute, refilling at half a token a second while the requests run (a loaded
    # machine runs them slower and earns a few more, but never all 35).
    assert 30 <= n <= 30 + math.ceil(elapsed * 0.5) + 1
    assert n < 35


async def test_commit_mid_transaction_cannot_lose_the_tenant_context(
    api: httpx.AsyncClient, app_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    _, ws = await _setup(api, "commit")
    # Inside workspace_transaction a stray commit fails loudly instead of continuing.
    with pytest.raises(InvalidRequestError):
        async with workspace_transaction(app_sessionmaker, WorkspaceContext(uuid.UUID(ws))) as s:
            await s.commit()
            await s.execute(select(WorkspaceMember))
    # A plain session that commits and autobegins keeps its context (after_begin listener).
    async with app_sessionmaker() as s:
        await set_workspace(s, WorkspaceContext(uuid.UUID(ws)))
        await s.commit()
        rows = (await s.execute(select(WorkspaceMember))).scalars().all()
        await s.rollback()
    assert len(rows) == 1


# ------------------------------------------------------------------ Sprint 4 review regressions


async def test_a_forwarded_consent_link_cannot_attach_the_victims_account(
    api: httpx.AsyncClient,
) -> None:
    attacker, ws = await _setup(api, "phisher")
    victim = await _register_login(api, "victim4")
    # The attacker starts a flow and the victim completes consent and redemption.
    result = await _connect(
        api, attacker, ws, "linkedin_member", account_id="victim-li", redeemer=victim
    )
    assert result == {"error": "invalid_connect_code", "status": "404"}
    assert (await api.get(_acc(ws), headers=_h(attacker))).json()["data"] == []


async def test_connect_code_is_single_use(api: httpx.AsyncClient) -> None:
    s, ws = await _setup(api, "codeonce")
    params = await _connect(api, s, ws, "bluesky", redeem=False)
    body = {"connect_code": params["connect_code"]}
    first = await api.post("/api/v1/social-accounts/connect/complete", headers=_h(s), json=body)
    again = await api.post("/api/v1/social-accounts/connect/complete", headers=_h(s), json=body)
    assert first.status_code == 200
    assert again.status_code == 404


async def test_reactivating_a_disconnected_account_respects_the_limit(
    postgres_urls: tuple[str, str],
) -> None:
    async with make_api(
        postgres_urls, rate_limit_enabled=False, max_accounts_per_workspace=1
    ) as api:
        s, ws = await _setup(api, "reactlimit")
        a = await _connect(api, s, ws, "x", account_id="x-a")
        await api.delete(_acc(ws, a["connected"]), headers=_h(s))
        await _connect(api, s, ws, "bluesky")  # the one slot is now taken
        r = await api.post(
            f"{_acc(ws, a['connected'])}/reauthorize",
            headers=_h(s),
            json={"return_path": "/w/x/accounts"},
        )
        assert r.status_code == 409
        # A fresh connect is refused before the user is even sent to the platform.
        r = await api.post(f"{_acc(ws)}/connect/x", headers=_h(s), json={})
        assert r.json()["error"]["code"] == "account_limit_reached"


async def test_reconnecting_cannot_break_stagger_required(api: httpx.AsyncClient) -> None:
    s, ws = await _setup(api, "stagger2")
    x1 = (await _connect(api, s, ws, "x", account_id="x-one"))["connected"]
    x2 = (await _connect(api, s, ws, "x", account_id="x-two"))["connected"]
    await api.delete(_acc(ws, x2), headers=_h(s))
    g = (
        await api.post(
            _grp(ws),
            headers=_h(s),
            json={"name": "Once", "stagger_mode": "none", "social_account_ids": [x1, x2]},
        )
    ).json()
    assert g["stagger_mode"] == "none"  # one active X account: allowed
    result = await _connect(api, s, ws, "x", account_id="x-two", reauthorize=x2)
    assert result["error"] == "stagger_required"
    assert (await api.get(_acc(ws, x2), headers=_h(s))).json()["status"] == "disconnected"


async def test_suspended_accounts_are_not_silently_reactivated(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    s, ws = await _setup(api, "susp")
    a = (await _connect(api, s, ws, "mastodon", account_id="m-1"))["connected"]
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE social_account SET status = 'suspended' WHERE id = :i"), {"i": a}
        )
    result = await _connect(api, s, ws, "mastodon", account_id="m-1", reauthorize=a)
    assert result["error"] == "account_suspended"


async def test_scoped_member_cannot_read_out_of_scope_group_settings(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    owner, ws = await _setup(api, "viascope")
    viewer = await _register_login(api, "viascoped")
    viewer_id = (await _me(api, viewer))["id"]
    await _add_member(owner_engine, ws, viewer_id, "viewer")
    a = (await _connect(api, owner, ws, "x"))["connected"]
    secret_group = (
        await api.post(
            _grp(ws), headers=_h(owner), json={"name": "Hidden", "social_account_ids": [a]}
        )
    ).json()
    await api.put(
        f"/api/v1/workspaces/{ws}/members/{viewer_id}/scopes",
        headers=_h(owner),
        json={"social_account_ids": [a]},
    )
    account = (await api.get(_acc(ws, a), headers=_h(viewer))).json()
    assert account["group_ids"] == []  # the out-of-scope group is not disclosed
    r = await api.get(
        f"{_acc(ws, a)}/settings", headers=_h(viewer), params={"via_group_id": secret_group["id"]}
    )
    assert r.status_code == 403
    # Checking health is an admin action now.
    assert (await api.post(f"{_acc(ws, a)}/health/check", headers=_h(viewer))).status_code == 403
    # Only the member themself or an admin can read their scopes.
    other = await _register_login(api, "viaother")
    other_id = (await _me(api, other))["id"]
    await _add_member(owner_engine, ws, other_id, "viewer")
    assert (
        await api.get(f"/api/v1/workspaces/{ws}/members/{other_id}/scopes", headers=_h(viewer))
    ).status_code == 403
    assert (
        await api.get(f"/api/v1/workspaces/{ws}/members/{viewer_id}/scopes", headers=_h(viewer))
    ).status_code == 200


async def test_scoped_member_sees_notices_for_accounts_reached_through_groups(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    owner, ws = await _setup(api, "noticescope")
    viewer = await _register_login(api, "noticeviewer")
    viewer_id = (await _me(api, viewer))["id"]
    await _add_member(owner_engine, ws, viewer_id, "viewer")
    mine = (await _connect(api, owner, ws, "x", handle="revoked_mine"))["connected"]
    theirs = (await _connect(api, owner, ws, "bluesky", handle="revoked_theirs"))["connected"]
    g = (
        await api.post(
            _grp(ws), headers=_h(owner), json={"name": "Mine", "social_account_ids": [mine]}
        )
    ).json()
    await api.put(
        f"/api/v1/workspaces/{ws}/members/{viewer_id}/scopes",
        headers=_h(owner),
        json={"account_group_ids": [g["id"]]},
    )
    for account in (mine, theirs):
        await api.post(f"{_acc(ws, account)}/health/check", headers=_h(owner))
    seen = (await api.get(f"/api/v1/workspaces/{ws}/notifications", headers=_h(viewer))).json()[
        "data"
    ]
    assert [n["social_account_id"] for n in seen] == [mine]
    hidden = next(
        n["id"]
        for n in (
            await api.get(f"/api/v1/workspaces/{ws}/notifications", headers=_h(owner))
        ).json()["data"]
        if n["social_account_id"] == theirs
    )
    r = await api.post(f"/api/v1/workspaces/{ws}/notifications/{hidden}/read", headers=_h(viewer))
    assert r.status_code == 404


async def test_a_refresh_does_not_overwrite_a_newer_reconnect(
    api: httpx.AsyncClient,
    owner_engine: AsyncEngine,
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    s, ws = await _setup(api, "race")
    acct = (await _connect(api, s, ws, "x", account_id="x-race"))["connected"]
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE social_account SET token_issued_at = now() - interval '2 hours', "
                "token_expires_at = now() + interval '5 minutes' WHERE id = :i"
            ),
            {"i": acct},
        )
    app = _app(api)
    connector = app.state.connectors["x"]
    original = connector.refresh

    async def refresh_during_reconnect(creds: dict[str, Any]) -> Any:
        # An admin reconnects while the platform refresh call is in flight.
        await _connect(api, s, ws, "x", account_id="x-race", reauthorize=acct)
        return await original(creds)

    connector.refresh = refresh_during_reconnect
    try:
        maintenance = AccountMaintenance(
            app_sessionmaker, app.state.connectors, app.state.key_provider
        )
        assert await maintenance.refresh_if_due(uuid.UUID(ws), uuid.UUID(acct)) == "superseded"
    finally:
        connector.refresh = original
