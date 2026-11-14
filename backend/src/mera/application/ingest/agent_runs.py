"""``agent_run`` / ``agent_step`` recording with cost (M4.11, docs/03 §9, docs/06 §8).

Every provider call's ``Usage`` is written as a step before its result is used; the run's
totals are incremented in the same statement, so "cost rolls up, it is not recomputed".
LLM calls are made *outside* any database transaction (a pooled connection must never wait
on a model), and each step is then written in its own short workspace transaction.

``input_redacted`` holds a short description of the input (item ids, sizes), never the
article text or the prompt: the full prompt belongs in the object store, which arrives
with media in M5 (``prompt_object_key`` stays NULL until then).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.domain.context import WorkspaceContext
from mera.domain.ports.llm import Usage
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.db.models import AgentRun, AgentStep
from mera.infrastructure.db.session import workspace_transaction


@dataclass(slots=True)
class RunHandle:
    workspace_id: uuid.UUID
    run_id: uuid.UUID
    next_seq: int = 1


async def start_run(
    sm: async_sessionmaker[AsyncSession],
    workspace_id: uuid.UUID,
    *,
    workflow_key: str,
    trigger: str,
    subject_type: str | None = None,
    subject_id: uuid.UUID | None = None,
) -> RunHandle:
    run_id = uuid7()
    async with workspace_transaction(sm, WorkspaceContext(workspace_id)) as s:
        s.add(
            AgentRun(
                id=run_id,
                workspace_id=workspace_id,
                workflow_key=workflow_key,
                trigger=trigger,
                subject_type=subject_type,
                subject_id=subject_id,
                state="running",
            )
        )
    return RunHandle(workspace_id, run_id)


async def record_step(
    s: AsyncSession,
    run: RunHandle,
    *,
    agent_key: str,
    step_type: str,
    usage: Usage | None,
    state: str,
    input_redacted: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    error: str | None = None,
    subject_type: str | None = None,
    subject_id: uuid.UUID | None = None,
    post_variant_id: uuid.UUID | None = None,
) -> None:
    seq = run.next_seq
    run.next_seq += 1
    s.add(
        AgentStep(
            id=uuid7(),
            workspace_id=run.workspace_id,
            agent_run_id=run.run_id,
            seq=seq,
            agent_key=agent_key,
            step_type=step_type,
            subject_type=subject_type,
            subject_id=subject_id,
            post_variant_id=post_variant_id,
            input_redacted=input_redacted or {},
            output=output or {},
            model=usage.model if usage else None,
            fallback_from=usage.fallback_from if usage else None,
            price_source=usage.price_source if usage else None,
            tokens_in=usage.tokens_in if usage else 0,
            tokens_out=usage.tokens_out if usage else 0,
            cost_usd=usage.cost_usd if usage else Decimal(0),
            latency_ms=usage.latency_ms if usage else 0,
            state=state,
            error=error,
        )
    )
    if usage is not None:
        await s.execute(
            update(AgentRun)
            .where(AgentRun.id == run.run_id)
            .values(
                tokens_in=AgentRun.tokens_in + usage.tokens_in,
                tokens_out=AgentRun.tokens_out + usage.tokens_out,
                cost_usd=AgentRun.cost_usd + usage.cost_usd,
            )
        )


async def finish_run(
    sm: async_sessionmaker[AsyncSession], run: RunHandle, *, state: str, error: str | None = None
) -> None:
    async with workspace_transaction(sm, WorkspaceContext(run.workspace_id)) as s:
        await s.execute(
            update(AgentRun)
            .where(AgentRun.id == run.run_id)
            .values(state=state, error=error, finished_at=func.now())
        )


async def spent_this_month(s: AsyncSession, *, now: datetime | None = None) -> Decimal:
    """This UTC calendar month's spend across every agent in the session's workspace."""
    start = utc_month_start(now or datetime.now(UTC))
    total = await s.scalar(
        select(func.coalesce(func.sum(AgentStep.cost_usd), 0)).where(AgentStep.started_at >= start)
    )
    return Decimal(total or 0)


def utc_month_start(now: datetime) -> datetime:
    return utc_day_start(now).replace(day=1)


def utc_day_start(now: datetime) -> datetime:
    now = now.astimezone(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def spent_today(
    s: AsyncSession,
    *,
    agent_key: str | None = None,
    agent_keys: tuple[str, ...] = (),
    now: datetime | None = None,
) -> Decimal:
    """Today's spend (UTC day) for one agent, or a family of agents sharing one cap, in the
    session's workspace."""
    keys = (agent_key,) if agent_key else agent_keys
    if not keys:
        raise ValueError("spent_today needs at least one agent key")
    start = utc_day_start(now or datetime.now(UTC))
    total = await s.scalar(
        select(func.coalesce(func.sum(AgentStep.cost_usd), 0)).where(
            AgentStep.agent_key.in_(keys),
            AgentStep.started_at >= start,
            AgentStep.started_at < start + timedelta(days=1),
        )
    )
    return Decimal(total or 0)


async def is_aborted(sm: async_sessionmaker[AsyncSession], run: RunHandle) -> bool:
    """Whether a person stopped this run (P2-M1.7). Checked between units of work."""
    async with workspace_transaction(sm, WorkspaceContext(run.workspace_id)) as s:
        state = await s.scalar(select(AgentRun.state).where(AgentRun.id == run.run_id))
    return state == "aborted"
