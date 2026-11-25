"""M8 publishing against real Postgres and the sandbox platform (docs/11 M8 acceptance, the
publishing rows of the docs/07 §11 matrix, and Phase 1 DoD 6, 8 and 9)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from mera.application.publishing.publisher import Publisher
from mera.domain.context import WorkspaceContext
from mera.domain.ports.object_store import ObjectNotFound
from mera.infrastructure.db.models import (
    PostVariant,
    PublishedComment,
    ScheduledPost,
    SocialAccount,
)
from mera.infrastructure.db.session import workspace_transaction
from mera.infrastructure.platforms.adapters.fake import FakePlatformAdapter
from tests.integration.test_accounts_m3 import _app, _connect, _setup
from tests.integration.test_approvals_m6 import _by, _decide, _edit, _publishable, _submit
from tests.integration.test_drafting_m5 import (
    CapturingDeferrer,
    _group,
    _run_queued,
    _sets,
    deferrer,  # noqa: F401 - shared fixture
)
from tests.integration.test_identity_m2 import _h
from tests.integration.test_ingest_m4 import _sm

pytestmark = pytest.mark.integration


@pytest.fixture
async def quiet(api: httpx.AsyncClient) -> AsyncIterator[CapturingDeferrer]:
    """Capture the approval and publishing services' job deferrals instead of queueing."""
    app = _app(api)
    fake = CapturingDeferrer()
    saved = (app.state.approval_service._deferrer, app.state.publishing_service._deferrer)
    app.state.approval_service._deferrer = fake
    app.state.publishing_service._deferrer = fake
    yield fake
    app.state.approval_service._deferrer, app.state.publishing_service._deferrer = saved


def _publisher(api: httpx.AsyncClient) -> tuple[Publisher, dict[str, FakePlatformAdapter]]:
    app = _app(api)
    adapters = {
        p: FakePlatformAdapter(p)
        for p in ("linkedin_member", "facebook", "threads", "x", "instagram")
    }
    pub = Publisher(
        _sm(api),
        adapters,
        app.state.key_provider,
        app.state.object_store,
        deferrer=CapturingDeferrer(),
        sandbox=True,
    )
    return pub, adapters


async def _four(
    api: httpx.AsyncClient,
    s: dict[str, Any],
    ws: str,
    deferrer: CapturingDeferrer,
    *,
    handles: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, str], str]:
    acc = {}
    for name, platform, kind in [
        ("li", "linkedin_member", "personal"),
        ("fb", "facebook", "page"),
        ("th", "threads", "personal"),
        ("xx", "x", "personal"),
    ]:
        handle = (handles or {}).get(name, f"{name}_{uuid.uuid4().hex[:6]}")
        acc[name] = (await _connect(api, s, ws, platform, handle=handle, kind=kind))["connected"]
    grp = await _group(api, s, ws, "Tech News", list(acc.values()))
    r = await api.post(
        _sets(ws),
        headers=_h(s),
        json={
            "topic": "What moving nightly batch jobs to Postgres 18 changed for us",
            "targets": {"account_group_ids": [grp]},
        },
    )
    assert r.status_code == 201, r.text
    await _run_queued(api, deferrer)
    ps = (await api.get(_sets(ws, r.json()["id"]), headers=_h(s))).json()
    assert all(v["state"] == "drafting" for v in ps["variants"]), [
        (v["platform"], v["guardrails"]) for v in ps["variants"]
    ]
    return ps, acc, grp


async def _slots(api: httpx.AsyncClient, ws: str, set_id: str) -> list[ScheduledPost]:
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as s:
        return list(
            (
                await s.execute(
                    select(ScheduledPost)
                    .where(ScheduledPost.post_set_id == uuid.UUID(set_id))
                    .order_by(ScheduledPost.scheduled_for)
                )
            ).scalars()
        )


async def _run_all(api: httpx.AsyncClient, pub: Publisher, ws: str, set_id: str) -> dict[str, str]:
    out = {}
    for sp in await _slots(api, ws, set_id):
        out[str(sp.social_account_id)] = await pub.publish(
            uuid.UUID(ws), sp.id, now=sp.scheduled_for + timedelta(seconds=1)
        )
    return out


async def _approve_all(
    api: httpx.AsyncClient, s: dict[str, Any], ws: str, set_id: str
) -> dict[str, Any]:
    req = await _submit(api, s, ws, set_id)
    r = await _decide(api, s, ws, req, "approve", None)
    assert r.status_code == 200, r.text
    return r.json()


async def test_approve_two_reject_two_publishes_exactly_two(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "exactlytwo")
    ps, acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    assert (await _decide(api, s, ws, req, "approve", [by["li"], by["fb"]])).status_code == 200
    r = await _decide(api, s, ws, req, "reject", [by["th"], by["xx"]])
    assert r.status_code == 200
    assert r.json()["state"] == "partially_approved"
    slots = await _slots(api, ws, ps["id"])
    assert {str(sp.social_account_id) for sp in slots} == {acc["li"], acc["fb"]}
    # Stored once, staggered: the two accounts are minutes apart, as the approver saw.
    assert slots[0].scheduled_for != slots[1].scheduled_for
    pub, adapters = _publisher(api)
    outcomes = await _run_all(api, pub, ws, ps["id"])
    assert sorted(outcomes.values()) == ["published", "published"]
    assert len(adapters["x"].posts) == 0
    assert len(adapters["threads"].posts) == 0
    published = (await api.get(f"/api/v1/workspaces/{ws}/published-posts", headers=_h(s))).json()[
        "data"
    ]
    assert {p["social_account_id"] for p in published} == {acc["li"], acc["fb"]}
    status = (await api.get(_sets(ws, ps["id"], "/status"), headers=_h(s))).json()
    assert status["state"] == "published"
    states = {a["social_account_id"]: a["variant_state"] for a in status["accounts"]}
    assert states[acc["th"]] == "rejected"
    assert states[acc["li"]] == "published"
    # The dispatch for the bot cards went out with every decision (web → bot sync).
    assert any(c["task"] == "approvals:dispatch" and c["kind"] == "card" for c in quiet.calls)
    assert (
        sum(1 for c in quiet.calls if c["task"] == "approvals:dispatch" and c["kind"] == "refresh")
        >= 1
    )


async def test_one_account_failing_leaves_the_others_published(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "partialfail")
    ps, acc, _ = await _four(
        api, s, ws, deferrer, handles={"fb": f"fb_fail_{uuid.uuid4().hex[:4]}"}
    )
    await _approve_all(api, s, ws, ps["id"])
    pub, _ = _publisher(api)
    outcomes = await _run_all(api, pub, ws, ps["id"])
    assert outcomes[acc["fb"]] == "dead_lettered"
    assert [o for k, o in outcomes.items() if k != acc["fb"]] == ["published"] * 3
    status = (await api.get(_sets(ws, ps["id"], "/status"), headers=_h(s))).json()
    assert status["state"] == "partially_published"
    fb = next(a for a in status["accounts"] if a["social_account_id"] == acc["fb"])
    assert fb["publish_state"] == "dead_lettered"
    assert "refused" in fb["last_error"]
    dl = (await api.get(f"/api/v1/workspaces/{ws}/dead-letters", headers=_h(s))).json()["data"]
    assert len(dl) == 1
    assert dl[0]["social_account_id"] == acc["fb"]
    # The publish report for the bots was queued once the set finished.
    assert any(c.get("kind") == "publish_report" for c in pub._deferrer.calls)  # type: ignore[union-attr]
    # Retry puts it back in the queue (and resolves the dead letter); the checks run again.
    r = await api.post(
        f"/api/v1/workspaces/{ws}/scheduled-posts/{fb['scheduled_post_id']}/retry", headers=_h(s)
    )
    assert r.status_code == 200
    assert r.json()["state"] == "queued"
    assert (await api.get(f"/api/v1/workspaces/{ws}/dead-letters", headers=_h(s))).json()[
        "data"
    ] == []


async def test_missing_media_object_dead_letters_once_and_stops_dispatch(
    api: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s, ws = await _setup(api, "missingmedia")
    ps, _acc, _ = await _four(api, s, ws, deferrer)
    await _approve_all(api, s, ws, ps["id"])
    pub, adapters = _publisher(api)
    target = (await _slots(api, ws, ps["id"]))[0]
    missing_key = f"ws/{ws}/missing-rendition.jpg"

    async def missing_media(*_args: Any) -> list[Any]:
        raise ObjectNotFound(missing_key)

    monkeypatch.setattr(pub, "_media", missing_media)
    outcome = await pub.publish(
        uuid.UUID(ws), target.id, now=target.scheduled_for + timedelta(seconds=1)
    )

    assert outcome == "dead_lettered"
    row = next(slot for slot in await _slots(api, ws, ps["id"]) if slot.id == target.id)
    assert row.state == "dead_lettered"
    assert row.next_attempt_at is None
    assert row.attempt_count == 0
    assert row.last_error == f"MEDIA_NOT_FOUND: attached media object is missing ({missing_key})."
    assert target.id not in {item.scheduled_post_id for item in await pub.due()}
    assert all(not adapter.posts for adapter in adapters.values())

    dead_letters = (await api.get(f"/api/v1/workspaces/{ws}/dead-letters", headers=_h(s))).json()[
        "data"
    ]
    matching = [item for item in dead_letters if item["subject_id"] == str(target.id)]
    assert len(matching) == 1
    assert matching[0]["error"].startswith("MEDIA_NOT_FOUND:")


async def test_lost_response_is_reconciled_not_double_posted(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "lostresp")
    ps, acc, _ = await _four(api, s, ws, deferrer, handles={"xx": f"x_lost_{uuid.uuid4().hex[:4]}"})
    await _approve_all(api, s, ws, ps["id"])
    pub, adapters = _publisher(api)
    sp = next(
        sp for sp in await _slots(api, ws, ps["id"]) if str(sp.social_account_id) == acc["xx"]
    )
    assert (
        await pub.publish(uuid.UUID(ws), sp.id, now=sp.scheduled_for + timedelta(seconds=1))
        == "retrying"
    )
    sp2 = next(x for x in await _slots(api, ws, ps["id"]) if x.id == sp.id)
    assert sp2.state == "failed"
    assert sp2.next_attempt_at is not None
    assert (
        await pub.publish(uuid.UUID(ws), sp.id, now=sp2.next_attempt_at + timedelta(seconds=1))
        == "published"
    )
    assert len(adapters["x"].posts) == 1  # one post on the platform, despite two attempts
    assert adapters["x"].attempts[sp.idempotency_key] == 1  # the retry reconciled, it did not post


async def test_first_comment_is_delivered_after_post_with_its_own_idempotency(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "firstcomment")
    ps, acc, _ = await _four(api, s, ws, deferrer)
    li = next(v for v in ps["variants"] if v["social_account_id"] == acc["li"])
    current = li["current_version"]
    r = await api.post(
        _sets(ws, ps["id"], f"/variants/{li['id']}/versions"),
        headers=_h(s),
        json={
            "body": current["body"],
            "thread_parts": current["thread_parts"],
            "hashtags": current["hashtags"],
            "link_url": current["link_url"],
            "first_comment": "Implementation notes are in the comments.",
            "based_on_version_id": current["id"],
        },
    )
    assert r.status_code == 201, r.text
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    assert (await _decide(api, s, ws, req, "approve", [by["li"]])).status_code == 200
    assert (
        await _decide(api, s, ws, req, "reject", [by["fb"], by["th"], by["xx"]])
    ).status_code == 200
    pub, adapters = _publisher(api)
    sp = (await _slots(api, ws, ps["id"]))[0]
    assert await pub.publish(uuid.UUID(ws), sp.id, now=sp.scheduled_for) == "published"
    assert [call["op"] for call in adapters["linkedin_member"].calls] == ["publish"]
    comment_time = sp.scheduled_for + timedelta(minutes=1)
    due = [c for c in await pub.comments_due(comment_time)]
    comment = next(c for c in due if str(c.workspace_id) == ws)
    assert (
        await pub.publish_comment(uuid.UUID(ws), comment.comment_id, now=comment_time)
        == "published"
    )
    assert await pub.publish_comment(uuid.UUID(ws), comment.comment_id) == "skipped"
    assert [call["op"] for call in adapters["linkedin_member"].calls] == ["publish", "comment"]
    assert len(adapters["linkedin_member"].posts) == 1
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        row = await tx.get(PublishedComment, comment.comment_id)
        assert row is not None
        assert row.state == "published"
        assert row.platform_comment_id is not None


async def test_rate_limit_backs_off_then_publishes(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "ratelimit")
    ps, acc, _ = await _four(
        api, s, ws, deferrer, handles={"th": f"th_ratelimit_{uuid.uuid4().hex[:4]}"}
    )
    await _approve_all(api, s, ws, ps["id"])
    pub, _ = _publisher(api)
    sp = next(
        sp for sp in await _slots(api, ws, ps["id"]) if str(sp.social_account_id) == acc["th"]
    )
    t = sp.scheduled_for + timedelta(seconds=1)
    assert await pub.publish(uuid.UUID(ws), sp.id, now=t) == "retrying"
    sp2 = next(x for x in await _slots(api, ws, ps["id"]) if x.id == sp.id)
    assert sp2.next_attempt_at is not None
    assert sp2.next_attempt_at >= datetime.now(UTC) + timedelta(seconds=25)
    assert "rate limit" in (sp2.last_error or "")
    assert await pub.publish(uuid.UUID(ws), sp.id, now=t) == "not_due"  # backing off
    assert await pub.publish(uuid.UUID(ws), sp.id, now=sp2.next_attempt_at) == "published"


async def test_revoked_account_holds_only_itself(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "revokedpub")
    ps, acc, _ = await _four(
        api, s, ws, deferrer, handles={"li": f"li_revoked_{uuid.uuid4().hex[:4]}"}
    )
    await _approve_all(api, s, ws, ps["id"])
    pub, _ = _publisher(api)
    outcomes = await _run_all(api, pub, ws, ps["id"])
    assert outcomes[acc["li"]] == "needs_reauth"
    assert sorted(o for k, o in outcomes.items() if k != acc["li"]) == ["published"] * 3
    account = (
        await api.get(f"/api/v1/workspaces/{ws}/social-accounts/{acc['li']}", headers=_h(s))
    ).json()
    assert account["status"] == "needs_reauth"
    li = next(
        sp for sp in await _slots(api, ws, ps["id"]) if str(sp.social_account_id) == acc["li"]
    )
    assert li.state == "queued"
    assert "On hold" in (li.last_error or "")
    # While it needs reconnecting, a later run holds again rather than failing it.
    assert (
        await pub.publish(uuid.UUID(ws), li.id, now=li.next_attempt_at + timedelta(seconds=1))
        == "held"
    )  # type: ignore[operator]


async def test_group_kill_switch_holds_publishing(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "killpub")
    ps, _acc, grp = await _four(api, s, ws, deferrer)
    await _approve_all(api, s, ws, ps["id"])
    r = await api.post(
        f"/api/v1/workspaces/{ws}/automation/kill-switch",
        headers=_h(s),
        json={"scope_type": "account_group", "scope_id": grp, "engaged": True, "reason": "check"},
    )
    assert r.status_code == 200, r.text
    pub, adapters = _publisher(api)
    outcomes = await _run_all(api, pub, ws, ps["id"])
    assert set(outcomes.values()) == {"held"}
    assert all(not a.posts for a in adapters.values())
    r = await api.post(
        f"/api/v1/workspaces/{ws}/automation/kill-switch",
        headers=_h(s),
        json={"scope_type": "account_group", "scope_id": grp, "engaged": False},
    )
    assert r.status_code == 200
    for sp in await _slots(api, ws, ps["id"]):
        assert (
            await pub.publish(uuid.UUID(ws), sp.id, now=sp.next_attempt_at + timedelta(seconds=1))
            == "published"
        )  # type: ignore[operator]


async def test_edit_after_approval_cancels_the_slot_and_never_publishes(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "editpub")
    ps, acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    assert (await _decide(api, s, ws, req, "approve", [by["xx"]])).status_code == 200
    xx = next(
        sp for sp in await _slots(api, ws, ps["id"]) if str(sp.social_account_id) == acc["xx"]
    )
    assert xx.state == "queued"
    await _edit(api, s, ws, ps["id"], by["xx"]["post_variant_id"])
    xx = next(sp for sp in await _slots(api, ws, ps["id"]) if sp.id == xx.id)
    assert xx.state == "cancelled"
    pub, adapters = _publisher(api)
    assert (
        await pub.publish(uuid.UUID(ws), xx.id, now=xx.scheduled_for + timedelta(minutes=1))
        == "skipped"
    )
    assert await _publishable(api, ws, by["xx"]["post_variant_id"]) is not None
    assert not adapters["x"].posts
    # Even a slot forced back into the queue is refused at the gate (version check).
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        await tx.execute(
            update(ScheduledPost).where(ScheduledPost.id == xx.id).values(state="queued")
        )
    assert (
        await pub.publish(uuid.UUID(ws), xx.id, now=xx.scheduled_for + timedelta(minutes=1))
        == "dead_lettered"
    )
    assert not adapters["x"].posts


async def test_unapproved_set_has_no_slot_and_schedule_refuses(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "noapproval")
    ps, _acc, _ = await _four(api, s, ws, deferrer)
    r = await api.post(_sets(ws, ps["id"], "/schedule"), headers=_h(s), json={})
    assert r.status_code == 200
    assert {x["outcome"] for x in r.json()["results"]} == {"approval_required"}
    assert await _slots(api, ws, ps["id"]) == []


async def test_reschedule_calendar_and_publish_now(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "calendar")
    ps, _acc, grp = await _four(api, s, ws, deferrer)
    await _approve_all(api, s, ws, ps["id"])
    slots = await _slots(api, ws, ps["id"])
    target = slots[0]
    when = (datetime.now(UTC) + timedelta(days=2)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    r = await api.patch(
        f"/api/v1/workspaces/{ws}/calendar/{target.id}/move",
        headers=_h(s),
        json={"scheduled_for": when.isoformat()},
    )
    assert r.status_code == 200, r.text
    assert datetime.fromisoformat(r.json()["scheduled_for"]) >= when
    cal = (
        await api.get(
            f"/api/v1/workspaces/{ws}/calendar",
            headers=_h(s),
            params={"group_id": grp},
        )
    ).json()["data"]
    assert len(cal) == 4
    assert all(c["group_name"] == "Tech News" for c in cal)
    # Publish one now: the slot moves to now and a job is queued for its account.
    r = await api.post(
        f"/api/v1/workspaces/{ws}/scheduled-posts/{target.id}/publish-now", headers=_h(s)
    )
    assert r.status_code == 200
    assert any(
        c["task"] == "publish:publish" and c["scheduled_post_id"] == str(target.id)
        for c in quiet.calls
    )
    # Cancelling frees it; the set-level schedule can place it again (same idempotency key).
    assert (
        await api.delete(f"/api/v1/workspaces/{ws}/scheduled-posts/{slots[1].id}", headers=_h(s))
    ).json()["state"] == "cancelled"
    res = (await api.post(_sets(ws, ps["id"], "/schedule"), headers=_h(s), json={})).json()[
        "results"
    ]
    assert sorted(x["outcome"] for x in res).count("scheduled") == 1
    assert len(await _slots(api, ws, ps["id"])) == 4


async def test_stagger_none_with_two_same_platform_accounts_is_blocked(
    api: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
    owner_engine: AsyncEngine,
) -> None:
    s, ws = await _setup(api, "staggerreq")
    ids = [
        (await _connect(api, s, ws, "x", handle=f"x{i}_{uuid.uuid4().hex[:4]}"))["connected"]
        for i in range(2)
    ]
    grp = await _group(api, s, ws, "Two X", ids)
    r = await api.post(
        _sets(ws),
        headers=_h(s),
        json={"topic": "Postgres 18 async I/O notes", "targets": {"account_group_ids": [grp]}},
    )
    await _run_queued(api, deferrer)
    set_id = r.json()["id"]
    # Simulate offsets from a group set to "none" (the group screen refuses that setting).
    async with owner_engine.begin() as c:
        await c.execute(
            text("UPDATE post_variant SET stagger_offset_seconds = 0 WHERE post_set_id = :i"),
            {"i": set_id},
        )
    r = await api.post(_sets(ws, set_id, "/request-approval"), headers=_h(s))
    assert r.status_code == 409
    assert "stagger" in r.text
    assert "none" in r.text


async def test_due_finds_slots_per_workspace(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "dueslots")
    ps, _acc, _ = await _four(api, s, ws, deferrer)
    await _approve_all(api, s, ws, ps["id"])
    pub, _ = _publisher(api)
    far = datetime.now(UTC) + timedelta(days=30)
    due = [d for d in await pub.due(far) if str(d.workspace_id) == ws]
    assert len(due) == 4
    n = await pub.enqueue_due(far)
    assert n >= 4
    calls = [c for c in pub._deferrer.calls if c["workspace_id"] == ws]  # type: ignore[union-attr]
    assert len({c["queueing_lock"] for c in calls}) == 4
    # Variants stay scheduled; the account rows are untouched by the scan.
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        states = set(
            (
                await tx.execute(
                    select(PostVariant.state).where(PostVariant.post_set_id == uuid.UUID(ps["id"]))
                )
            ).scalars()
        )
        assert states == {"scheduled"}
        assert all(
            a.status == "active" for a in (await tx.execute(select(SocialAccount))).scalars()
        )


async def test_held_same_platform_posts_resume_minutes_apart(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    """Found in the Sprint 14 browser run: after a workspace stop was released, two X
    accounts resumed one minute apart. Stagger now also holds at publish time."""
    s, ws = await _setup(api, "resumegap")
    ids = [
        (await _connect(api, s, ws, "x", handle=f"x{i}_{uuid.uuid4().hex[:4]}"))["connected"]
        for i in range(2)
    ]
    grp = await _group(api, s, ws, "Two X", ids)
    r = await api.post(
        _sets(ws),
        headers=_h(s),
        json={"topic": "Postgres 18 async I/O notes", "targets": {"account_group_ids": [grp]}},
    )
    await _run_queued(api, deferrer)
    await _approve_all(api, s, ws, r.json()["id"])
    stop = {"scope_type": "workspace", "engaged": True, "reason": "check"}
    assert (
        await api.post(f"/api/v1/workspaces/{ws}/automation/kill-switch", headers=_h(s), json=stop)
    ).status_code == 200
    pub, adapters = _publisher(api)
    held = await _run_all(api, pub, ws, r.json()["id"])
    assert set(held.values()) == {"held"}
    stop["engaged"] = False
    assert (
        await api.post(f"/api/v1/workspaces/{ws}/automation/kill-switch", headers=_h(s), json=stop)
    ).status_code == 200
    # The next dispatcher tick after the release: both held slots are due together.
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        await tx.execute(
            update(ScheduledPost)
            .where(ScheduledPost.post_set_id == uuid.UUID(r.json()["id"]))
            .values(next_attempt_at=datetime.now(UTC))
        )
    slots = await _slots(api, ws, r.json()["id"])
    tick = datetime.now(UTC) + timedelta(seconds=1)
    first = await pub.publish(uuid.UUID(ws), slots[0].id, now=tick)
    second = await pub.publish(uuid.UUID(ws), slots[1].id, now=tick)
    assert (first, second) == ("published", "spaced")
    again = next(sp for sp in await _slots(api, ws, r.json()["id"]) if sp.id == slots[1].id)
    published = next(sp for sp in await _slots(api, ws, r.json()["id"]) if sp.id == slots[0].id)
    assert again.state == "queued"
    assert again.next_attempt_at is not None
    assert published.published_at is not None
    assert again.next_attempt_at - published.published_at >= timedelta(minutes=3)
    assert len(adapters["x"].posts) == 1
