"""``Agent[TIn, TOut]`` (M5.1; docs/06 §1, §8).

An agent is a typed contract (input dataclass -> Pydantic output), a prompt builder and a
model configuration. ``run`` is the only way to call one, and it does the same three things
every time:

1. Checks the agent family's daily cost cap for the workspace *before* spending.
2. Calls the provider outside any database transaction.
3. Records the step with its ``Usage`` and its ``post_variant_id`` (per-account cost
   attribution, docs/03 §9) before the result is used, on failure as well as success.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.agents import models
from mera.application.ingest import agent_runs
from mera.domain.context import WorkspaceContext
from mera.domain.ports.llm import LLMError, LLMProvider, Message, ModelConfig
from mera.infrastructure.db.models import Workspace
from mera.infrastructure.db.session import workspace_transaction

TIn = TypeVar("TIn")
TOut = TypeVar("TOut", bound=BaseModel)

# Writer, Editor and Hashtag share one daily cap: they are one drafting pipeline.
DRAFTING_AGENTS: tuple[str, ...] = ("writer", "editor", "hashtag")


class CostCapReached(Exception):
    def __init__(self, spent: Decimal, cap: Decimal, period: str = "daily") -> None:
        what = "daily drafting" if period == "daily" else "monthly workspace"
        super().__init__(f"{what} cost cap reached (${spent:.2f} of ${cap:.2f})")
        self.spent = spent
        self.cap = cap
        self.period = period


@dataclass(slots=True)
class AgentContext:
    sm: async_sessionmaker[AsyncSession]
    run: agent_runs.RunHandle
    daily_cap_usd: Decimal
    cap_family: tuple[str, ...] = DRAFTING_AGENTS
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    # Every agent together, per calendar month (M5.16); None = no monthly cap.
    monthly_cap_usd: Decimal | None = None

    @property
    def workspace_id(self) -> uuid.UUID:
        return self.run.workspace_id


class Agent(ABC, Generic[TIn, TOut]):
    key: str
    output: type[TOut]

    def __init__(self, llm: LLMProvider, config: ModelConfig) -> None:
        self._llm = llm
        self.config = config

    @abstractmethod
    def messages(self, inp: TIn) -> list[Message]: ...

    def redact(self, inp: TIn) -> dict[str, Any]:
        """A short, PII-free description of the input for the run viewer."""
        return {}

    def summarize(self, out: TOut) -> dict[str, Any]:
        return {}

    async def run(
        self, ctx: AgentContext, inp: TIn, *, post_variant_id: uuid.UUID | None = None
    ) -> TOut:
        async with workspace_transaction(ctx.sm, WorkspaceContext(ctx.workspace_id)) as s:
            spent = await agent_runs.spent_today(s, agent_keys=ctx.cap_family, now=ctx.clock())
            month = (
                await agent_runs.spent_this_month(s, now=ctx.clock())
                if ctx.monthly_cap_usd is not None
                else Decimal(0)
            )
            ws = await s.get(Workspace, ctx.workspace_id)
            # The workspace's model choice for this agent, read at call time.
            config = models.override(self.config, ws.settings if ws else None, self.key)
            llm_cfg = ((ws.settings or {}).get("llm_config") or {}) if ws else {}
        if spent >= ctx.daily_cap_usd:
            raise CostCapReached(spent, ctx.daily_cap_usd)
        if ctx.monthly_cap_usd is not None and month >= ctx.monthly_cap_usd:
            raise CostCapReached(month, ctx.monthly_cap_usd, "monthly")
        redacted = self.redact(inp)
        try:
            result = await self._llm.structured(
                config, self.messages(inp), self.output, llm_config=llm_cfg
            )
        except LLMError as exc:
            async with workspace_transaction(ctx.sm, WorkspaceContext(ctx.workspace_id)) as s:
                await agent_runs.record_step(
                    s,
                    ctx.run,
                    agent_key=self.key,
                    step_type="llm_call",
                    usage=exc.usage,
                    state="failed",
                    error=exc.kind,  # provider text can echo prompts or key fragments
                    input_redacted=redacted,
                    post_variant_id=post_variant_id,
                    subject_type="post_variant" if post_variant_id else None,
                    subject_id=post_variant_id,
                )
            raise
        async with workspace_transaction(ctx.sm, WorkspaceContext(ctx.workspace_id)) as s:
            await agent_runs.record_step(
                s,
                ctx.run,
                agent_key=self.key,
                step_type="llm_call",
                usage=result.usage,
                state="succeeded",
                input_redacted=redacted,
                output=self.summarize(result.value),
                post_variant_id=post_variant_id,
                subject_type="post_variant" if post_variant_id else None,
                subject_id=post_variant_id,
            )
        return result.value
