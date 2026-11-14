"""P2-M7 orchestration against real Postgres: one resumable run drafts every account,
asks for the image Instagram needs, and records the Scheduler agent's proposals; the run
detail shows its children with their own steps and costs."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from mera.application.services.orchestration_service import OrchestrationService
from mera.domain.context import WorkspaceContext
from mera.infrastructure.db.models import AgentRun, MediaJob
from mera.infrastructure.db.session import workspace_transaction
from mera.infrastructure.media.generators import FakeMediaGenerator
from tests.integration.test_accounts_m3 import _app, _connect, _setup
from tests.integration.test_drafting_m5 import CapturingDeferrer, _group, _sets, deferrer  # noqa: F401
from tests.integration.test_identity_m2 import _h
from tests.integration.test_ingest_m4 import _sm

pytestmark = pytest.mark.integration


async def _set_with_instagram(api: httpx.AsyncClient, s: dict[str, Any], ws: str) -> str:
    acc = [
        (await _connect(api, s, ws, p, handle=f"{p[:3]}_{uuid.uuid4().hex[:6]}", kind=k))[
            "connected"
        ]
        for p, k in (("linkedin_member", "personal"), ("instagram", "business"))
    ]
    grp = await _group(api, s, ws, "Launch", acc)
    r = await api.post(
        _sets(ws),
        headers=_h(s),
        json={
            "topic": "What we learned shipping a Postgres upgrade",
            "targets": {"account_group_ids": [grp]},
        },
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def _orch(api: httpx.AsyncClient, sink: CapturingDeferrer) -> OrchestrationService:
    app = _app(api)
    media = app.state.media_generation_service
    media._deferrer = sink
    media._gen = FakeMediaGenerator()
    return OrchestrationService(_sm(api), app.state.drafting, sink, media)


async def _runs(api: httpx.AsyncClient, ws: str, **where: Any) -> list[AgentRun]:
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        q = select(AgentRun)
        for k, v in where.items():
            q = q.where(getattr(AgentRun, k) == v)
        return list((await tx.execute(q.order_by(AgentRun.started_at))).scalars())


async def test_orchestration_runs_every_stage_and_shows_the_graph(
    api: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
) -> None:
    s, ws = await _setup(api, "orch")
    set_id = await _set_with_instagram(api, s, ws)
    sink = CapturingDeferrer()
    app = _app(api)
    saved = app.state.orchestration_service
    app.state.orchestration_service = _orch(api, sink)
    try:
        r = await api.post(_sets(ws, set_id, "/orchestrate"), headers=_h(s))
        assert r.status_code == 202, r.text
        run_id = r.json()["run_id"]
        call = next(c for c in sink.calls if c["task"] == "drafting:orchestrate")
        # A second start while one runs is refused.
        again = await api.post(_sets(ws, set_id, "/orchestrate"), headers=_h(s))
        assert again.status_code == 409
        orch = app.state.orchestration_service
        out = await orch.run(uuid.UUID(ws), uuid.UUID(run_id), uuid.UUID(call["user_id"]))
        assert out == "succeeded"
    finally:
        app.state.orchestration_service = saved
    detail = (await api.get(f"/api/v1/workspaces/{ws}/agent-runs/{run_id}", headers=_h(s))).json()
    stages = [(st["output"]["stage"], st["state"]) for st in detail["steps"]]
    assert stages == [("draft", "succeeded"), ("media", "succeeded"), ("schedule", "succeeded")]
    media = detail["steps"][1]["output"]
    assert media["requested"] == 1  # only Instagram needs an image
    proposals = detail["steps"][2]["output"]["proposals"]
    assert len(proposals) == 2
    assert all(v is None for v in proposals.values())  # no history yet: no proposal
    # The graph: the drafting and media runs are children, each with steps and cost.
    kinds = {c["workflow_key"] for c in detail["children"]}
    assert "drafting" in kinds or any(k.startswith("draft") for k in kinds), kinds
    assert all("steps" in c for c in detail["children"])
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        jobs = list((await tx.execute(select(MediaJob))).scalars())
    assert len(jobs) == 1
    assert jobs[0].post_variant_id is not None


async def test_a_failed_stage_resumes_without_redoing_the_ones_before(
    api: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
) -> None:
    s, ws = await _setup(api, "orchresume")
    set_id = await _set_with_instagram(api, s, ws)
    sink = CapturingDeferrer()
    orch = _orch(api, sink)

    class Broken:
        async def generate(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("media service down")

    app = _app(api)
    saved = app.state.orchestration_service
    app.state.orchestration_service = orch
    try:
        run_id = (await api.post(_sets(ws, set_id, "/orchestrate"), headers=_h(s))).json()["run_id"]
        user_id = next(c for c in sink.calls if c["task"] == "drafting:orchestrate")["user_id"]
        real = orch._media_gen
        orch._media_gen = Broken()
        # The media stage notes per-account failures without failing the run…
        out = await orch.run(uuid.UUID(ws), uuid.UUID(run_id), uuid.UUID(user_id))
        assert out == "succeeded"
        detail = (
            await api.get(f"/api/v1/workspaces/{ws}/agent-runs/{run_id}", headers=_h(s))
        ).json()
        assert "media service down" in str(detail["steps"][1]["output"]["notes"])

        # …but a stage that raises fails the run, and resume picks up there.
        run2 = (await api.post(_sets(ws, set_id, "/orchestrate"), headers=_h(s))).json()["run_id"]

        async def boom(*a: Any, **k: Any) -> dict[str, Any]:
            raise RuntimeError("scheduler down")

        orch._schedule = boom  # type: ignore[method-assign]
        assert await orch.run(uuid.UUID(ws), uuid.UUID(run2), uuid.UUID(user_id)) == "failed"
        drafts_before = len(await _runs(api, ws, workflow_key="drafting"))
        r = await api.post(f"/api/v1/workspaces/{ws}/agent-runs/{run2}/resume", headers=_h(s))
        assert r.status_code == 202, r.text
        del orch._schedule  # back to the real stage
        orch._media_gen = real
        assert await orch.run(uuid.UUID(ws), uuid.UUID(run2), uuid.UUID(user_id)) == "succeeded"
        assert len(await _runs(api, ws, workflow_key="drafting")) == drafts_before
        detail = (await api.get(f"/api/v1/workspaces/{ws}/agent-runs/{run2}", headers=_h(s))).json()
        stages = [(st["output"]["stage"], st["state"]) for st in detail["steps"]]
        assert stages == [
            ("draft", "succeeded"),
            ("media", "succeeded"),
            ("schedule", "failed"),
            ("schedule", "succeeded"),
        ]
        # Only failed or stopped runs resume.
        r = await api.post(f"/api/v1/workspaces/{ws}/agent-runs/{run2}/resume", headers=_h(s))
        assert r.status_code == 409
    finally:
        app.state.orchestration_service = saved
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        n = await tx.scalar(
            select(func.count())
            .select_from(AgentRun)
            .where(AgentRun.workflow_key == "orchestration")
        )
    assert n == 2


async def test_slot_proposals_endpoint_without_history(api: httpx.AsyncClient) -> None:
    s, ws = await _setup(api, "orchslots")
    acc = (await _connect(api, s, ws, "linkedin_member", handle=f"li_{uuid.uuid4().hex[:6]}"))[
        "connected"
    ]
    r = await api.get(
        f"/api/v1/workspaces/{ws}/social-accounts/{acc}/slot-proposals", headers=_h(s)
    )
    assert r.status_code == 200, r.text
    assert r.json()["proposals"] == []
    assert r.json()["scores"] == []
