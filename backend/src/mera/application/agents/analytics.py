"""The Analytics agent (P3-M1.4, docs/06, docs/02 metrics flow): narrates, never computes.

It is given the week's insights, each already written from an aggregate by rule
(``mera.domain.policies.insights``), and writes a short summary with a few
recommendations. Every sentence must cite insight ids from its input. ``grounded`` drops
any citation it was not given and any recommendation with none left, so the agent cannot
introduce a number or a claim that is not on the page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from mera.application.agents.base import Agent
from mera.application.agents.fence import fenced
from mera.domain.ports.llm import Message


@dataclass(frozen=True, slots=True)
class InsightLine:
    id: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class AnalyticsRequest:
    workspace: str
    period: str
    insights: tuple[InsightLine, ...]


class Recommendation(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    cites: list[str] = Field(default_factory=list, max_length=10)


class AnalyticsSummary(BaseModel):
    summary: str = Field(default="", max_length=1200)
    cites: list[str] = Field(default_factory=list, max_length=30)
    recommendations: list[Recommendation] = Field(default_factory=list, max_length=5)


SYSTEM = """\
You write the weekly analytics summary for the workspace {workspace}, {period}.

You are given the week's findings, each with an id, inside <untrusted_findings>. They were
computed from the workspace's own numbers. Your job is to summarise them for a busy
person and suggest what to try next.

RULES
- Use only the findings given. Do not add numbers, causes or facts that are not in them.
- Cite the ids of the findings each part rests on (cites, and cites per recommendation).
- At most three short recommendations. Say "keep going" if nothing needs to change.
- No findings: say there is not enough data yet, with no recommendations."""


class AnalyticsAgent(Agent[AnalyticsRequest, AnalyticsSummary]):
    key = "analytics"
    output = AnalyticsSummary

    def messages(self, inp: AnalyticsRequest) -> list[Message]:
        lines = "\n".join(f"[{i.id}] {i.title}. {i.body}" for i in inp.insights) or "(none)"
        return [
            Message("system", SYSTEM.format(workspace=inp.workspace, period=inp.period)),
            Message("user", fenced("untrusted_findings", lines, "untrusted_findings")),
        ]

    def redact(self, inp: AnalyticsRequest) -> dict[str, Any]:
        return {"insights": len(inp.insights), "period": inp.period}

    def summarize(self, out: AnalyticsSummary) -> dict[str, Any]:
        return {"cites": out.cites, "recommendations": len(out.recommendations)}


def grounded(out: AnalyticsSummary, ids: set[str]) -> AnalyticsSummary:
    recs = []
    for r in out.recommendations:
        cites = [c for c in r.cites if c in ids]
        if cites:
            recs.append(Recommendation(text=r.text, cites=cites))
    return AnalyticsSummary(
        summary=out.summary, cites=[c for c in out.cites if c in ids], recommendations=recs
    )
