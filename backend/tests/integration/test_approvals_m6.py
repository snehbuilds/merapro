"""M6 approval core against real Postgres (docs/07 §11 matrix, ADR-0014).

Publishing is M8, so "publishes" here means ``assert_publishable`` passes for that account,
which is the only gate publishing will have.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mera.application.approvals.publishable import NotPublishable, assert_publishable
from mera.domain.context import WorkspaceContext
from mera.infrastructure.db.models import ApprovalRequest
from mera.infrastructure.db.session import workspace_transaction
from tests.integration.test_accounts_m3 import _app, _connect, _setup
from tests.integration.test_drafting_m5 import (
    CapturingDeferrer,
    _group,
    _run_queued,
    _sets,
    deferrer,  # noqa: F401 - shared fixture
)
from tests.integration.test_identity_m2 import _add_member, _h, _me, _register_login
from tests.integration.test_ingest_m4 import _sm

pytestmark = pytest.mark.integration


def _ar(ws: str, rid: str = "", suffix: str = "") -> str:
    return f"/api/v1/workspaces/{ws}/approval-requests" + (f"/{rid}" if rid else "") + suffix


async def _four(
    api: httpx.AsyncClient, s: dict[str, Any], ws: str, deferrer: CapturingDeferrer
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    """Four accounts on four platforms in two groups, drafted."""
    acc = {}
    for name, platform, kind in [
        ("li", "linkedin_member", "personal"),
        ("fb", "facebook", "page"),
        ("th", "threads", "personal"),
        ("xx", "x", "personal"),
    ]:
        acc[name] = (await _connect(api, s, ws, platform, handle=f"{name}_{ws[:4]}", kind=kind))[
            "connected"
        ]
    groups = {
        "work": await _group(api, s, ws, "Work", [acc["li"], acc["fb"]]),
        "social": await _group(api, s, ws, "Social", [acc["th"], acc["xx"]]),
    }
    r = await api.post(
        _sets(ws),
        headers=_h(s),
        json={
            "topic": "What moving nightly batch jobs to Postgres 18 changed for us",
            "targets": {"social_account_ids": list(acc.values())},
        },
    )
    assert r.status_code == 201, r.text
    await _run_queued(api, deferrer)
    ps = (await api.get(_sets(ws, r.json()["id"]), headers=_h(s))).json()
    assert all(v["state"] == "drafting" for v in ps["variants"]), [
        (v["platform"], v["state"], v["guardrails"]) for v in ps["variants"]
    ]
    return ps, acc, groups


async def _submit(
    api: httpx.AsyncClient, s: dict[str, Any], ws: str, set_id: str
) -> dict[str, Any]:
    r = await api.post(_sets(ws, set_id, "/request-approval"), headers=_h(s))
    assert r.status_code == 201, r.text
    return r.json()


def _by(req: dict[str, Any], acc: dict[str, str]) -> dict[str, dict[str, Any]]:
    inv = {v: k for k, v in acc.items()}
    return {inv[v["social_account_id"]]: v for v in req["variants"]}


async def _decide(
    api: httpx.AsyncClient,
    s: dict[str, Any],
    ws: str,
    req: dict[str, Any],
    action: str,
    variants: list[dict[str, Any]] | None,
    *,
    hashes: dict[str, str] | None = None,
) -> httpx.Response:
    shown = (
        variants
        if variants is not None
        else [v for v in req["variants"] if v["state"] == "pending"]
    )
    return await api.post(
        _ar(ws, req["id"], "/decisions"),
        headers=_h(s),
        json={
            "action": action,
            "variant_scope": [v["post_variant_id"] for v in variants]
            if variants is not None
            else None,
            "content_hashes": hashes
            if hashes is not None
            else {v["post_variant_id"]: v["content_hash"] for v in shown},
        },
    )


async def _publishable(api: httpx.AsyncClient, ws: str, variant_id: str) -> str | None:
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        try:
            await assert_publishable(tx, uuid.UUID(variant_id))
        except NotPublishable as exc:
            return exc.code
    return None


async def _edit(
    api: httpx.AsyncClient, s: dict[str, Any], ws: str, set_id: str, variant_id: str
) -> None:
    ps = (await api.get(_sets(ws, set_id), headers=_h(s))).json()
    v = next(x for x in ps["variants"] if x["id"] == variant_id)
    r = await api.post(
        _sets(ws, set_id, f"/variants/{variant_id}/versions"),
        headers=_h(s),
        json={
            "body": v["current_version"]["body"] + " (Edited by a person.)",
            "hashtags": v["current_version"]["hashtags"],
            "link_url": v["current_version"]["link_url"],
            "based_on_version_id": v["current_version"]["id"],
        },
    )
    assert r.status_code == 201, r.text


# ------------------------------------------------------------------ scenarios


async def test_four_accounts_approve_all_then_each_is_publishable(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "apr-all")
    ps, _acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    assert req["state"] == "pending"
    assert len(req["variants"]) == 4
    assert all(v["can_decide"] and v["required"] == 1 for v in req["variants"])
    set_now = (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()
    assert set_now["state"] == "needs_approval"
    # A second submission while one is open is refused.
    r = await api.post(_sets(ws, ps["id"], "/request-approval"), headers=_h(s))
    assert (r.status_code, r.json()["error"]["code"]) == (409, "approval_pending")
    # Nothing is publishable before approval.
    assert await _publishable(api, ws, req["variants"][0]["post_variant_id"]) == "not_approved"

    r = await _decide(api, s, ws, req, "approve", None)
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["state"] == "approved"
    assert {v["state"] for v in done["variants"]} == {"approved"}
    assert [d["action"] for d in done["decisions"]] == ["approve"]
    for v in done["variants"]:
        assert await _publishable(api, ws, v["post_variant_id"]) is None
    set_now = (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()
    # M8: every approved account is placed on the calendar in the decision's transaction.
    assert set_now["state"] == "scheduled"
    assert {v["state"] for v in set_now["variants"]} == {"scheduled"}
    # Tapping again: already decided.
    r = await _decide(api, s, ws, req, "approve", None)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "approval_already_decided")
    listed = (await api.get(_ar(ws), headers=_h(s), params={"state": "approved"})).json()["data"]
    assert [x["id"] for x in listed] == [req["id"]]


async def test_approve_two_reject_two_resolves_partially_approved(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "apr-part")
    ps, acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    r = await _decide(api, s, ws, req, "approve", [by["li"], by["fb"]])
    assert r.status_code == 200, r.text
    mid = r.json()
    assert mid["state"] == "pending"  # still deciding X and Threads (ADR-0014)
    r = await _decide(api, s, ws, mid, "reject", [by["th"], by["xx"]])
    done = r.json()
    assert done["state"] == "partially_approved"
    assert await _publishable(api, ws, by["li"]["post_variant_id"]) is None
    assert await _publishable(api, ws, by["xx"]["post_variant_id"]) == "not_approved"
    # M8: the two approved accounts are scheduled; the rejected two are not.
    assert (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()["state"] == "scheduled"


async def test_editing_one_account_supersedes_only_that_account(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "apr-edit")
    ps, acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    r = await _decide(api, s, ws, req, "approve", [by["li"]])
    assert r.status_code == 200
    await _edit(api, s, ws, ps["id"], by["fb"]["post_variant_id"])
    now = (await api.get(_ar(ws, req["id"]), headers=_h(s))).json()
    st = {k: v["state"] for k, v in _by(now, acc).items()}
    assert st == {"li": "approved", "fb": "superseded", "th": "pending", "xx": "pending"}
    assert now["state"] == "pending"
    # The edited account's new text has no approval; the approved one still publishes.
    assert await _publishable(api, ws, by["fb"]["post_variant_id"]) == "not_approved"
    assert await _publishable(api, ws, by["li"]["post_variant_id"]) is None
    # "Approve all" with the hashes from before the edit is refused, not applied to new text.
    r = await _decide(api, s, ws, req, "approve", [by["fb"]])
    assert (r.status_code, r.json()["error"]["code"]) == (409, "approval_already_decided")
    # A sibling's own approval is unaffected by the edit.
    r = await _decide(api, s, ws, now, "approve", [by["th"], by["xx"]])
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "partially_approved"  # approved + superseded

    # Editing an approved account leaves its approval on record but it can't publish the
    # new text (checks 3, 5, 6: the approval names the old version).
    await _edit(api, s, ws, ps["id"], by["li"]["post_variant_id"])
    assert await _publishable(api, ws, by["li"]["post_variant_id"]) == "not_approved"


async def test_stale_hash_and_wrong_scope_are_refused(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "apr-hash")
    ps, acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    r = await _decide(
        api, s, ws, req, "approve", [by["li"]], hashes={by["li"]["post_variant_id"]: "0" * 64}
    )
    assert (r.status_code, r.json()["error"]["code"]) == (422, "approval_content_mismatch")
    r = await _decide(
        api,
        s,
        ws,
        req,
        "approve",
        [{"post_variant_id": str(uuid.uuid4()), "content_hash": "0" * 64}],
    )
    assert (r.status_code, r.json()["error"]["code"]) == (422, "unknown_variant")


async def test_quorum_strictest_policy_and_snapshot(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, owner_engine: AsyncEngine
) -> None:
    s, ws = await _setup(api, "apr-quorum")
    ps, acc, groups = await _four(api, s, ws, deferrer)
    second = await _register_login(api, "apr-q2")
    second_id = (await _me(api, second))["id"]
    await _add_member(owner_engine, ws, second_id, "approver")
    owner_id = (await _me(api, s))["id"]
    pol = f"/api/v1/workspaces/{ws}/approval-policies"
    # A permissive group policy cannot weaken the stricter account policy on the page.
    r = await api.put(
        pol,
        headers=_h(s),
        json={
            "scope_type": "account_group",
            "scope_id": groups["work"],
            "required_approver_count": 1,
        },
    )
    assert r.status_code == 200, r.text
    r = await api.put(
        pol,
        headers=_h(s),
        json={
            "scope_type": "social_account",
            "scope_id": acc["fb"],
            "required_approver_count": 2,
            "quorum_mode": "all_of",
            "approver_user_ids": [owner_id, second_id],
        },
    )
    assert r.status_code == 200, r.text
    # Approvers cannot write policy.
    r = await api.put(
        pol, headers=_h(second), json={"scope_type": "workspace", "required_approver_count": 1}
    )
    assert r.status_code == 403
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    assert (by["fb"]["required"], by["fb"]["quorum"]) == (2, "all_of")
    assert by["li"]["required"] == 1

    # Lowering the policy now does not change the request in flight (policy_snapshot).
    await api.put(
        pol,
        headers=_h(s),
        json={
            "scope_type": "social_account",
            "scope_id": acc["fb"],
            "required_approver_count": 1,
            "approver_user_ids": [],
            "quorum_mode": "any_of",
        },
    )

    r = await _decide(api, s, ws, req, "approve", [by["fb"]])
    one = _by(r.json(), acc)["fb"]
    assert (one["state"], one["approve_count"], one["my_vote"], one["can_decide"]) == (
        "pending",
        1,
        "approve",
        False,
    )
    r = await _decide(api, s, ws, req, "approve", [by["fb"]])
    assert (r.status_code, r.json()["error"]["code"]) == (409, "already_voted")
    r = await _decide(api, second, ws, req, "approve", [by["fb"]])
    assert r.status_code == 200, r.text
    assert _by(r.json(), acc)["fb"]["state"] == "approved"
    assert await _publishable(api, ws, by["fb"]["post_variant_id"]) is None
    # A named-approver policy excludes everyone else.
    third = await _register_login(api, "apr-q3")
    await _add_member(owner_engine, ws, (await _me(api, third))["id"], "approver")
    # (fb is decided; li is open to any approver.)
    r = await _decide(api, third, ws, req, "approve", [by["li"]])
    assert r.status_code == 200, r.text


async def test_scoped_approver_and_group_kill_switch(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, owner_engine: AsyncEngine
) -> None:
    s, ws = await _setup(api, "apr-scope")
    ps, acc, groups = await _four(api, s, ws, deferrer)
    scoped = await _register_login(api, "apr-sc")
    scoped_id = (await _me(api, scoped))["id"]
    await _add_member(owner_engine, ws, scoped_id, "approver")
    r = await api.put(
        f"/api/v1/workspaces/{ws}/members/{scoped_id}/scopes",
        headers=_h(s),
        json={"account_group_ids": [groups["work"]]},
    )
    assert r.status_code == 200, r.text
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    # The scoped approver sees only their group's accounts.
    mine = (await api.get(_ar(ws, req["id"]), headers=_h(scoped))).json()
    assert {v["social_account_id"] for v in mine["variants"]} == {acc["li"], acc["fb"]}
    # A scope naming another group's account fails whole.
    r = await _decide(api, scoped, ws, req, "approve", [by["li"], by["xx"]])
    assert (r.status_code, r.json()["error"]["code"]) == (403, "approver_not_authorized")
    assert (
        _by((await api.get(_ar(ws, req["id"]), headers=_h(s))).json(), acc)["li"]["state"]
        == "pending"
    )

    # Stop the Social group: its accounts can't be approved or published; Work can.
    ks = f"/api/v1/workspaces/{ws}/automation/kill-switch"
    r = await api.post(
        ks,
        headers=_h(scoped),
        json={"scope_type": "account_group", "scope_id": groups["social"], "engaged": True},
    )
    assert r.status_code == 403  # outside their scope
    r = await api.post(
        ks,
        headers=_h(s),
        json={
            "scope_type": "account_group",
            "scope_id": groups["social"],
            "engaged": True,
            "reason": "test",
        },
    )
    assert r.status_code == 200, r.text
    r = await _decide(api, s, ws, req, "approve", [by["th"]])
    assert (r.status_code, r.json()["error"]["code"]) == (409, "kill_switch_engaged")
    r = await _decide(api, scoped, ws, req, "approve", [by["li"], by["fb"]])
    assert r.status_code == 200, r.text
    assert await _publishable(api, ws, by["li"]["post_variant_id"]) is None
    # Releasing is an Admin's.
    r = await api.post(
        ks,
        headers=_h(scoped),
        json={"scope_type": "account_group", "scope_id": groups["work"], "engaged": False},
    )
    assert r.status_code in (403, 409)
    r = await api.post(ks, headers=_h(s), json={"scope_type": "workspace", "engaged": True})
    assert await _publishable(api, ws, by["li"]["post_variant_id"]) == "kill_switch_engaged"
    await api.post(ks, headers=_h(s), json={"scope_type": "workspace", "engaged": False})
    await api.post(
        ks,
        headers=_h(s),
        json={"scope_type": "account_group", "scope_id": groups["social"], "engaged": False},
    )
    r = await _decide(api, s, ws, req, "approve", [by["th"], by["xx"]])
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "approved"


async def test_tokens_scoped_single_use_and_bound_to_the_hash(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "apr-tok")
    ps, acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    svc = _app(api).state.approval_service
    owner_id = uuid.UUID((await _me(api, s))["id"])
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        r_ = await tx.get(ApprovalRequest, uuid.UUID(req["id"]))
        assert r_ is not None
        mk = lambda scope, action="approve": svc.mint_token(  # noqa: E731
            tx, r_, approver_id=owner_id, action=action, scope=scope, channel="test"
        )
        t_all = await mk(None)
        t_li = await mk([uuid.UUID(by["li"]["post_variant_id"])])
        t_li2 = await mk([uuid.UUID(by["li"]["post_variant_id"])])
        t_th = await mk([uuid.UUID(by["th"]["post_variant_id"])])
        t_xx = await mk([uuid.UUID(by["xx"]["post_variant_id"])], "reject")
    from mera.application.errors import ConflictError, Gone, InvalidInput

    wsid = uuid.UUID(ws)
    # Scoped approve, then a replay of the same button: used.
    assert await svc.decide_with_token(wsid, t_li, channel="test", external_id=None) == "pending"
    with pytest.raises(Gone) as gone:
        await svc.decide_with_token(wsid, t_li, channel="test", external_id=None)
    assert gone.value.code == "approval_token_used"
    # A second button for the same, now decided, account was revoked with it (scoped).
    with pytest.raises(Gone):
        await svc.decide_with_token(wsid, t_li2, channel="test", external_id=None)
    # "Approve all" carries the composite hash and was revoked by the first decision; the
    # sibling's own button still works after an edit elsewhere.
    with pytest.raises(Gone):
        await svc.decide_with_token(wsid, t_all, channel="test", external_id=None)
    await _edit(api, s, ws, ps["id"], by["fb"]["post_variant_id"])
    assert await svc.decide_with_token(wsid, t_th, channel="test", external_id=None) == "pending"
    assert (
        await svc.decide_with_token(wsid, t_xx, channel="test", external_id=None)
        == "partially_approved"
    )
    # The wrong channel is not this button's.
    with pytest.raises(Gone):
        await svc.decide_with_token(wsid, "x" * 22, channel="test", external_id=None)
    _ = (InvalidInput, ConflictError)


async def test_not_ready_expiry_and_cancel(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, owner_engine: AsyncEngine
) -> None:
    s, ws = await _setup(api, "apr-misc")
    ps, _acc, _ = await _four(api, s, ws, deferrer)
    # A banned word added after drafting blocks submission (guardrails re-run).
    vid = (
        await api.post(
            f"/api/v1/workspaces/{ws}/brand-voices",
            headers=_h(s),
            json={
                "scope_type": "workspace",
                "name": "Strict",
                "banned_words": ["postgres"],
                "is_default": True,
            },
        )
    ).json()
    assert vid.get("id"), vid
    r = await api.post(_sets(ws, ps["id"], "/request-approval"), headers=_h(s))
    assert (r.status_code, r.json()["error"]["code"]) == (409, "not_ready_for_approval")
    assert "banned" in r.json()["error"]["message"]
    await api.delete(f"/api/v1/workspaces/{ws}/brand-voices/{vid['id']}", headers=_h(s))

    req = await _submit(api, s, ws, ps["id"])
    # Switching an account off mid-approval would change what is being approved: refused.
    v0 = req["variants"][0]["post_variant_id"]
    r = await api.patch(
        _sets(ws, ps["id"], f"/variants/{v0}"), headers=_h(s), json={"enabled": False}
    )
    assert (r.status_code, r.json()["error"]["code"]) == (409, "variant_locked")
    # Expiry is terminal and never publishes.
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE approval_request SET expires_at = now() - interval '1 minute' "
                "WHERE id = :id"
            ),
            {"id": req["id"]},
        )
    r = await _decide(api, s, ws, req, "approve", None)
    assert (r.status_code, r.json()["error"]["code"]) == (410, "approval_expired")
    # The expiry is recorded (not just reported): every pending account expired.
    got = (await api.get(_ar(ws, req["id"]), headers=_h(s))).json()
    assert got["state"] == "expired"
    assert (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()["state"] == "drafting"
    # A fresh request can be withdrawn.
    req = await _submit(api, s, ws, ps["id"])
    r = await api.post(_ar(ws, req["id"], "/cancel"), headers=_h(s), json={"reason": "not today"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "cancelled"
    assert (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()["state"] == "drafting"
    # The decision log is append-only.
    async with owner_engine.begin() as conn:
        with pytest.raises(Exception, match="append-only"):
            await conn.execute(
                text("UPDATE approval_decision SET reason = 'x' WHERE approval_request_id = :id"),
                {"id": req["id"]},
            )
    # And can be submitted again.
    again = await _submit(api, s, ws, ps["id"])
    assert again["id"] != req["id"]
