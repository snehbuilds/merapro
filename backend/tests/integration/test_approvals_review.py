"""Regression tests for the Sprint 8 security review (S8-H1..M6, L8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.test_accounts_m3 import _grp, _setup
from tests.integration.test_approvals_m6 import (
    _ar,
    _by,
    _decide,
    _edit,
    _four,
    _publishable,
    _submit,
)
from tests.integration.test_drafting_m5 import (
    CapturingDeferrer,
    _sets,
    deferrer,  # noqa: F401 - shared fixture
)
from tests.integration.test_identity_m2 import _add_member, _h, _me, _register_login
from tests.integration.test_sprint3 import _key, _kh

pytestmark = pytest.mark.integration


async def test_leaving_a_stopped_group_does_not_escape_its_kill_switch(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, owner_engine: AsyncEngine
) -> None:
    s, ws = await _setup(api, "rev-h1")
    ps, acc, groups = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    r = await _decide(api, s, ws, req, "approve", [by["th"]])
    assert r.status_code == 200, r.text
    ks = f"/api/v1/workspaces/{ws}/automation/kill-switch"
    r = await api.post(
        ks,
        headers=_h(s),
        json={"scope_type": "account_group", "scope_id": groups["social"], "engaged": True},
    )
    assert r.status_code == 200
    # An Editor may no longer pull an account out of a stopped group...
    editor = await _register_login(api, "rev-h1-ed")
    await _add_member(owner_engine, ws, (await _me(api, editor))["id"], "editor")
    r = await api.delete(f"{_grp(ws, groups['social'])}/members/{acc['th']}", headers=_h(editor))
    assert r.status_code == 403
    r = await api.delete(_grp(ws, groups["social"]), headers=_h(editor))
    assert r.status_code == 403
    # ...and even when an Admin does, the stop recorded at submission still holds.
    r = await api.delete(f"{_grp(ws, groups['social'])}/members/{acc['th']}", headers=_h(s))
    assert r.status_code in (200, 204), r.text
    assert await _publishable(api, ws, by["th"]["post_variant_id"]) == "kill_switch_engaged"
    r = await _decide(api, s, ws, req, "approve", [by["xx"]])
    assert (r.status_code, r.json()["error"]["code"]) == (409, "kill_switch_engaged")


async def test_settings_only_approver_count_keeps_default_timers(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "rev-h2")
    r = await api.patch(
        f"/api/v1/workspaces/{ws}", headers=_h(s), json={"settings": {"required_approvers": 2}}
    )
    assert r.status_code == 200, r.text
    ps, _acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    assert all(v["required"] == 2 for v in req["variants"])
    expires = datetime.fromisoformat(req["expires_at"])
    assert expires - datetime.now(UTC) <= timedelta(hours=12, minutes=1)


async def test_editing_an_approved_account_keeps_the_request_honest(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "rev-m5")
    ps, acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    assert (await _decide(api, s, ws, req, "approve", [by["li"]])).status_code == 200
    await _edit(api, s, ws, ps["id"], by["li"]["post_variant_id"])
    now = (await api.get(_ar(ws, req["id"]), headers=_h(s))).json()
    r = await _decide(api, s, ws, now, "approve", [by["fb"], by["th"], by["xx"]])
    assert r.status_code == 200, r.text
    # LinkedIn's approval covered its old text, so the request is not "approved".
    assert r.json()["state"] == "partially_approved"
    assert (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()["state"] != "approved"


async def test_api_keys_cannot_supply_a_human_approval(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "rev-m6")
    ps, _acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    key = await _key(api, s, ws, ["approvals:read", "approvals:decide"])
    r = await api.post(
        _ar(ws, req["id"], "/decisions"),
        headers=_kh(key["secret"]),
        json={
            "action": "approve",
            "variant_scope": None,
            "content_hashes": {v["post_variant_id"]: v["content_hash"] for v in req["variants"]},
        },
    )
    assert (r.status_code, r.json()["error"]["code"]) == (403, "human_approval_required")
    assert (await api.get(_ar(ws, req["id"]), headers=_kh(key["secret"]))).status_code == 200


async def test_scoped_members_see_only_their_accounts_decisions(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, owner_engine: AsyncEngine
) -> None:
    s, ws = await _setup(api, "rev-m4")
    ps, acc, groups = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    r = await api.post(
        _ar(ws, req["id"], "/decisions"),
        headers=_h(s),
        json={
            "action": "reject",
            "variant_scope": [by["xx"]["post_variant_id"]],
            "content_hashes": {by["xx"]["post_variant_id"]: by["xx"]["content_hash"]},
            "reason": "secret reason about X",
        },
    )
    assert r.status_code == 200
    scoped = await _register_login(api, "rev-m4-sc")
    sid = (await _me(api, scoped))["id"]
    await _add_member(owner_engine, ws, sid, "approver")
    await api.put(
        f"/api/v1/workspaces/{ws}/members/{sid}/scopes",
        headers=_h(s),
        json={"account_group_ids": [groups["work"]]},
    )
    seen = (await api.get(_ar(ws, req["id"]), headers=_h(scoped))).json()
    assert seen["decisions"] == []
    assert seen["content_hash"] == ""
    assert "secret reason" not in str(seen)


async def test_expiry_is_recorded_and_frees_the_set_for_resubmission(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, owner_engine: AsyncEngine
) -> None:
    s, ws = await _setup(api, "rev-l8")
    ps, acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    assert (await _decide(api, s, ws, req, "approve", [by["li"]])).status_code == 200
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE approval_request SET expires_at = now() - interval '1 minute' WHERE id = :i"
            ),
            {"i": req["id"]},
        )
    r = await _decide(api, s, ws, req, "approve", [by["fb"]])
    assert (r.status_code, r.json()["error"]["code"]) == (410, "approval_expired")
    got = (await api.get(_ar(ws, req["id"]), headers=_h(s))).json()
    states = {k: v["state"] for k, v in _by(got, acc).items()}
    assert states == {"li": "approved", "fb": "expired", "th": "expired", "xx": "expired"}
    assert got["state"] == "partially_approved"
    # Expiry never publishes the expired accounts; the approved one still can.
    assert await _publishable(api, ws, by["fb"]["post_variant_id"]) == "not_approved"
    assert await _publishable(api, ws, by["li"]["post_variant_id"]) is None
    r = await api.post(
        _sets(ws, ps["id"], "/request-approval"),
        headers=_h(s),
        json={"scheduled_for": "2020-01-01T00:00:00Z"},
    )
    assert (r.status_code, r.json()["error"]["code"]) == (422, "invalid_scheduled_for")
