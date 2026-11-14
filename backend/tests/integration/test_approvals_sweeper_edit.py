"""M6 part 2: the sweeper (6.8), the edit flow (6.10), the remaining endpoints (6.11) and
re-asking only the remaining accounts (Sprint 8 carry-over)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update

from mera.application.approvals.sweeper import shift_for_quiet, sweep
from mera.domain.context import WorkspaceContext
from mera.domain.policies.edit_reply import classify
from mera.infrastructure.db.models import ApprovalRequest, Event
from mera.infrastructure.db.session import workspace_transaction
from tests.integration.test_accounts_m3 import _app, _setup
from tests.integration.test_approvals_m6 import _ar, _by, _decide, _submit
from tests.integration.test_drafting_m5 import CapturingDeferrer, _sets, deferrer  # noqa: F401
from tests.integration.test_identity_m2 import _h
from tests.integration.test_ingest_m4 import _sm
from tests.integration.test_publishing_m8 import _four, quiet  # noqa: F401

pytestmark = pytest.mark.integration


async def _req_row(api: httpx.AsyncClient, ws: str, rid: str) -> ApprovalRequest:
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as s:
        row = await s.get(ApprovalRequest, uuid.UUID(rid))
        assert row is not None
        return row


async def _set_times(api: httpx.AsyncClient, ws: str, rid: str, **values: Any) -> None:
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as s:
        await s.execute(
            update(ApprovalRequest).where(ApprovalRequest.id == uuid.UUID(rid)).values(**values)
        )


async def test_sweeper_reminds_escalates_then_expires_per_account(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "sweeper")
    ps, acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    assert (await _decide(api, s, ws, req, "approve", [by["li"], by["fb"]])).status_code == 200
    now = datetime.now(UTC)
    await _set_times(api, ws, req["id"], remind_at=now - timedelta(minutes=1))
    sink = CapturingDeferrer()
    counts = await sweep(_sm(api), sink, now)
    assert counts.get("reminder") == 1
    row = await _req_row(api, ws, req["id"])
    assert row.reminded_at is not None
    assert any(c["kind"] == "reminder" and c["subject_id"] == req["id"] for c in sink.calls)
    # Swept once: a second sweep does not remind again.
    assert (await sweep(_sm(api), CapturingDeferrer(), now)).get("reminder") is None
    await _set_times(api, ws, req["id"], escalate_at=now - timedelta(minutes=1))
    assert (await sweep(_sm(api), sink, now)).get("escalation") == 1
    assert (await _req_row(api, ws, req["id"])).escalation_level == 1
    await _set_times(api, ws, req["id"], expires_at=now - timedelta(seconds=1))
    assert (await sweep(_sm(api), sink, now)).get("expired") == 1
    done = (await api.get(_ar(ws, req["id"]), headers=_h(s))).json()
    assert done["state"] == "partially_approved"
    states = {v["social_account_id"]: v["state"] for v in done["variants"]}
    assert states[acc["li"]] == "approved"
    assert states[acc["th"]] == "expired"
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        kinds = set((await tx.execute(select(Event.event_type))).scalars())
    assert {
        "approval.reminded",
        "approval.escalated",
        "approval.expired",
        "post.scheduled",
    } <= kinds


async def test_quiet_hours_shift_timers_instead_of_consuming_them(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "quietshift")
    r = await api.patch(f"/api/v1/workspaces/{ws}", headers=_h(s), json={"timezone": "UTC"})
    assert r.status_code in (200, 422), r.text
    ps, _acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    row = await _req_row(api, ws, req["id"])
    due = datetime(2026, 11, 10, 2, 0, tzinfo=UTC)
    row.remind_at = due
    row.reminded_at = None
    exp_before = row.expires_at
    moved = shift_for_quiet(row, due, {"start": "22:00", "end": "07:00"}, "UTC")
    assert moved
    assert row.remind_at == due + timedelta(hours=5)
    assert row.expires_at == exp_before + timedelta(hours=5)
    assert not shift_for_quiet(
        row, datetime(2026, 11, 10, 12, 0, tzinfo=UTC), {"start": "22:00", "end": "07:00"}, "UTC"
    )


async def test_edit_one_account_supersedes_only_it_and_reasks(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "editflow")
    ps, acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    assert (await _decide(api, s, ws, req, "approve", [by["li"]])).status_code == 200
    r = await api.post(
        _ar(ws, req["id"], "/edit"),
        headers=_h(s),
        json={
            "instruction": "shorter, and lead with the question",
            "variant_ids": [by["xx"]["post_variant_id"]],
        },
    )
    assert r.status_code == 200, r.text
    job = next(c for c in quiet.calls if c["task"] == "drafting:draft")
    assert job["trigger"] == "approval_edit"
    assert job["variant_ids"] == [by["xx"]["post_variant_id"]]
    # A second edit while the first runs is refused.
    r = await api.post(_ar(ws, req["id"], "/edit"), headers=_h(s), json={"instruction": "again"})
    assert r.status_code == 409
    # Play the worker: revise, then re-ask.
    app = _app(api)
    await app.state.drafting.draft(
        uuid.UUID(ws),
        uuid.UUID(ps["id"]),
        variant_ids=[uuid.UUID(by["xx"]["post_variant_id"])],
        mode="revise",
        instruction=job["instruction"],
        user_id=uuid.UUID(job["user_id"]),
    )
    after = (await api.get(_ar(ws, req["id"]), headers=_h(s))).json()
    st = {v["social_account_id"]: v["state"] for v in after["variants"]}
    assert st[acc["xx"]] == "superseded"
    assert st[acc["li"]] == "approved"
    assert st[acc["fb"]] == "pending"
    assert st[acc["th"]] == "pending"
    assert after["state"] == "pending"  # siblings are still decidable
    follow = await app.state.approval_service.resubmit_after_edit(
        uuid.UUID(ws),
        uuid.UUID(ps["id"]),
        [uuid.UUID(by["xx"]["post_variant_id"])],
        uuid.UUID(job["user_id"]),
    )
    assert follow is not None
    assert [v.social_account_id for v in follow.variants] == [uuid.UUID(acc["xx"])]
    assert follow.variants[0].version_no == 2
    # Two open requests now, on disjoint accounts; both decide independently.
    open_ = (await api.get(_ar(ws), headers=_h(s), params={"state": "open"})).json()["data"]
    assert len(open_) == 2
    r = await _decide(
        api, s, ws, open_[0] if open_[0]["id"] == str(follow.id) else open_[1], "approve", None
    )
    assert r.status_code == 200
    assert r.json()["state"] == "approved"
    # The original request still accepts its own pending accounts.
    assert (await _decide(api, s, ws, after, "approve", [by["fb"], by["th"]])).status_code == 200


async def test_submit_only_remaining_accounts_after_partial_outcome(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "remaining")
    ps, acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    # While it is pending, a second request for the same accounts is refused.
    r = await api.post(_sets(ws, ps["id"], "/request-approval"), headers=_h(s))
    assert (r.status_code, r.json()["error"]["code"]) == (409, "approval_pending")
    r = await api.post(_ar(ws, req["id"], "/cancel"), headers=_h(s), json={})
    assert r.status_code == 200
    assert r.json()["state"] == "cancelled"
    # Cancelled accounts go back to drafting and can be sent again, a subset by id.
    r = await api.post(
        _sets(ws, ps["id"], "/request-approval"),
        headers=_h(s),
        json={"variant_ids": [by["li"]["post_variant_id"], by["th"]["post_variant_id"]]},
    )
    assert r.status_code == 201, r.text
    assert {v["social_account_id"] for v in r.json()["variants"]} == {acc["li"], acc["th"]}
    # Default selection: everything else still in drafting.
    r = await api.post(_sets(ws, ps["id"], "/request-approval"), headers=_h(s))
    assert r.status_code == 201, r.text
    assert {v["social_account_id"] for v in r.json()["variants"]} == {acc["fb"], acc["xx"]}


async def test_preview_and_variants_endpoints(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "preview")
    ps, _acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    pv = (await api.get(_ar(ws, req["id"], "/preview"), headers=_h(s))).json()
    assert pv["workspace"]["name"]
    assert pv["group"]["name"] == "Tech News"
    assert len(pv["request"]["variants"]) == 4
    assert pv["stagger_span_seconds"] >= 0
    assert pv["source"]["title"]
    assert "distinct" in pv
    assert "facts_verified_all" in pv
    vs = (await api.get(_ar(ws, req["id"], "/variants"), headers=_h(s))).json()["data"]
    assert {v["state"] for v in vs} == {"pending"}


def test_edit_reply_classification() -> None:
    body = "Postgres 18 ships async I/O. " * 10
    assert classify("shorter", body) == "instruction"
    assert classify("lead with the read-replica question", body) == "instruction"
    assert (
        classify("Make it friendlier and drop the last paragraph about replicas please", body)
        == "instruction"
    )
    long_text = (
        "Should we size read replicas differently now?\n\n"
        "io_uring lets reads go async. https://pg.example #postgres"
    )
    assert classify(long_text, body) == "replacement"
    assert classify(" ", body) == "ambiguous"
