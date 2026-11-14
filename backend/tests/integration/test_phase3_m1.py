"""P3-M1.1 hashtag attribution against real Postgres (docs/14).

One real post set is published through the whole flow; its LinkedIn post is then cloned
in the database into a history of 28 posts with metrics (cloning rows is far quicker than
publishing 28 sets, and the job only reads published posts, versions and snapshots). The
daily job runs for real, and the endpoint is read as a member would.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import Table, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from mera.application.analytics.hashtag_stats import HashtagStats
from mera.infrastructure.db.models import (
    MetricSnapshot,
    PostVersion,
    PublishedPost,
    ScheduledPost,
)
from tests.integration.test_accounts_m3 import _app, _setup
from tests.integration.test_analytics_m10 import _published_set
from tests.integration.test_drafting_m5 import CapturingDeferrer, deferrer  # noqa: F401
from tests.integration.test_identity_m2 import _h
from tests.integration.test_ingest_m4 import _sm
from tests.integration.test_publishing_m8 import quiet  # noqa: F401

pytestmark = pytest.mark.integration


async def _clone(conn: AsyncConnection, table: Table, row_id: uuid.UUID, **changes: Any) -> Any:
    row = (await conn.execute(select(table).where(table.c.id == row_id))).mappings().one()
    new = {**row, "id": uuid.uuid4(), **changes}
    await conn.execute(insert(table).values(**new))
    return new["id"]


async def test_hashtag_lift_per_account_with_honest_samples(
    api: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
    owner_engine: AsyncEngine,
) -> None:
    s, ws = await _setup(api, "tagstats")
    _ps, acc = await _published_set(api, s, ws, deferrer)
    li = uuid.UUID(acc["li"])
    now = datetime.now(UTC)
    pv, sp, pp = PostVersion.__table__, ScheduledPost.__table__, PublishedPost.__table__
    async with owner_engine.begin() as conn:
        post = (await conn.execute(select(pp).where(pp.c.social_account_id == li))).mappings().one()
        # Three versions: tagged #Postgres, tagged with a rarely used tag, and untagged.
        versions = {}
        for name, tags in (("postgres", ["#Postgres"]), ("rare", ["rare"]), ("plain", [])):
            versions[name] = await _clone(
                conn,
                pv,
                post["post_version_id"],
                hashtags=tags,
                body=f"A post about databases ({name}).",
                version_no=900 + len(versions),
                content_hash=os.urandom(32),
            )
        # 12 tagged posts at 40, 12 plain at 10, 2 "rare" at 10: the median is 10.
        plan = [("postgres", 40)] * 12 + [("plain", 10)] * 12 + [("rare", 10)] * 2
        for i, (name, likes) in enumerate(plan):
            sched = await _clone(
                conn, sp, post["scheduled_post_id"], idempotency_key=uuid.uuid4().hex
            )
            pid = await _clone(
                conn,
                pp,
                post["id"],
                scheduled_post_id=sched,
                post_version_id=versions[name],
                platform_post_id=f"clone-{i}-{uuid.uuid4().hex[:6]}",
                published_at=now - timedelta(hours=i + 1),
            )
            await conn.execute(
                insert(MetricSnapshot.__table__).values(
                    workspace_id=uuid.UUID(ws),
                    subject_type="post",
                    published_post_id=pid,
                    social_account_id=li,
                    metric_key="likes",
                    value=likes,
                    platform_field="likes",
                    captured_at=now,
                )
            )

    out = await HashtagStats(_sm(api)).refresh_workspace(uuid.UUID(ws), now)
    assert out["accounts"] >= 4
    r = await api.get(
        f"/api/v1/workspaces/{ws}/hashtags/stats",
        headers=_h(s),
        params={"social_account_id": str(li)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    rows = {x["tag"]: x for x in body["data"]}
    assert body["min_samples"] == 10
    assert rows["postgres"]["post_count"] == 12
    assert rows["postgres"]["evidence"] == "measured"
    assert rows["postgres"]["lift"] == pytest.approx(3.0)  # 4x typical, capped at 3
    # Two posts are luck, not evidence: no lift is given at all.
    assert rows["rare"]["post_count"] == 2
    assert rows["rare"]["evidence"] == "not_enough_data"
    assert rows["rare"]["lift"] is None
    # Recomputing replaces the account's rows; nothing doubles.
    await HashtagStats(_sm(api)).refresh_workspace(uuid.UUID(ws), now)
    again = (
        await api.get(
            f"/api/v1/workspaces/{ws}/hashtags/stats",
            headers=_h(s),
            params={"social_account_id": str(li), "tag": "#Postgres"},
        )
    ).json()["data"]
    assert len(again) == 1
    assert again[0]["post_count"] == 12


async def test_confidence_calibration_counts_decided_agent_versions(
    api: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
) -> None:
    """P3-M1.6: four agent-drafted accounts approved as written are four outcomes."""
    s, ws = await _setup(api, "calibrate")
    _ps, acc = await _published_set(api, s, ws, deferrer)
    r = await api.get(f"/api/v1/workspaces/{ws}/analytics/confidence-calibration", headers=_h(s))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workspace"]["decided"] == 4
    assert sum(b["approved_unedited"] for b in body["workspace"]["bands"]) == 4
    assert body["workspace"]["separates"] is None  # four decisions prove nothing yet
    assert all(b["rate"] is None for b in body["workspace"]["bands"])
    assert set(body["accounts"]) == set(acc.values())
    one = (
        await api.get(
            f"/api/v1/workspaces/{ws}/analytics/confidence-calibration",
            headers=_h(s),
            params={"account_id": acc["li"]},
        )
    ).json()
    assert one["workspace"]["decided"] == 1


async def test_weekly_insights_cite_evidence_and_move_weights_reversibly(
    api: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
    owner_engine: AsyncEngine,
) -> None:
    """P3-M1.3 to 1.5: a source whose posts do 3x the account's typical post is called out,
    its weight moves by at most 15%, the move reverts exactly, and the summary cites only
    the week's findings."""
    import hashlib

    from mera.application.analytics.insights import InsightRun
    from mera.domain.context import WorkspaceContext
    from mera.infrastructure.db.models import ContentItem, PostSet, Source
    from mera.infrastructure.db.session import workspace_transaction

    s, ws = await _setup(api, "insights")
    _ps, acc = await _published_set(api, s, ws, deferrer)
    li = uuid.UUID(acc["li"])
    wsid = uuid.UUID(ws)
    now = datetime.now(UTC)
    async with workspace_transaction(_sm(api), WorkspaceContext(wsid)) as tx:
        src = Source(id=uuid.uuid4(), workspace_id=wsid, source_type="rss", name="Postgres Weekly")
        tx.add(src)
        await tx.flush()
        item = ContentItem(
            id=uuid.uuid4(),
            workspace_id=wsid,
            source_id=src.id,
            url="https://pg.example/a",
            canonical_url="https://pg.example/a",
            url_hash=hashlib.sha256(b"pg-a").digest(),
            title="Async I/O",
        )
        tx.add(item)
        await tx.flush()
        src_id, item_id = src.id, item.id
    sp, pp, pst = ScheduledPost.__table__, PublishedPost.__table__, PostSet.__table__
    async with owner_engine.begin() as conn:
        post = (await conn.execute(select(pp).where(pp.c.social_account_id == li))).mappings().one()
        from_source = await _clone(conn, pst, post["post_set_id"], content_item_id=item_id)
        plan = [(from_source, 30)] * 12 + [(post["post_set_id"], 10)] * 14
        for i, (set_id, likes) in enumerate(plan):
            sched = await _clone(
                conn, sp, post["scheduled_post_id"], idempotency_key=uuid.uuid4().hex
            )
            pid = await _clone(
                conn,
                pp,
                post["id"],
                post_set_id=set_id,
                scheduled_post_id=sched,
                platform_post_id=f"ins-{i}-{uuid.uuid4().hex[:6]}",
                published_at=now - timedelta(hours=i + 1),
            )
            await conn.execute(
                insert(MetricSnapshot.__table__).values(
                    workspace_id=wsid,
                    subject_type="post",
                    published_post_id=pid,
                    social_account_id=li,
                    metric_key="likes",
                    value=likes,
                    platform_field="likes",
                    captured_at=now,
                )
            )
    drafting = _app(api).state.post_service._drafting
    out = await InsightRun(_sm(api), drafting).refresh_workspace(wsid, now, force=True)
    assert out["insights"] >= 1
    assert out["weights"] == 1
    data = (await api.get(f"/api/v1/workspaces/{ws}/analytics/insights", headers=_h(s))).json()[
        "data"
    ]
    source = next(x for x in data if x["kind"] == "source")
    assert source["title"] == "Source Postgres Weekly does well"
    assert source["evidence"]["posts"] == 12
    assert source["evidence"]["mean_ratio"] == pytest.approx(3.0)
    summary = next(x for x in data if x["kind"] == "summary")
    ids = {x["id"] for x in data if x["kind"] != "summary"}
    assert summary["evidence"]["insights"]
    assert set(summary["evidence"]["insights"]) <= ids  # cites only the week's findings
    r = await api.post(
        f"/api/v1/workspaces/{ws}/analytics/insights/{source['id']}/acknowledge", headers=_h(s)
    )
    assert r.json()["acknowledged_at"]
    w = (await api.get(f"/api/v1/workspaces/{ws}/analytics/weights", headers=_h(s))).json()
    change = w["changes"][0]
    assert change["subject_id"] == str(src_id)
    assert (change["before"], change["after"]) == (1.0, pytest.approx(1.15))
    assert change["insight_id"] == source["id"]
    r = await api.post(
        f"/api/v1/workspaces/{ws}/analytics/weight-changes/{change['id']}/revert", headers=_h(s)
    )
    assert r.status_code == 200, r.text
    again = await api.post(
        f"/api/v1/workspaces/{ws}/analytics/weight-changes/{change['id']}/revert", headers=_h(s)
    )
    assert again.json()["error"]["code"] == "already_reverted"
    w = (await api.get(f"/api/v1/workspaces/{ws}/analytics/weights", headers=_h(s))).json()
    assert next(x for x in w["current"] if x["subject_id"] == str(src_id))["weight"] == 1.0
    # Weekly, not on every call; and the loop can be turned off.
    assert (await InsightRun(_sm(api)).refresh_workspace(wsid, now))["skipped"] == 1
    r = await api.put(
        f"/api/v1/workspaces/{ws}/analytics/settings", headers=_h(s), json={"auto_tune": False}
    )
    assert r.json() == {"auto_tune": False}
    off = await InsightRun(_sm(api)).refresh_workspace(wsid, now, force=True)
    assert off.get("weights", 0) == 0
    cmp = (await api.get(f"/api/v1/workspaces/{ws}/analytics/comparison", headers=_h(s))).json()
    li_row = next(x for x in cmp["accounts"] if x["key"] == str(li))
    assert li_row["posts"] == 26
    assert li_row["evidence"] == "measured"
