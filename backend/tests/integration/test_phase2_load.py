"""P2-M10.3 load check: seven platforms, 50 accounts, inbox polling on.

Fifty accounts across LinkedIn, Facebook, Instagram, Threads, X, Bluesky and Mastodon each
publish one post through the real flow (draft, approve, place, publish), so every post
has its real rows. Then the inbox polls all of them at once: 200 new comments, the triage
budget (50) used, the rest left for the next poll. The checks are generous on purpose
(they run on laptops and CI runners); what they catch is a poll or a listing that grows
with the number of accounts in a way it should not, and the publish dispatcher handing
two accounts the same lock.
"""

from __future__ import annotations

import statistics
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from mera.application.publishing.publisher import Publisher
from mera.application.services.inbox_service import InboxService
from mera.domain.context import WorkspaceContext
from mera.domain.value_objects.platform import Platform
from mera.infrastructure.db.models import PublishedPost
from mera.infrastructure.db.session import workspace_transaction
from mera.infrastructure.platforms.adapters.fake import FakePlatformAdapter
from mera.infrastructure.platforms.engagement import FakeEngagement
from tests.integration.conftest import make_api
from tests.integration.test_accounts_m3 import _app, _connect, _setup
from tests.integration.test_approvals_m6 import _decide, _submit
from tests.integration.test_drafting_m5 import CapturingDeferrer, _run_queued, _sets, deferrer  # noqa: F401
from tests.integration.test_identity_m2 import _h
from tests.integration.test_ingest_m4 import _sm
from tests.integration.test_media_m5 import _photo, _upload
from tests.integration.test_publishing_m8 import _run_all, quiet  # noqa: F401

pytestmark = pytest.mark.integration

PLATFORMS: list[tuple[str, str]] = [
    ("linkedin_member", "personal"),
    ("facebook", "page"),
    ("instagram", "business"),
    ("threads", "personal"),
    ("x", "personal"),
    ("bluesky", "personal"),
    ("mastodon", "personal"),
]


@pytest.fixture
async def api(postgres_urls: tuple[str, str]) -> AsyncIterator[httpx.AsyncClient]:
    """Fifty accounts in one workspace: above the default cap of 25, which a deployment
    raises with MAX_ACCOUNTS_PER_WORKSPACE (up to 500)."""
    async with make_api(
        postgres_urls, rate_limit_enabled=False, max_accounts_per_workspace=60
    ) as c:
        yield c


class Locks:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def defer(
        self,
        task_name: str,
        *,
        queue: str,
        lock: str | None = None,
        queueing_lock: str | None = None,
        **kw: Any,
    ) -> bool:
        self.calls.append({"lock": lock, "queueing_lock": queueing_lock, **kw})
        return True


async def test_fifty_accounts_seven_platforms_inbox_polling(
    api: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
) -> None:
    s, ws = await _setup(api, "loadphase2")
    accounts: dict[str, list[str]] = {p: [] for p, _ in PLATFORMS}
    for i in range(50):
        platform, kind = PLATFORMS[i % len(PLATFORMS)]
        out = await _connect(
            api, s, ws, platform, handle=f"{platform[:4]}_{i}_{uuid.uuid4().hex[:4]}", kind=kind
        )
        accounts[platform].append(out["connected"])
    assert sum(len(v) for v in accounts.values()) == 50
    photo = (await _upload(api, s, ws, _photo(), alt="A dashboard")).json()

    # One post set per "row" of accounts: one account of each platform, so no two siblings
    # share a platform (the stagger rule is per platform) and every set has seven or fewer.
    sets = []
    for row in range(8):
        ids = [accounts[p][row] for p, _ in PLATFORMS if row < len(accounts[p])]
        r = await api.post(
            _sets(ws),
            headers=_h(s),
            json={
                "topic": f"Row {row}: what changed in release {row}.{row} of our read path",
                "targets": {"social_account_ids": ids},
            },
        )
        assert r.status_code == 201, r.text
        sets.append(r.json()["id"])
    await _run_queued(api, deferrer)
    for sid in sets:
        ps = (await api.get(_sets(ws, sid), headers=_h(s))).json()
        for v in ps["variants"]:
            if v["platform"] == "instagram":  # Instagram cannot post without an image
                r = await api.put(
                    _sets(ws, sid, f"/variants/{v['id']}/media"),
                    headers=_h(s),
                    json={
                        "media": [{"asset_id": photo["id"]}],
                        "based_on_version_id": v["current_version"]["id"],
                    },
                )
                assert r.status_code == 200, r.text
        req = await _submit(api, s, ws, sid)
        r = await _decide(api, s, ws, req, "approve", None)
        assert r.status_code == 200, r.text

    app = _app(api)
    pub = Publisher(
        _sm(api),
        {p.value: FakePlatformAdapter(p.value) for p in Platform},
        app.state.key_provider,
        app.state.object_store,
        deferrer=CapturingDeferrer(),
        sandbox=True,
    )
    # The dispatcher: fifty due slots, fifty different per-account locks.
    locks = Locks()
    pub._deferrer = locks  # type: ignore[assignment]
    await pub.enqueue_due(datetime.now(UTC) + timedelta(days=2))
    mine = [c for c in locks.calls if c.get("workspace_id") == ws]
    assert len(mine) == 50
    assert len({c["lock"] for c in mine}) == 50
    pub._deferrer = CapturingDeferrer()  # type: ignore[assignment]
    outcomes: dict[str, str] = {}
    for sid in sets:
        outcomes.update(await _run_all(api, pub, ws, sid))
    assert list(outcomes.values()).count("published") == 50, outcomes

    # ---- inbox polling on all fifty ----
    inbox: InboxService = app.state.inbox_service
    saved = inbox._eng
    inbox._eng = {p.value: FakeEngagement(p.value) for p in Platform}
    try:
        async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
            last = max((await tx.execute(select(PublishedPost.published_at))).scalars())
        t0 = time.perf_counter()
        first = await inbox.poll_workspace(uuid.UUID(ws), last + timedelta(minutes=6))
        first_s = time.perf_counter() - t0
        assert first["polled"] == 50, first
        assert first["new"] == 200, first
        assert first["triaged"] == 50, first  # the per-poll triage budget
        t0 = time.perf_counter()
        second = await inbox.poll_workspace(uuid.UUID(ws), last + timedelta(minutes=12))
        second_s = time.perf_counter() - t0
        assert second.get("new", 0) == 0, second
        assert second["polled"] == 50, second
        assert second["triaged"] == 50, second  # the next fifty from the backlog
    finally:
        inbox._eng = saved
    timings = []
    for _ in range(5):
        t0 = time.perf_counter()
        r = await api.get(f"/api/v1/workspaces/{ws}/inbox/comments?limit=100", headers=_h(s))
        timings.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200
        assert len(r.json()["data"]) == 100
    median_ms = statistics.median(timings)
    print(f"\nload: first poll {first_s:.1f}s, second {second_s:.1f}s, listing {median_ms:.0f}ms")
    assert first_s < 90, first_s
    assert second_s < 60, second_s
    assert statistics.median(timings[1:]) < 1500, timings
