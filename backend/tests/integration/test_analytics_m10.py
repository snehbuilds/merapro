"""M10 analytics against real Postgres (docs/11 M10 acceptance), plus the SSE stream and the
audit log filters."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from mera.application.analytics.sync import MetricsSync, next_sync
from mera.domain.context import WorkspaceContext
from mera.domain.ports.platform_adapter import MetricReading, RateLimited
from mera.infrastructure.db.models import MetricSnapshot, PublishedPost
from mera.infrastructure.db.session import workspace_transaction
from mera.infrastructure.platforms.adapters.fake import FakePlatformAdapter
from tests.integration.test_accounts_m3 import _app, _setup
from tests.integration.test_approvals_m6 import _decide, _submit
from tests.integration.test_drafting_m5 import CapturingDeferrer, deferrer  # noqa: F401
from tests.integration.test_identity_m2 import _h
from tests.integration.test_ingest_m4 import _sm
from tests.integration.test_publishing_m8 import _four, _publisher, _run_all, quiet  # noqa: F401

pytestmark = pytest.mark.integration


class _Limited(FakePlatformAdapter):
    async def post_metrics(
        self, credentials: dict[str, Any], platform_account_id: str, platform_post_id: str
    ) -> list[MetricReading]:
        raise RateLimited("slow down", retry_after=900)


def _sync(api: httpx.AsyncClient, adapters: dict[str, Any] | None = None) -> MetricsSync:
    app = _app(api)
    adapters = adapters or {
        p: FakePlatformAdapter(p) for p in ("linkedin_member", "facebook", "threads", "x")
    }
    return MetricsSync(_sm(api), adapters, app.state.key_provider)


async def _published_set(
    api: httpx.AsyncClient, s: dict[str, Any], ws: str, deferrer: CapturingDeferrer, **kw: Any
) -> tuple[dict[str, Any], dict[str, str]]:
    ps, acc, _ = await _four(api, s, ws, deferrer, **kw)
    req = await _submit(api, s, ws, ps["id"])
    assert (await _decide(api, s, ws, req, "approve", None)).status_code == 200
    pub, _ = _publisher(api)
    await _run_all(api, pub, ws, ps["id"])
    return ps, acc


async def _due_now(owner_engine: AsyncEngine, ws: str) -> None:
    async with owner_engine.begin() as c:
        await c.execute(
            text(
                "UPDATE published_post SET next_metrics_at = now() - interval '1 minute' "
                "WHERE workspace_id = :w"
            ),
            {"w": ws},
        )


async def test_sync_is_idempotent_and_not_reported_is_null(
    api: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
    owner_engine: AsyncEngine,
) -> None:
    s, ws = await _setup(api, "metrics")
    ps, acc = await _published_set(api, s, ws, deferrer)
    await _due_now(owner_engine, ws)
    sync = _sync(api)
    now = datetime.now(UTC)
    first = await sync.sync_workspace(uuid.UUID(ws), now)
    assert first["posts"] == 4
    assert first["snapshots"] > 0
    await _due_now(owner_engine, ws)
    again = await sync.sync_workspace(uuid.UUID(ws), now)  # same window: nothing new
    assert again.get("snapshots", 0) == 0
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        assert (
            await tx.scalar(
                select(func.count())
                .select_from(MetricSnapshot)
                .where(MetricSnapshot.subject_type == "post")
            )
            == first["snapshots"]
        )
        assert all(
            p.next_metrics_at is not None
            for p in (await tx.execute(select(PublishedPost))).scalars()
        )
    # LinkedIn personal profiles report no impressions: null, never zero.
    li_only = (
        await api.get(
            f"/api/v1/workspaces/{ws}/analytics/overview",
            headers=_h(s),
            params={"account_id": acc["li"]},
        )
    ).json()
    assert li_only["totals"]["impressions"] is None
    assert li_only["totals"]["likes"] is not None
    every = (await api.get(f"/api/v1/workspaces/{ws}/analytics/overview", headers=_h(s))).json()
    assert every["totals"]["posts"] == 4
    assert every["totals"]["impressions"] is not None
    li_row = next(r for r in every["by_platform"] if r["platform"] == "linkedin_member")
    assert li_row["impressions"] is None
    # Set: combined and per-account split, both present and distinct.
    res = (
        await api.get(f"/api/v1/workspaces/{ws}/post-sets/{ps['id']}/results", headers=_h(s))
    ).json()
    assert res["combined"]["posts"] == 4
    assert len(res["accounts"]) == 4
    assert res["combined"]["likes"] == sum(a["metrics"]["likes"] or 0 for a in res["accounts"])
    # CSV: "not reported" in the cell, not an empty or a zero.
    r = await api.get(f"/api/v1/workspaces/{ws}/analytics/export.csv", headers=_h(s))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "not reported" in r.text
    assert r.text.count("\n") == 5
    ts = (
        await api.get(
            f"/api/v1/workspaces/{ws}/analytics/timeseries",
            headers=_h(s),
            params={"metric": "posts"},
        )
    ).json()
    assert sum(p["value"] for p in ts["points"]) == 4
    post = (
        await api.get(
            f"/api/v1/workspaces/{ws}/analytics/posts/{res['accounts'][0]['published_post_id']}",
            headers=_h(s),
        )
    ).json()
    assert post["synced"]
    assert post["series"]


def test_cadence_tapers_with_age() -> None:
    now = datetime(2026, 11, 10, tzinfo=UTC)
    assert next_sync(now - timedelta(hours=2), now) == now + timedelta(hours=1)
    assert next_sync(now - timedelta(days=3), now) == now + timedelta(hours=6)
    assert next_sync(now - timedelta(days=20), now) == now + timedelta(days=1)
    assert next_sync(now - timedelta(days=40), now) is None


async def test_rate_limited_sync_reschedules_without_starving_others(
    api: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
    owner_engine: AsyncEngine,
) -> None:
    s, ws = await _setup(api, "metricslimit")
    _ps, acc = await _published_set(api, s, ws, deferrer)
    await _due_now(owner_engine, ws)
    adapters: dict[str, Any] = {
        p: FakePlatformAdapter(p) for p in ("linkedin_member", "facebook", "threads")
    }
    adapters["x"] = _Limited("x")
    out = await _sync(api, adapters).sync_workspace(uuid.UUID(ws), datetime.now(UTC))
    assert out["rate_limited"] == 1
    assert out["posts"] == 3
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        x = (
            await tx.execute(
                select(PublishedPost).where(PublishedPost.social_account_id == uuid.UUID(acc["xx"]))
            )
        ).scalar_one()
    assert x.next_metrics_at is not None
    assert x.next_metrics_at > datetime.now(UTC) + timedelta(minutes=10)
    assert x.metrics_synced_at is None  # the window is kept, not lost


async def test_account_comparison_uses_each_accounts_baseline(
    api: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
    owner_engine: AsyncEngine,
) -> None:
    s, ws = await _setup(api, "compare")
    first, acc = await _published_set(api, s, ws, deferrer)
    # Make the first set's posts history (30 days ago), then publish a second set now.
    async with owner_engine.begin() as c:
        await c.execute(
            text(
                "UPDATE published_post SET published_at = published_at - interval '30 days' "
                "WHERE post_set_id = :p"
            ),
            {"p": first["id"]},
        )
    # A second set on the same accounts (connected again under the same handles is not
    # possible; publish one more set to the same group instead).
    ps2 = await api.post(
        f"/api/v1/workspaces/{ws}/post-sets",
        headers=_h(s),
        json={
            "topic": "Second story about read replicas",
            "targets": {"social_account_ids": list(acc.values())},
        },
    )
    from tests.integration.test_drafting_m5 import _run_queued

    await _run_queued(api, deferrer)
    req = await _submit(api, s, ws, ps2.json()["id"])
    assert (await _decide(api, s, ws, req, "approve", None)).status_code == 200
    pub, _ = _publisher(api)
    await _run_all(api, pub, ws, ps2.json()["id"])
    await _due_now(owner_engine, ws)
    await _sync(api).sync_workspace(uuid.UUID(ws), datetime.now(UTC))
    rows = (
        await api.get(
            f"/api/v1/workspaces/{ws}/analytics/account-comparison",
            headers=_h(s),
            params={"from": (datetime.now(UTC) - timedelta(days=7)).isoformat()},
        )
    ).json()["data"]
    assert len(rows) == 4
    for r in rows:
        assert r["posts"] == 1
        assert r["baseline_posts"] == 1
        assert r["index_vs_baseline"] == pytest.approx(
            r["median_engagement"] / r["baseline_median_engagement"], rel=1e-3
        )
    groups = (
        await api.get(f"/api/v1/workspaces/{ws}/analytics/group-comparison", headers=_h(s))
    ).json()["data"]
    assert groups[0]["name"] == "Tech News"
    assert groups[0]["accounts"] == 4
    turn = (
        await api.get(f"/api/v1/workspaces/{ws}/analytics/approval-turnaround", headers=_h(s))
    ).json()
    assert turn["count"] == 2
    assert turn["median_minutes"] is not None
    top = (
        await api.get(
            f"/api/v1/workspaces/{ws}/analytics/top-posts", headers=_h(s), params={"limit": 3}
        )
    ).json()["data"]
    assert len(top) == 3


async def test_events_stream_replays_after_last_event_id(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "sse")
    ps, _acc, _ = await _four(api, s, ws, deferrer)
    r = await api.get(
        f"/api/v1/workspaces/{ws}/events",
        headers={**_h(s), "Last-Event-ID": "0"},
        params={"once": "true", "types": "approval.*"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    req = await _submit(api, s, ws, ps["id"])
    r = await api.get(
        f"/api/v1/workspaces/{ws}/events",
        headers={**_h(s), "Last-Event-ID": "0"},
        params={"once": "true", "types": "approval.*"},
    )
    body = r.text
    assert "event: approval.requested" in body
    assert req["id"] in body
    assert "post.scheduled" not in body  # filtered by glob
    last = max(int(line[4:]) for line in body.splitlines() if line.startswith("id: "))
    r = await api.get(
        f"/api/v1/workspaces/{ws}/events",
        headers={**_h(s), "Last-Event-ID": str(last)},
        params={"once": "true"},
    )
    assert "event: approval.requested" not in r.text


async def test_audit_log_filters_and_current_actor_name(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "auditview")
    ps, acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    assert (await _decide(api, s, ws, req, "approve", None)).status_code == 200
    r = await api.get(
        f"/api/v1/workspaces/{ws}/audit-log", headers=_h(s), params={"action": "approval."}
    )
    rows = r.json()["data"]
    assert rows
    assert all(x["action"].startswith("approval.") for x in rows)
    assert all(x["actor_name"] == "auditview" for x in rows if x["actor_type"] == "user")
    created = next(
        x
        for x in rows
        if x["action"] == "approval_request.created" or x["action"].startswith("approval.")
    )
    assert created["surface"] in ("api", "web")
    r = await api.get(
        f"/api/v1/workspaces/{ws}/audit-log", headers=_h(s), params={"account_id": acc["li"]}
    )
    assert all(x["social_account_id"] == acc["li"] for x in r.json()["data"])
