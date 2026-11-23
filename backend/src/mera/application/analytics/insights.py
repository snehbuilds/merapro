"""The weekly insight run (P3-M1.3 to 1.5, docs/02 metrics flow, docs/14 §1.3-1.4).

Per workspace, over the last ``PERIOD``:

1. **Facts**: every published post with its latest engagement (likes, comments, shares),
   its account, group, platform, and the source and pillar of the content it came from.
2. **Aggregates and findings** by rule (``mera.domain.policies.insights``), stored as
   ``insight`` rows with the aggregate as evidence.
3. **Summary** by the Analytics agent, which may only cite those rows; when it is
   unavailable a plain list of titles stands in, so the week is never empty.
4. **Weights**: each source and pillar with enough posts moves toward its performance by
   at most 15%, recorded in ``weight_change``. A workspace turns this off with
   ``settings.analytics.auto_tune = false``.
5. **Email** to Owners and Admins when a notifier is configured.

A workspace runs at most once in ``MIN_INTERVAL`` unless forced.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.agents.analytics import (
    AnalyticsRequest,
    AnalyticsSummary,
    InsightLine,
    Recommendation,
    grounded,
)
from mera.application.agents.base import AgentContext
from mera.application.ingest import agent_runs
from mera.domain.context import WorkspaceContext
from mera.domain.policies.insights import (
    MIN_POSTS,
    Finding,
    Names,
    PostFact,
    aggregate,
    findings,
    next_weight,
)
from mera.domain.ports.notifier import EmailMessage, Notifier
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.db.models import (
    AccountGroup,
    ApprovalDecision,
    ApprovalRequest,
    ContentItem,
    ContentItemScore,
    ContentPillar,
    Insight,
    MetricSnapshot,
    PostSet,
    PublishedPost,
    SocialAccount,
    Source,
    User,
    WeightChange,
    Workspace,
    WorkspaceMember,
)
from mera.infrastructure.db.session import system_scan_transaction, workspace_transaction

log = structlog.get_logger(__name__)

PERIOD: Final = timedelta(days=28)
MIN_INTERVAL: Final = timedelta(days=6)
ENGAGEMENT: Final = ("likes", "comments", "shares")


def auto_tune(ws: Workspace | None) -> bool:
    return bool(((ws.settings if ws else None) or {}).get("analytics", {}).get("auto_tune", True))


class InsightRun:
    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        drafting: Any | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self._sm = sm
        self._drafting = drafting
        self._notifier = notifier

    async def run(self, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(UTC)
        async with system_scan_transaction(self._sm) as s:
            ws_ids = list(
                (
                    await s.execute(
                        select(Workspace.id).where(
                            Workspace.deleted_at.is_(None), Workspace.archived_at.is_(None)
                        )
                    )
                ).scalars()
            )
        counts: Counter[str] = Counter()
        for ws in ws_ids:
            try:
                counts.update(await self.refresh_workspace(ws, now))
            except Exception as exc:  # one workspace must not stop the others
                log.exception(
                    "insights.workspace_failed",
                    message=(
                        "Insight generation failed for a workspace; continuing the scheduled run."
                    ),
                    workspace_id=str(ws),
                    error_type=type(exc).__name__,
                )
                counts["error"] += 1
        log.info("insights.run", **counts)
        return dict(counts)

    # ------------------------------------------------------------------ facts

    async def _facts(
        self, s: AsyncSession, start: datetime, end: datetime
    ) -> tuple[list[PostFact], Names]:
        latest = (
            select(
                MetricSnapshot.published_post_id,
                MetricSnapshot.metric_key,
                func.max(MetricSnapshot.captured_at).label("at"),
            )
            .where(
                MetricSnapshot.subject_type == "post",
                MetricSnapshot.metric_key.in_(ENGAGEMENT),
            )
            .group_by(MetricSnapshot.published_post_id, MetricSnapshot.metric_key)
            .subquery()
        )
        rows = (
            await s.execute(
                select(
                    PublishedPost.id,
                    PublishedPost.social_account_id,
                    PublishedPost.account_group_id,
                    PublishedPost.platform,
                    PostSet.content_item_id,
                    MetricSnapshot.value,
                )
                .join(PostSet, PostSet.id == PublishedPost.post_set_id)
                .join(latest, latest.c.published_post_id == PublishedPost.id)
                .join(
                    MetricSnapshot,
                    (MetricSnapshot.published_post_id == latest.c.published_post_id)
                    & (MetricSnapshot.metric_key == latest.c.metric_key)
                    & (MetricSnapshot.captured_at == latest.c.at),
                )
                .where(
                    PublishedPost.deleted_at.is_(None),
                    PublishedPost.published_at >= start,
                    PublishedPost.published_at < end,
                )
            )
        ).all()
        posts: dict[uuid.UUID, dict[str, Any]] = {}
        for pid, acc, grp, platform, item, value in rows:
            p = posts.setdefault(
                pid,
                {"acc": acc, "grp": grp, "platform": platform, "item": item, "e": 0.0},
            )
            p["e"] += float(value or Decimal(0))
        items = {p["item"] for p in posts.values() if p["item"]}
        source_of: dict[uuid.UUID, uuid.UUID | None] = {}
        pillar_of: dict[tuple[uuid.UUID, uuid.UUID | None], uuid.UUID | None] = {}
        if items:
            source_of = dict(
                (
                    await s.execute(
                        select(ContentItem.id, ContentItem.source_id).where(
                            ContentItem.id.in_(items)
                        )
                    )
                ).all()
            )
            for item, grp, scores in (
                await s.execute(
                    select(
                        ContentItemScore.content_item_id,
                        ContentItemScore.account_group_id,
                        ContentItemScore.pillar_scores,
                    ).where(ContentItemScore.content_item_id.in_(items))
                )
            ).all():
                best = max((scores or {}).items(), key=lambda kv: kv[1], default=None)
                pillar = uuid.UUID(best[0]) if best and best[1] > 0 else None
                pillar_of[(item, grp)] = pillar
                pillar_of.setdefault((item, None), pillar)
        facts = [
            PostFact(
                account_id=p["acc"],
                platform=p["platform"],
                engagement=p["e"],
                group_id=p["grp"],
                source_id=source_of.get(p["item"]) if p["item"] else None,
                pillar_id=(
                    pillar_of.get((p["item"], p["grp"]), pillar_of.get((p["item"], None)))
                    if p["item"]
                    else None
                ),
            )
            for p in posts.values()
        ]
        names = Names(
            pillar={
                str(i): n
                for i, n in (await s.execute(select(ContentPillar.id, ContentPillar.name))).all()
            },
            source={str(i): n for i, n in (await s.execute(select(Source.id, Source.name))).all()},
            account={
                str(i): n
                for i, n in (
                    await s.execute(select(SocialAccount.id, SocialAccount.nickname))
                ).all()
            },
            group={
                str(i): n
                for i, n in (await s.execute(select(AccountGroup.id, AccountGroup.name))).all()
            },
        )
        return facts, names

    async def _approval_minutes(
        self, s: AsyncSession, start: datetime, end: datetime
    ) -> dict[str, list[float]]:
        first = (
            select(
                ApprovalDecision.approval_request_id,
                func.min(ApprovalDecision.decided_at).label("at"),
            )
            .group_by(ApprovalDecision.approval_request_id)
            .subquery()
        )
        rows = (
            await s.execute(
                select(ApprovalRequest.account_group_id, ApprovalRequest.created_at, first.c.at)
                .join(first, first.c.approval_request_id == ApprovalRequest.id)
                .where(ApprovalRequest.created_at >= start, ApprovalRequest.created_at < end)
            )
        ).all()
        out: dict[str, list[float]] = defaultdict(list)
        for grp, created, decided in rows:
            out[str(grp) if grp else "workspace"].append(
                max(0.0, (decided - created).total_seconds() / 60)
            )
        return out

    # ------------------------------------------------------------------ run

    async def refresh_workspace(
        self, workspace_id: uuid.UUID, now: datetime, *, force: bool = False
    ) -> Counter[str]:
        counts: Counter[str] = Counter()
        start, end = now - PERIOD, now
        period = (start.isoformat(), end.isoformat())
        ctx = WorkspaceContext(workspace_id)
        async with workspace_transaction(self._sm, ctx) as s:
            recent = await s.scalar(
                select(func.max(Insight.created_at)).where(Insight.kind == "summary")
            )
            if recent is not None and now - recent < MIN_INTERVAL and not force:
                counts["skipped"] += 1
                return counts
            ws = await s.get(Workspace, workspace_id)
            tune = auto_tune(ws)
            ws_name = ws.name if ws else ""
            facts, names = await self._facts(s, start, end)
            aggs = aggregate(facts)
            found = findings(
                aggs,
                names,
                period,
                await self._approval_minutes(s, start, end),
                {str(f.account_id): f.platform for f in facts},
            )
            rows: list[tuple[Insight, Finding]] = []
            for f in found:
                row = Insight(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    account_group_id=uuid.UUID(f.group_id) if f.group_id else None,
                    social_account_id=uuid.UUID(f.account_id) if f.account_id else None,
                    period_start=start,
                    period_end=end,
                    kind=f.kind,
                    title=f.title,
                    body=f.body,
                    evidence=f.evidence,
                    confidence=Decimal(str(f.confidence)),
                )
                s.add(row)
                rows.append((row, f))
            counts["insights"] += len(rows)
            # Weights: sources and pillars with enough posts, toward their performance.
            if tune:
                by_subject = {f.subject: row.id for row, f in rows if f.subject}
                for a in aggs:
                    if a.dimension not in ("source", "pillar") or a.posts < MIN_POSTS:
                        continue
                    entity: Source | ContentPillar | None = (
                        await s.get(Source, uuid.UUID(a.key), with_for_update=True)
                        if a.dimension == "source"
                        else await s.get(ContentPillar, uuid.UUID(a.key), with_for_update=True)
                    )
                    if entity is None:
                        continue
                    before = float(entity.weight)
                    after = next_weight(before, a.mean_ratio)
                    if abs(after - before) < 0.005:
                        continue
                    entity.weight = after
                    s.add(
                        WeightChange(
                            id=uuid7(),
                            workspace_id=workspace_id,
                            subject_type=a.dimension,
                            subject_id=uuid.UUID(a.key),
                            before=before,
                            after=after,
                            reason=(
                                f"{a.posts} posts averaged {a.mean_ratio:.2f}x the accounts' "
                                "typical engagement"
                            ),
                            insight_id=by_subject.get((a.dimension, a.key)),
                        )
                    )
                    counts["weights"] += 1
        summary = await self._summary(workspace_id, ws_name, period, [r for r, _ in rows])
        async with workspace_transaction(self._sm, ctx) as s:
            s.add(
                Insight(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    period_start=start,
                    period_end=end,
                    kind="summary",
                    title="This week",
                    body=summary.summary,
                    evidence={
                        "insights": summary.cites,
                        "recommendations": [r.model_dump() for r in summary.recommendations],
                    },
                    confidence=Decimal("1"),
                )
            )
        await self._email(workspace_id, ws_name, summary, [r for r, _ in rows])
        counts["workspaces"] += 1
        return counts

    async def _summary(
        self, workspace_id: uuid.UUID, ws_name: str, period: tuple[str, str], rows: list[Insight]
    ) -> AnalyticsSummary:
        ids = {str(r.id) for r in rows}
        fallback = AnalyticsSummary(
            summary=(
                "This week: " + "; ".join(r.title for r in rows) + "."
                if rows
                else "Not enough data yet for findings this week."
            ),
            cites=sorted(ids),
            recommendations=[],
        )
        if self._drafting is None:
            return fallback
        run = await agent_runs.start_run(
            self._sm, workspace_id, workflow_key="weekly_insights", trigger="schedule"
        )
        try:
            ctx = AgentContext(
                self._sm,
                run,
                self._drafting.config.daily_cost_cap_usd,
                monthly_cap_usd=self._drafting.config.monthly_cost_cap_usd,
            )
            out = await self._drafting.analytics.run(
                ctx,
                AnalyticsRequest(
                    workspace=ws_name,
                    period=f"{period[0][:10]} to {period[1][:10]}",
                    insights=tuple(InsightLine(str(r.id), r.title, r.body) for r in rows),
                ),
            )
        except Exception as exc:  # any failure: the plain list stands in
            await agent_runs.finish_run(self._sm, run, state="failed", error=type(exc).__name__)
            return fallback
        await agent_runs.finish_run(self._sm, run, state="succeeded")
        return grounded(out, ids)

    async def _email(
        self,
        workspace_id: uuid.UUID,
        ws_name: str,
        summary: AnalyticsSummary,
        rows: list[Insight],
    ) -> None:
        if self._notifier is None:
            return
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            emails = list(
                (
                    await s.execute(
                        select(User.email)
                        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
                        .where(WorkspaceMember.role.in_(("owner", "admin")))
                    )
                ).scalars()
            )
        lines = [summary.summary, ""]
        lines += [f"- {r.title}: {r.body}" for r in rows]
        lines += [f"Try: {rec.text}" for rec in summary.recommendations]
        for to in emails:
            try:
                await self._notifier.send(
                    EmailMessage(
                        to=to,
                        subject=f"Mera weekly insights: {ws_name}",
                        text="\n".join(lines),
                    )
                )
            except Exception:  # an email failure never undoes the run
                log.warning("insights.email_failed", workspace_id=str(workspace_id))


__all__ = ["PERIOD", "InsightRun", "Recommendation", "auto_tune"]
