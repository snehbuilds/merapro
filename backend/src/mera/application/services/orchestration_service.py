"""Full orchestration (P2-M7.3): one run from a post set to approval-ready posts.

The stages, in order, each recorded as a ``handoff`` step (``output.stage``) on one parent run:

1. **draft**: Writer, Hashtag and Editor per account (the drafting pipeline; its own run
   becomes a child of this one). Research happened when the source was ingested.
2. **media**: the Media agent and a generation job for each account whose platform
   cannot post without an image and has none; budget and ceiling still apply, and a
   refusal is noted, not fatal.
3. **schedule**: the Scheduler agent's proposal per account, with its reason. Nothing is
   scheduled here: scheduling happens on approval, through ``place``.

**Resumable.** A stage that succeeded is never run again. Resuming a failed or stopped run
starts at the first stage without a success, so a crash after drafting does not pay for
drafting twice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Final

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application import scheduling
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.drafting.pipeline import Drafting
from mera.application.errors import ConflictError, NotFoundError
from mera.application.ingest import agent_runs
from mera.application.ingest.agent_runs import RunHandle
from mera.application.publishing.scheduler import effective_rules
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.domain.context import WorkspaceContext
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.domain.value_objects.platform import Platform
from mera.infrastructure.db.models import (
    AgentRun,
    AgentStep,
    PostSet,
    PostVariant,
    PostVersion,
    User,
)
from mera.infrastructure.db.session import workspace_transaction
from mera.infrastructure.platforms.capabilities import capabilities_for
from mera.infrastructure.queue.deferrer import Deferrer

log = structlog.get_logger(__name__)

TASK: Final = "drafting:orchestrate"
WORKFLOW: Final = "orchestration"
STAGES: Final = ("draft", "media", "schedule")


class OrchestrationService(WorkspaceScopedService):
    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        drafting: Drafting,
        deferrer: Deferrer | None,
        media: Any | None = None,
    ) -> None:
        super().__init__(sm)
        self._drafting = drafting
        self._deferrer = deferrer
        self._media_gen = media  # MediaGenerationService

    async def start(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta, set_id: uuid.UUID
    ) -> uuid.UUID:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.EDIT_POSTS, workspace_id)
            if await s.get(PostSet, set_id) is None:
                raise NotFoundError("Post set not found.")
            running = await s.scalar(
                select(AgentRun.id).where(
                    AgentRun.workflow_key == WORKFLOW,
                    AgentRun.subject_id == set_id,
                    AgentRun.state == "running",
                )
            )
            if running is not None:
                raise ConflictError("This post set is already being worked on.", code="run_active")
        run = await agent_runs.start_run(
            self._sm,
            workspace_id,
            workflow_key=WORKFLOW,
            trigger="manual",
            subject_type="post_set",
            subject_id=set_id,
        )
        await self._enqueue(workspace_id, run.run_id, user.user_id)
        return run.run_id

    async def resume(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta, run_id: uuid.UUID
    ) -> uuid.UUID:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.EDIT_POSTS, workspace_id)
            run = await s.get(AgentRun, run_id, with_for_update=True)
            if run is None or run.workflow_key != WORKFLOW:
                raise NotFoundError("Orchestration run not found.")
            if run.state not in ("failed", "aborted"):
                raise ConflictError(
                    "Only a failed or stopped run can be resumed.", code="not_resumable"
                )
            run.state, run.error, run.finished_at = "running", None, None
        await self._enqueue(workspace_id, run_id, user.user_id)
        return run_id

    async def slot_proposals(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        account_id: uuid.UUID,
    ) -> dict[str, Any]:
        from mera.application.publishing.scheduler import resolved_posting_times  # noqa: F401
        from mera.domain.policies.slot_scores import propose
        from mera.infrastructure.db.models import SocialAccount, Workspace

        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id, social_account_id=account_id)
            account = await s.get(SocialAccount, account_id)
            if account is None:
                raise NotFoundError("Account not found.")
            ws = await s.get(Workspace, workspace_id)
            tz = getattr(account, "timezone", None) or (ws.timezone if ws else "UTC")
            scores = await scheduling.account_scores(s, account_id, tz)
        now = datetime.now(UTC)
        return {
            "proposals": [
                vars_of(p)
                for p in propose(
                    scores,
                    after=now,
                    tz_name=tz,
                    count=5,
                    min_samples=scheduling.MIN_SLOT_SAMPLES,
                )
            ],
            "scores": [vars_of(x) for x in scores[:24]],
            "time_zone": tz,
        }

    async def _enqueue(
        self, workspace_id: uuid.UUID, run_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        if self._deferrer is not None:
            await self._deferrer.defer(
                TASK,
                queue="agents",
                queueing_lock=f"orchestrate:{run_id}",
                workspace_id=str(workspace_id),
                run_id=str(run_id),
                user_id=str(user_id),
            )

    # ------------------------------------------------------------------ worker

    async def run(self, workspace_id: uuid.UUID, run_id: uuid.UUID, user_id: uuid.UUID) -> str:
        ctx = WorkspaceContext(workspace_id)
        async with workspace_transaction(self._sm, ctx) as s:
            run = await s.get(AgentRun, run_id)
            if run is None or run.state != "running":
                return run.state if run else "missing"
            set_id = run.subject_id
            started = run.started_at
            steps = list(
                (
                    await s.execute(
                        select(AgentStep)
                        .where(AgentStep.agent_run_id == run_id)
                        .order_by(AgentStep.seq)
                    )
                ).scalars()
            )
            u = await s.get(User, user_id)
        assert set_id is not None
        done = {
            str(st.output.get("stage"))
            for st in steps
            if st.step_type == "handoff" and st.state == "succeeded"
        }
        handle = RunHandle(
            workspace_id, run_id, next_seq=(max((st.seq for st in steps), default=0) + 1)
        )
        who = (
            AuthenticatedUser(user_id=u.id, email=u.email, display_name=u.display_name)
            if u
            else None
        )
        for stage in STAGES:
            if stage in done:
                continue
            if await agent_runs.is_aborted(self._sm, handle):
                return "aborted"
            try:
                out = await getattr(self, f"_{stage}")(workspace_id, set_id, run_id, started, who)
            except Exception as exc:  # recorded on the run; resume picks up here
                async with workspace_transaction(self._sm, ctx) as s:
                    await agent_runs.record_step(
                        s,
                        handle,
                        agent_key=stage,
                        step_type="handoff",
                        usage=None,
                        state="failed",
                        output={"stage": stage},
                        error=f"{type(exc).__name__}: {exc}"[:500],
                    )
                await agent_runs.finish_run(
                    self._sm, handle, state="failed", error=f"{stage} failed"
                )
                log.info("orchestration.stage_failed", stage=stage, error=type(exc).__name__)
                return "failed"
            async with workspace_transaction(self._sm, ctx) as s:
                await agent_runs.record_step(
                    s,
                    handle,
                    agent_key=stage,
                    step_type="handoff",
                    usage=None,
                    state="succeeded",
                    output={"stage": stage, **out},
                )
        await agent_runs.finish_run(self._sm, handle, state="succeeded")
        return "succeeded"

    async def _draft(
        self,
        workspace_id: uuid.UUID,
        set_id: uuid.UUID,
        run_id: uuid.UUID,
        started: datetime,
        who: AuthenticatedUser | None,
    ) -> dict[str, Any]:
        outcome = await self._drafting.draft(
            workspace_id, set_id, mode="create", user_id=who.user_id if who else None
        )
        await self._adopt(workspace_id, set_id, run_id, started)
        if outcome.capped:
            raise RuntimeError(f"the {outcome.cap_period} AI budget is spent")
        return {"drafted": outcome.drafted, "failed": list(outcome.failed)}

    async def _adopt(
        self, workspace_id: uuid.UUID, set_id: uuid.UUID, run_id: uuid.UUID, started: datetime
    ) -> None:
        """Runs the stages started for this set become children of this run (the graph)."""
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            await s.execute(
                update(AgentRun)
                .where(
                    AgentRun.id != run_id,
                    AgentRun.parent_run_id.is_(None),
                    AgentRun.workflow_key != WORKFLOW,
                    AgentRun.started_at >= started,
                    AgentRun.subject_id.in_(
                        select(PostVariant.id).where(PostVariant.post_set_id == set_id)
                    )
                    | (AgentRun.subject_id == set_id),
                )
                .values(parent_run_id=run_id)
            )

    async def _media(
        self,
        workspace_id: uuid.UUID,
        set_id: uuid.UUID,
        run_id: uuid.UUID,
        started: datetime,
        who: AuthenticatedUser | None,
    ) -> dict[str, Any]:
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            rows = (
                await s.execute(
                    select(PostVariant, PostVersion)
                    .join(PostVersion, PostVersion.id == PostVariant.current_version_id)
                    .where(PostVariant.post_set_id == set_id, PostVariant.enabled.is_(True))
                )
            ).all()
        needs = [
            v.id
            for v, ver in rows
            if capabilities_for(Platform(v.platform)).requires_media and not (ver.media or [])
        ]
        notes: dict[str, str] = {}
        if not needs:
            return {"requested": 0, "notes": notes}
        if self._media_gen is None or who is None:
            return {
                "requested": 0,
                "notes": {str(v): "media generation unavailable" for v in needs},
            }
        requested = 0
        for vid in needs:
            try:
                jobs = await self._media_gen.generate(
                    who,
                    workspace_id,
                    RequestMeta(surface="worker"),
                    post_variant_id=vid,
                    aspect="portrait",
                    attach=True,
                )
                requested += len(jobs)
            except Exception as exc:  # budget, ceiling, screening: noted, not fatal
                notes[str(vid)] = str(getattr(exc, "message", None) or exc)[:200]
        await self._adopt(workspace_id, set_id, run_id, started)
        return {"requested": requested, "notes": notes}

    async def _schedule(
        self,
        workspace_id: uuid.UUID,
        set_id: uuid.UUID,
        run_id: uuid.UUID,
        started: datetime,
        who: AuthenticatedUser | None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        proposals: dict[str, Any] = {}
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            variants = list(
                (
                    await s.execute(
                        select(PostVariant).where(
                            PostVariant.post_set_id == set_id, PostVariant.enabled.is_(True)
                        )
                    )
                ).scalars()
            )
            for v in variants:
                tz, _q, _c = await effective_rules(s, v)
                p = await scheduling.proposal_for(s, v.social_account_id, after=now, tz_name=tz)
                proposals[str(v.id)] = scheduling.as_placement(p) if p else None
        return {"proposals": proposals}


def vars_of(obj: Any) -> dict[str, Any]:
    return {k: getattr(obj, k) for k in obj.__slots__}
