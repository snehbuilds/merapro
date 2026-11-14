"""The agent run viewer and cost roll-ups (M5.16 reporting; docs/04 §4.13, docs/06 §8-9).

Model choice per agent is editable per workspace (Sprint 8, ``agents.models``); retry
and abort are M9 polish. Costs roll up from ``agent_step`` (never recomputed);
per-account attribution comes from ``agent_step.post_variant_id``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from mera.application.agents import models as agent_models
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import ConflictError, InvalidInput, NotFoundError
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.config import Settings
from mera.domain.errors import PermissionDenied
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.domain.ports.llm import ModelConfig
from mera.domain.principal import Principal
from mera.infrastructure.db.models import AgentRun, AgentStep, PostVariant, SocialAccount, Workspace
from mera.infrastructure.db.repositories.audit import record


@dataclass(frozen=True, slots=True)
class StepView:
    id: uuid.UUID
    seq: int
    agent_key: str
    step_type: str
    state: str
    model: str | None
    fallback_from: str | None
    price_source: str | None
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    latency_ms: int
    post_variant_id: uuid.UUID | None
    input_redacted: dict[str, Any]
    output: dict[str, Any]
    error: str | None
    started_at: datetime


@dataclass(frozen=True, slots=True)
class RunView:
    id: uuid.UUID
    workflow_key: str
    trigger: str
    subject_type: str | None
    subject_id: uuid.UUID | None
    state: str
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    step_count: int
    steps: list[StepView]
    # P2-M7.4: an orchestration run's graph, each child with its own steps and cost.
    parent_run_id: uuid.UUID | None = None
    children: list[RunView] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CostRow:
    key: str
    label: str
    cost_usd: Decimal
    tokens_in: int
    tokens_out: int
    calls: int


@dataclass(frozen=True, slots=True)
class CostReport:
    since: datetime
    until: datetime
    total_usd: Decimal
    by_agent: list[CostRow]
    by_model: list[CostRow]
    by_workflow: list[CostRow]
    by_account: list[CostRow]


@dataclass(frozen=True, slots=True)
class AgentDefinitionView:
    agent_key: str
    name: str
    model: str
    fallback_models: list[str]
    temperature: float
    cost_30d_usd: Decimal
    editable: bool = False
    default_model: str | None = None
    choices: list[str] = field(default_factory=list)


class AgentRunService(WorkspaceScopedService):
    def __init__(self, sm: Any, settings: Settings) -> None:
        super().__init__(sm)
        self._settings = settings

    async def list_runs(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        workflow_key: str | None,
        state: str | None,
        subject_id: uuid.UUID | None,
        limit: int,
    ) -> list[RunView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            _unscoped(principal)
            q = select(AgentRun).order_by(AgentRun.started_at.desc()).limit(limit)
            if workflow_key:
                q = q.where(AgentRun.workflow_key == workflow_key)
            if state:
                q = q.where(AgentRun.state == state)
            if subject_id:
                q = q.where(AgentRun.subject_id == subject_id)
            runs = list((await s.execute(q)).scalars())
            counts = dict(
                (
                    await s.execute(
                        select(AgentStep.agent_run_id, func.count())
                        .where(AgentStep.agent_run_id.in_([r.id for r in runs]))
                        .group_by(AgentStep.agent_run_id)
                    )
                ).all()
            )
            return [_run(r, int(counts.get(r.id, 0)), []) for r in runs]

    async def get_run(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, run_id: uuid.UUID, meta: RequestMeta
    ) -> RunView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            _unscoped(principal)
            r = await s.get(AgentRun, run_id)
            if r is None:
                raise NotFoundError("Agent run not found.")
            steps = list(
                (
                    await s.execute(
                        select(AgentStep)
                        .where(AgentStep.agent_run_id == run_id)
                        .order_by(AgentStep.seq)
                    )
                ).scalars()
            )
            kids = list(
                (
                    await s.execute(
                        select(AgentRun)
                        .where(AgentRun.parent_run_id == run_id)
                        .order_by(AgentRun.started_at)
                    )
                ).scalars()
            )
            children = []
            for k in kids:
                ks = list(
                    (
                        await s.execute(
                            select(AgentStep)
                            .where(AgentStep.agent_run_id == k.id)
                            .order_by(AgentStep.seq)
                        )
                    ).scalars()
                )
                children.append(_run(k, len(ks), [_step(x) for x in ks]))
            return _run(r, len(steps), [_step(x) for x in steps], children)

    async def abort(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, run_id: uuid.UUID, meta: RequestMeta
    ) -> RunView:
        """Stop a queued or running run (P2-M1.7). Cooperative: the worker finishes the
        account it is on, then stops, so no half-written version is stored."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.EDIT_POSTS, workspace_id)
            _unscoped(principal)
            r = await s.get(AgentRun, run_id, with_for_update=True)
            if r is None:
                raise NotFoundError("Agent run not found.")
            if r.state not in ("queued", "running"):
                raise ConflictError(
                    f"This run has already {r.state.replace('_', ' ')}.", code="run_not_active"
                )
            r.state = "aborted"
            r.error = f"Stopped by {user.display_name}."
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "agent_run.aborted",
                    workspace_id=workspace_id,
                    subject_type="agent_run",
                    subject_id=r.id,
                ),
            )
            await s.flush()
            return _run(r, 0, [])

    async def retry_target(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, run_id: uuid.UUID, meta: RequestMeta
    ) -> uuid.UUID:
        """The post set a failed or stopped drafting run was for; the caller queues a new
        draft for it. Only drafting runs can be retried: a revision's instruction belongs to
        the person who gave it and is asked again rather than replayed."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.EDIT_POSTS, workspace_id)
            _unscoped(principal)
            r = await s.get(AgentRun, run_id)
            if r is None:
                raise NotFoundError("Agent run not found.")
            if r.state not in ("failed", "aborted"):
                raise ConflictError(
                    "Only a failed or stopped run can be retried.", code="run_not_retryable"
                )
            if r.workflow_key != "draft_from_content" or r.subject_id is None:
                raise ConflictError(
                    "Only drafting runs can be retried; ask for a revision again instead.",
                    code="run_not_retryable",
                )
            return r.subject_id

    async def costs(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        since: datetime | None,
        until: datetime | None,
        account_id: uuid.UUID | None,
    ) -> CostReport:
        until = until or datetime.now(UTC)
        since = since or until - timedelta(days=30)
        if until - since > timedelta(days=366):
            raise InvalidInput(
                "Ask for at most a year at a time.", code="range_too_long", field="from"
            )
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            _unscoped(principal)
            base = [AgentStep.started_at >= since, AgentStep.started_at < until]
            if account_id:
                base.append(
                    AgentStep.post_variant_id.in_(
                        select(PostVariant.id).where(PostVariant.social_account_id == account_id)
                    )
                )

            async def group(col: Any, label_of: dict[Any, str] | None = None) -> list[CostRow]:
                rows = (
                    await s.execute(
                        select(
                            col,
                            func.coalesce(func.sum(AgentStep.cost_usd), 0),
                            func.coalesce(func.sum(AgentStep.tokens_in), 0),
                            func.coalesce(func.sum(AgentStep.tokens_out), 0),
                            func.count(),
                        )
                        .select_from(AgentStep)
                        .where(*base)
                        .group_by(col)
                    )
                ).all()
                out = [
                    CostRow(
                        str(k) if k is not None else "unattributed",
                        (label_of or {}).get(k, str(k) if k is not None else "Unattributed"),
                        Decimal(c),
                        int(ti),
                        int(to),
                        int(n),
                    )
                    for k, c, ti, to, n in rows
                ]
                return sorted(out, key=lambda r: r.cost_usd, reverse=True)

            by_agent = await group(AgentStep.agent_key)
            by_model = await group(AgentStep.model)
            workflow_col = (
                select(AgentRun.workflow_key)
                .where(AgentRun.id == AgentStep.agent_run_id)
                .scalar_subquery()
            )
            by_workflow = await group(workflow_col)
            account_col = (
                select(PostVariant.social_account_id)
                .where(PostVariant.id == AgentStep.post_variant_id)
                .scalar_subquery()
            )
            names = dict((await s.execute(select(SocialAccount.id, SocialAccount.nickname))).all())
            by_account = await group(account_col, names)
            return CostReport(
                since=since,
                until=until,
                total_usd=sum((r.cost_usd for r in by_agent), Decimal(0)),
                by_agent=by_agent,
                by_model=by_model,
                by_workflow=by_workflow,
                by_account=by_account,
            )

    async def definitions(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> list[AgentDefinitionView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            restricted = principal.member_scope.restricted
            ws = await s.get(Workspace, workspace_id)
            ws_settings = ws.settings if ws else None
        # Models and temperatures are not sensitive; spend spans every account, so a
        # group-scoped member sees the configuration without the costs.
        cost: dict[str, Decimal] = {}
        if not restricted:
            report = await self.costs(
                user, workspace_id, meta, since=None, until=None, account_id=None
            )
            cost = {r.key: r.cost_usd for r in report.by_agent}
        out = []
        for key, name, base in self._bases():
            eff = agent_models.override(base, ws_settings, key)
            out.append(
                AgentDefinitionView(
                    key,
                    name,
                    eff.model,
                    list(eff.fallbacks),
                    eff.temperature,
                    cost.get(key, Decimal(0)),
                    editable=True,
                    default_model=base.model,
                    choices=list(agent_models.CHAT_MODELS),
                )
            )
        return out

    def _bases(self) -> list[tuple[str, str, ModelConfig]]:
        st = self._settings
        return [
            (
                "research",
                "Research",
                ModelConfig(st.research_model, tuple(st.research_fallbacks), 0.2),
            ),
            ("writer", "Writer", ModelConfig(st.writer_model, tuple(st.writer_fallbacks), 0.8)),
            (
                "editor",
                "Editor / Compliance",
                ModelConfig(st.editor_model, tuple(st.editor_fallbacks), 0.1),
            ),
            ("hashtag", "Hashtag", ModelConfig(st.hashtag_model, tuple(st.hashtag_fallbacks), 0.2)),
        ]

    async def set_model(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        agent_key: str,
        model: str | None,
        fallbacks: list[str] | None,
    ) -> AgentDefinitionView:
        """Choose an agent's model for this workspace; ``model=None`` returns to the default.
        Admins only: the model sets what every draft costs."""
        if agent_key not in agent_models.EDITABLE_AGENTS:
            raise NotFoundError("No such agent.")
        for m in [*([model] if model else []), *(fallbacks or [])]:
            if not agent_models.is_valid_model(m):
                raise InvalidInput(
                    f"{m} is not an available model.", code="unknown_model", field="model"
                )
        if fallbacks is not None and len(fallbacks) > agent_models.MAX_FALLBACKS:
            raise InvalidInput(
                "At most three fallbacks.", code="too_many_fallbacks", field="fallbacks"
            )
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.MANAGE_WORKSPACE, workspace_id)
            ws = await s.get(Workspace, workspace_id, with_for_update=True)
            if ws is None:
                raise NotFoundError("Workspace not found.")
            self._ensure_writable(ws)
            settings = dict(ws.settings or {})
            chosen = dict(settings.get("agent_models") or {})
            before = chosen.get(agent_key)
            if model is None:
                chosen.pop(agent_key, None)
            else:
                chosen[agent_key] = {"model": model, "fallbacks": list(fallbacks or [])}
            settings["agent_models"] = chosen
            ws.settings = settings
            await s.flush()
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "agent_definition.updated",
                    workspace_id=workspace_id,
                    subject_type="agent",
                    before={"agent": agent_key, "config": before},
                    after={"agent": agent_key, "config": chosen.get(agent_key)},
                ),
            )
        rows = await self.definitions(user, workspace_id, meta)
        return next(r for r in rows if r.agent_key == agent_key)


def _unscoped(principal: Principal) -> None:
    """Runs and costs span every account in the workspace; a member restricted to some
    groups would see the others' accounts, drafts' angles and spend."""
    if principal.member_scope.restricted:
        raise PermissionDenied("agent runs are workspace-wide", permission=Permission.READ.value)


def _step(x: AgentStep) -> StepView:
    return StepView(
        id=x.id,
        seq=x.seq,
        agent_key=x.agent_key,
        step_type=x.step_type,
        state=x.state,
        model=x.model,
        fallback_from=x.fallback_from,
        price_source=x.price_source,
        tokens_in=x.tokens_in,
        tokens_out=x.tokens_out,
        cost_usd=x.cost_usd,
        latency_ms=x.latency_ms,
        post_variant_id=x.post_variant_id,
        input_redacted=dict(x.input_redacted),
        output=dict(x.output),
        error=x.error,
        started_at=x.started_at,
    )


def _run(
    r: AgentRun, count: int, steps: list[StepView], children: list[RunView] | None = None
) -> RunView:
    return RunView(
        id=r.id,
        workflow_key=r.workflow_key,
        trigger=r.trigger,
        subject_type=r.subject_type,
        subject_id=r.subject_id,
        state=r.state,
        tokens_in=r.tokens_in,
        tokens_out=r.tokens_out,
        cost_usd=r.cost_usd,
        error=r.error,
        started_at=r.started_at,
        finished_at=r.finished_at,
        step_count=count,
        steps=steps,
        parent_run_id=r.parent_run_id,
        children=children or [],
    )
