"""M11: the demo seed end to end (11.4) and the load sanity check (11.8)."""

from __future__ import annotations

import statistics
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from mera.application.approvals.sweeper import sweep_workspace
from mera.config import Settings, get_settings
from mera.domain.context import WorkspaceContext
from mera.infrastructure.db.models import Workspace
from mera.infrastructure.db.session import workspace_transaction
from tests.integration.test_accounts_m3 import _setup
from tests.integration.test_approvals_m6 import _submit
from tests.integration.test_drafting_m5 import CapturingDeferrer, deferrer  # noqa: F401
from tests.integration.test_ingest_m4 import _sm
from tests.integration.test_publishing_m8 import _four, _publisher, quiet  # noqa: F401

pytestmark = pytest.mark.integration


async def test_demo_seed_builds_a_usable_sandbox(postgres_urls: tuple[str, str]) -> None:
    from mera.seed import EMAIL, PASSWORD, seed

    owner, app = postgres_urls
    settings = Settings(
        **{
            **get_settings().model_dump(),
            "database_url": app,
            "database_migration_url": owner,
            "sandbox_llm": True,
            "sandbox_mode": True,
            "media_root": tempfile.mkdtemp(prefix="mera-seed-"),
        }
    )
    out = await seed(settings)
    assert out.startswith("seeded")
    assert await seed(settings) == "already seeded"
    from mera.api.main import create_app

    api_app = create_app(settings.model_copy(update={"rate_limit_enabled": False}))
    async with (
        api_app.router.lifespan_context(api_app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=api_app), base_url="http://t") as c,
    ):
        tok = (
            await c.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        ).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        spaces = {
            w["slug"]: w for w in (await c.get("/api/v1/workspaces", headers=h)).json()["data"]
        }
        assert {"acme", "northwind"} <= set(spaces)
        acme = spaces["acme"]["id"]
        accounts = (await c.get(f"/api/v1/workspaces/{acme}/social-accounts", headers=h)).json()[
            "data"
        ]
        assert len(accounts) == 5
        groups = (await c.get(f"/api/v1/workspaces/{acme}/account-groups", headers=h)).json()[
            "data"
        ]
        assert {g["name"] for g in groups} == {"Tech News", "Company Official"}
        since = (datetime.now(UTC) - timedelta(days=91)).isoformat()
        ov = (
            await c.get(
                f"/api/v1/workspaces/{acme}/analytics/overview", headers=h, params={"from": since}
            )
        ).json()
        assert ov["totals"]["posts"] >= 30
        li = next(r for r in ov["by_platform"] if r["platform"] == "linkedin_member")
        assert li["impressions"] is None  # not reported, even in the seed
        cmp_ = (
            await c.get(
                f"/api/v1/workspaces/{acme}/analytics/account-comparison",
                headers=h,
                params={"from": (datetime.now(UTC) - timedelta(days=30)).isoformat()},
            )
        ).json()["data"]
        assert any(r["index_vs_baseline"] is not None for r in cmp_)
        open_ = (await c.get(f"/api/v1/workspaces/{acme}/approval-requests", headers=h)).json()[
            "data"
        ]
        assert len(open_) == 1  # one set waiting, for the queue and the bot


async def test_load_sweeper_and_dispatcher(
    api: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
    owner_engine: AsyncEngine,
) -> None:
    """50 open requests covering 200 accounts' approvals: the sweep stays under 50 ms when
    nothing is due (the partial indexes), and due slots for ten accounts get ten distinct
    per-account locks (no account waits on another)."""
    s, ws = await _setup(api, "loadcheck")
    ps, _acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    async with owner_engine.begin() as c:
        for _ in range(49):
            rid = uuid.uuid4()
            await c.execute(
                text(
                    "INSERT INTO approval_request (id, workspace_id, subject_type, "
                    "post_set_id, account_group_id, content_hash, state, policy_snapshot, "
                    "remind_at, escalate_at, expires_at) SELECT :n, workspace_id, "
                    "subject_type, post_set_id, account_group_id, "
                    "content_hash, 'pending', policy_snapshot, now() + interval '2 hours', "
                    "now() + interval '4 hours', now() + interval '12 hours' FROM approval_request "
                    "WHERE id = :r"
                ),
                {"n": rid, "r": req["id"]},
            )
            await c.execute(
                text(
                    "INSERT INTO approval_variant_state (id, workspace_id, approval_request_id, "
                    "post_variant_id, post_version_id, social_account_id, content_hash, state, "
                    "required_count, quorum_mode) SELECT gen_random_uuid(), workspace_id, :n, "
                    "post_variant_id, post_version_id, social_account_id, content_hash, 'pending', "
                    "required_count, quorum_mode FROM approval_variant_state "
                    "WHERE approval_request_id = :r"
                ),
                {"n": rid, "r": req["id"]},
            )
        n = (
            await c.execute(
                text(
                    "SELECT count(*) FROM approval_variant_state avs JOIN approval_request r "
                    "ON r.id = avs.approval_request_id "
                    "WHERE r.workspace_id = :w AND r.state = 'pending'"
                ),
                {"w": ws},
            )
        ).scalar()
    assert n == 200
    timings = []
    for _ in range(6):
        async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
            w = (
                await tx.execute(select(Workspace).where(Workspace.id == uuid.UUID(ws)))
            ).scalar_one()
            t0 = time.perf_counter()
            steps = await sweep_workspace(tx, w, datetime.now(UTC))
            timings.append((time.perf_counter() - t0) * 1000)
            assert steps == []
    assert statistics.median(timings[1:]) < 50, timings

    # Ten accounts, one due slot each: ten jobs with ten different account locks.
    pub, _ = _publisher(api)

    class Locks:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def defer(
            self,
            task_name: str,
            *,
            queue: str,
            lock: str | None = None,
            queueing_lock: str | None = None,
            **kw: object,
        ) -> bool:
            self.calls.append({"lock": lock, "queueing_lock": queueing_lock, **kw})
            return True

    pub._deferrer = Locks()  # type: ignore[assignment]
    s2, ws2 = await _setup(api, "tenaccounts")
    from tests.integration.test_accounts_m3 import _connect
    from tests.integration.test_drafting_m5 import _run_queued, _sets
    from tests.integration.test_identity_m2 import _h

    ids = [
        (await _connect(api, s2, ws2, p, handle=f"{p}_{i}_{uuid.uuid4().hex[:4]}", kind=k))[
            "connected"
        ]
        for i, (p, k) in enumerate(
            [
                ("linkedin_member", "personal"),
                ("facebook", "page"),
                ("threads", "personal"),
                ("x", "personal"),
            ]
            * 3
        )
    ][:10]
    from tests.integration.test_approvals_m6 import _decide

    for n, account in enumerate(ids):
        r = await api.post(
            _sets(ws2),
            headers=_h(s2),
            json={
                "topic": f"Story {n}: what changed in release {n}.0 for read replicas",
                "targets": {"social_account_ids": [account]},
            },
        )
        assert r.status_code == 201, r.text
        await _run_queued(api, deferrer)
        req2 = await _submit(api, s2, ws2, r.json()["id"])
        assert (await _decide(api, s2, ws2, req2, "approve", None)).status_code == 200
    jobs = await pub.enqueue_due(datetime.now(UTC) + timedelta(days=1))
    mine = [c for c in pub._deferrer.calls if c["workspace_id"] == ws2]  # type: ignore[union-attr]
    assert jobs >= 10
    assert len(mine) == 10
    assert len({c["lock"] for c in mine}) == 10
