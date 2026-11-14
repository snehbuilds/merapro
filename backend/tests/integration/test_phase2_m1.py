"""P2-M1 (docs/13): the Phase 1 gaps in publishing and the Composer.

Approval validity runs from the slot the approval placed (ADR-0016); banned words and
hashtags are rechecked at publish; the hashtag registry, post templates and posting slots;
the multi-Page connect choice; stopping and retrying agent runs; alt text suggestions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from mera.application.ingest import agent_runs
from mera.domain.context import WorkspaceContext
from mera.infrastructure.db.models import AgentRun, ScheduledPost
from mera.infrastructure.db.session import workspace_transaction
from tests.integration.test_accounts_m3 import _connect, _setup
from tests.integration.test_approvals_m6 import _by, _decide
from tests.integration.test_drafting_m5 import (
    CapturingDeferrer,
    _group,
    _run_queued,
    _sets,
    deferrer,  # noqa: F401 - shared fixture
)
from tests.integration.test_identity_m2 import _h
from tests.integration.test_ingest_m4 import _sm
from tests.integration.test_media_m5 import _photo, _upload
from tests.integration.test_publishing_m8 import (
    _approve_all,
    _four,
    _publisher,
    _slots,
    quiet,  # noqa: F401 - shared fixture
)

pytestmark = pytest.mark.integration


def _ws(ws: str, path: str) -> str:
    return f"/api/v1/workspaces/{ws}{path}"


# ---------------------------------------------------------------- hashtag registry


async def test_hashtag_registry_normalizes_and_upserts(api: httpx.AsyncClient) -> None:
    s, ws = await _setup(api, "tags")
    r = await api.put(
        _ws(ws, "/hashtags"),
        headers=_h(s),
        json={"tag": "#MachineLearning", "status": "preferred", "aliases": ["#ML", "ml"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["tag"] == "machinelearning"
    assert r.json()["aliases"] == ["ml"]
    r = await api.put(
        _ws(ws, "/hashtags"), headers=_h(s), json={"tag": "machinelearning", "status": "banned"}
    )
    assert r.json()["status"] == "banned"
    assert r.json()["aliases"] == ["ml"]  # aliases kept when not given
    bad = await api.put(
        _ws(ws, "/hashtags"), headers=_h(s), json={"tag": "two words", "status": "banned"}
    )
    assert bad.status_code == 422
    rows = (await api.get(_ws(ws, "/hashtags?status=banned"), headers=_h(s))).json()["data"]
    assert [x["tag"] for x in rows] == ["machinelearning"]
    assert (
        await api.delete(_ws(ws, f"/hashtags/{rows[0]['id']}"), headers=_h(s))
    ).status_code == 204
    assert (await api.get(_ws(ws, "/hashtags"), headers=_h(s))).json()["data"] == []


async def test_banned_tag_blocks_submission_and_a_queued_post(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "bantag")
    ps, acc, _ = await _four(api, s, ws, deferrer)
    li = next(v for v in ps["variants"] if v["social_account_id"] == acc["li"])
    tag = f"legacy{uuid.uuid4().hex[:6]}"
    cur = li["current_version"]
    r = await api.post(
        _sets(ws, ps["id"], f"/variants/{li['id']}/versions"),
        headers=_h(s),
        json={
            "body": cur["body"],
            "hashtags": [*cur["hashtags"], f"#{tag}"],
            "link_url": cur["link_url"],
            "based_on_version_id": cur["id"],
        },
    )
    assert r.status_code == 201, r.text

    # Banned before submission: the set cannot go for approval.
    await api.put(_ws(ws, "/hashtags"), headers=_h(s), json={"tag": tag, "status": "banned"})
    r = await api.post(_sets(ws, ps["id"], "/request-approval"), headers=_h(s))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "not_ready_for_approval"
    assert "banned hashtag" in r.json()["error"]["message"]

    # Allowed again, approved, then banned while queued: that account stops, the rest go.
    await api.put(_ws(ws, "/hashtags"), headers=_h(s), json={"tag": tag, "status": "allowed"})
    await _approve_all(api, s, ws, ps["id"])
    await api.put(_ws(ws, "/hashtags"), headers=_h(s), json={"tag": tag, "status": "banned"})
    pub, _ = _publisher(api)
    outcomes = {}
    for sp in await _slots(api, ws, ps["id"]):
        outcomes[str(sp.social_account_id)] = await pub.publish(
            uuid.UUID(ws), sp.id, now=sp.scheduled_for + timedelta(seconds=1)
        )
    assert outcomes[acc["li"]] == "dead_lettered"
    dl = (await api.get(_ws(ws, "/dead-letters"), headers=_h(s))).json()["data"]
    assert any("banned_content" in d["error"] for d in dl)
    others = [o for k, o in outcomes.items() if k != acc["li"]]
    # Siblings that used the same tag stop too; the others publish.
    assert set(others) <= {"published", "dead_lettered"}
    assert "published" in others


# ---------------------------------------------------------------- ADR-0016 validity


async def test_approval_for_a_later_slot_is_valid_at_that_slot(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "validity")
    ps, acc, _ = await _four(api, s, ws, deferrer)
    later = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    r = await api.post(
        _sets(ws, ps["id"], "/request-approval"),
        headers=_h(s),
        json={"scheduled_for": later.isoformat()},
    )
    assert r.status_code == 201, r.text
    req = r.json()
    by = _by(req, acc)
    assert (await _decide(api, s, ws, req, "approve", [by["li"], by["xx"]])).status_code == 200
    slots = {str(sp.social_account_id): sp for sp in await _slots(api, ws, ps["id"])}
    li, xx = slots[acc["li"]], slots[acc["xx"]]
    assert li.placement["approved_slot"] == li.scheduled_for.isoformat()
    assert li.scheduled_for >= later
    pub, _ = _publisher(api)
    # Three days after a 24-hour approval: valid, because validity runs from the slot.
    assert await pub.publish(uuid.UUID(ws), li.id, now=li.scheduled_for + timedelta(minutes=1)) == (
        "published"
    )
    # Held a day past its slot (a kill switch, an outage): stale, as before.
    held = xx.scheduled_for + timedelta(hours=25)
    assert await pub.publish(uuid.UUID(ws), xx.id, now=held) == "dead_lettered"
    dl = (await api.get(_ws(ws, "/dead-letters"), headers=_h(s))).json()["data"]
    assert any("approval_stale" in d["error"] for d in dl)


# ---------------------------------------------------------------- templates


async def test_post_set_from_a_template(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "tmpl")
    a = (await _connect(api, s, ws, "linkedin_member", handle="t_li"))["connected"]
    b = (await _connect(api, s, ws, "x", handle="t_x"))["connected"]
    grp = await _group(api, s, ws, "Weekly", [a, b])
    r = await api.post(
        _ws(ws, "/post-templates"),
        headers=_h(s),
        json={
            "name": "Release notes",
            "topic": "What shipped this week in the data platform",
            "outline": "One headline change, why it matters, one link.",
            "hashtags": ["#DataEngineering"],
            "target_group_ids": [grp],
        },
    )
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert r.json()["hashtags"] == ["dataengineering"]
    dup = await api.post(
        _ws(ws, "/post-templates"), headers=_h(s), json={"name": "Release notes", "topic": "x"}
    )
    assert dup.json()["error"]["code"] == "template_name_taken"

    r = await api.post(_sets(ws), headers=_h(s), json={"template_id": tid})
    assert r.status_code == 201, r.text
    ps = r.json()
    assert ps["title"] == "Release notes"
    assert "Structure to follow" in ps["topic_brief"]
    assert "#dataengineering" in ps["topic_brief"]
    assert {v["social_account_id"] for v in ps["variants"]} == {a, b}
    await _run_queued(api, deferrer)
    drafted = (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()
    bodies = [v["current_version"]["body"] for v in drafted["variants"]]
    assert len(set(bodies)) == 2  # still one post per account
    listed = (await api.get(_ws(ws, "/post-templates"), headers=_h(s))).json()["data"]
    assert listed[0]["use_count"] == 1
    r = await api.patch(_ws(ws, f"/post-templates/{tid}"), headers=_h(s), json={"outline": ""})
    assert r.json()["outline"] is None
    assert (await api.delete(_ws(ws, f"/post-templates/{tid}"), headers=_h(s))).status_code == 204
    r = await api.post(_sets(ws), headers=_h(s), json={"template_id": tid})
    assert r.json()["error"]["code"] == "unknown_template"


# ---------------------------------------------------------------- posting slots


async def test_approval_without_a_time_uses_the_next_posting_time(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "slots")
    ps, acc, grp = await _four(api, s, ws, deferrer)
    # Every day at 09:00 workspace time.
    schedule = [{"day": d, "time": "09:00"} for d in range(7)]
    r = await api.patch(
        _ws(ws, f"/account-groups/{grp}"), headers=_h(s), json={"posting_schedule": schedule}
    )
    assert r.status_code == 200, r.text
    slots_view = (
        await api.get(_ws(ws, f"/posting-slots?group_id={grp}&days=3"), headers=_h(s))
    ).json()["data"]
    assert {x["social_account_id"] for x in slots_view} == set(acc.values())
    assert all(x["source"] == "group" for x in slots_view)
    first = min(datetime.fromisoformat(x["at"]) for x in slots_view)
    # 09:00 in the workspace's timezone (Asia/Kolkata for test workspaces).
    assert first.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%H:%M") == "09:00"
    await _approve_all(api, s, ws, ps["id"])
    placed = await _slots(api, ws, ps["id"])
    earliest = min(sp.scheduled_for for sp in placed)
    assert earliest >= first
    assert earliest < first + timedelta(hours=1)  # the posting time plus stagger
    after = (await api.get(_ws(ws, f"/posting-slots?group_id={grp}&days=3"), headers=_h(s))).json()[
        "data"
    ]
    assert any(x["taken"] for x in after)
    bad = await api.get(_ws(ws, "/posting-slots?days=30"), headers=_h(s))
    assert bad.status_code == 400  # query validation uses the 400 envelope


# ---------------------------------------------------------------- several Pages


async def test_a_login_with_several_pages_asks_which_one(api: httpx.AsyncClient) -> None:
    s, ws = await _setup(api, "pages")
    one, two = f"p1-{uuid.uuid4().hex[:6]}", f"p2-{uuid.uuid4().hex[:6]}"
    params = await _connect(
        api,
        s,
        ws,
        "facebook",
        account_id=f"{one},{two}",
        handle="acme_news,acme_jobs",
        kind="page",
        redeem=False,
    )
    code = params["connect_code"]
    r = await api.get(
        "/api/v1/social-accounts/connect/choices", params={"connect_code": code}, headers=_h(s)
    )
    assert r.status_code == 200
    choices = r.json()["data"]
    assert [c["handle"] for c in choices] == ["acme_news", "acme_jobs"]
    assert all("credentials" not in c and "access_token" not in str(c) for c in choices)
    r = await api.post(
        "/api/v1/social-accounts/connect/complete", headers=_h(s), json={"connect_code": code}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "choice_required"
    r = await api.post(
        "/api/v1/social-accounts/connect/complete",
        headers=_h(s),
        json={"connect_code": code, "platform_account_id": "not-offered"},
    )
    assert r.json()["error"]["code"] == "invalid_choice"
    r = await api.post(
        "/api/v1/social-accounts/connect/complete",
        headers=_h(s),
        json={"connect_code": code, "platform_account_id": two},
    )
    assert r.status_code == 200, r.text
    acct = (
        await api.get(_ws(ws, f"/social-accounts/{r.json()['account_id']}"), headers=_h(s))
    ).json()
    assert acct["handle"] == "acme_jobs"
    # Used once.
    again = await api.post(
        "/api/v1/social-accounts/connect/complete",
        headers=_h(s),
        json={"connect_code": code, "platform_account_id": one},
    )
    assert again.status_code == 404


# ---------------------------------------------------------------- agent runs


async def test_stop_and_retry_agent_runs(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "runs")
    a = (await _connect(api, s, ws, "linkedin_member", handle="r_li"))["connected"]
    r = await api.post(
        _sets(ws),
        headers=_h(s),
        json={"topic": "A run to stop", "targets": {"social_account_ids": [a]}},
    )
    set_id = r.json()["id"]
    run = await agent_runs.start_run(
        _sm(api),
        uuid.UUID(ws),
        workflow_key="draft_from_content",
        trigger="manual",
        subject_type="post_set",
        subject_id=uuid.UUID(set_id),
    )
    r = await api.post(_ws(ws, f"/agent-runs/{run.run_id}/abort"), headers=_h(s))
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "aborted"
    assert await agent_runs.is_aborted(_sm(api), run)
    again = await api.post(_ws(ws, f"/agent-runs/{run.run_id}/abort"), headers=_h(s))
    assert again.json()["error"]["code"] == "run_not_active"
    # Drain the creation job so the set is idle, then retry the stopped run.
    await _run_queued(api, deferrer)
    r = await api.post(_ws(ws, f"/agent-runs/{run.run_id}/retry"), headers=_h(s))
    assert r.status_code == 202, r.text
    assert r.json()["generation_status"] == "queued"
    # A succeeded run is not retried.
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        ok = (
            await tx.execute(select(AgentRun).where(AgentRun.state == "succeeded").limit(1))
        ).scalar_one()
    r = await api.post(_ws(ws, f"/agent-runs/{ok.id}/retry"), headers=_h(s))
    assert r.json()["error"]["code"] == "run_not_retryable"


async def test_drafting_honours_a_stop(api: httpx.AsyncClient, deferrer: CapturingDeferrer) -> None:
    """A stop before the loop reaches an account leaves that account undrafted."""
    from mera.application.drafting import pipeline

    s, ws = await _setup(api, "stopdraft")
    a = (await _connect(api, s, ws, "linkedin_member", handle="sd_li"))["connected"]
    b = (await _connect(api, s, ws, "x", handle="sd_x"))["connected"]
    r = await api.post(
        _sets(ws),
        headers=_h(s),
        json={"topic": "Stop me", "targets": {"social_account_ids": [a, b]}, "generate": False},
    )
    set_id = r.json()["id"]
    original = agent_runs.is_aborted

    async def stop_now(sm: Any, run: Any) -> bool:
        return True

    pipeline.agent_runs.is_aborted = stop_now  # type: ignore[assignment]
    try:
        drafting = api._transport.app.state.drafting  # type: ignore[attr-defined]
        await drafting.draft(uuid.UUID(ws), uuid.UUID(set_id))
    finally:
        pipeline.agent_runs.is_aborted = original  # type: ignore[assignment]
    ps = (await api.get(_sets(ws, set_id), headers=_h(s))).json()
    assert all(v["current_version"] is None for v in ps["variants"])
    assert "Stopped" in (ps["generation_error"] or "")
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        last = await tx.get(AgentRun, uuid.UUID(ps["last_run_id"]))
        assert last is not None
        assert last.state == "aborted"


# ---------------------------------------------------------------- alt text


async def test_alt_text_is_suggested_not_saved(api: httpx.AsyncClient) -> None:
    s, ws = await _setup(api, "alt")
    up = await _upload(api, s, ws, _photo(1600, 900))
    assert up.status_code == 201, up.text
    asset = up.json()["id"]
    r = await api.post(
        _ws(ws, f"/media/{asset}/alt-text/suggest"),
        headers=_h(s),
        json={"platform": "instagram", "post_excerpt": "Our new office"},
    )
    assert r.status_code == 200, r.text
    assert "Sandbox description" in r.json()["alt_text"]
    media = (await api.get(_ws(ws, f"/media/{asset}"), headers=_h(s))).json()
    assert media["default_alt_text"] is None  # nothing saved
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        run = (
            await tx.execute(select(AgentRun).where(AgentRun.workflow_key == "alt_text"))
        ).scalar_one()
        assert run.state == "succeeded"
    _ = ScheduledPost  # keeps the model import used by sibling tests' fixtures
