"""Analytics reads (M10.4-10.10; docs/04 §4.12).

Rules that carry the weight:

- **"Not reported" is not zero.** A metric no post in scope reported comes back ``None``
  and the UI says "Not reported" (M10 acceptance). A sum counts only reported values.
- **Rollups**: a post's latest snapshot per metric; a set is the sum of its accounts (and the
  split is returned beside it); an account, a group and the workspace are sums of posts.
- **Comparison is against each account's own baseline** (median engagement per post over
  the 90 days before the window), never raw totals: a large account always wins on totals.
- **Approval turnaround** is Mera's own data, so it is available on every platform.
- A scoped member sees only the accounts in their scope, in every figure.
"""

from __future__ import annotations

import csv
import io
import statistics
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application.analytics.insights import auto_tune
from mera.application.approvals import core as approvals
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import ConflictError, InvalidInput, NotFoundError
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.domain.errors import PermissionDenied
from mera.domain.policies import rbac
from mera.domain.policies.calibration import MIN_GAP, MIN_PER_BAND, Calibration, calibrate
from mera.domain.policies.insights import MAX_STEP, MIN_POSTS, aggregate
from mera.domain.policies.rbac import Permission
from mera.domain.policies.scheduling import zone
from mera.domain.principal import Principal
from mera.infrastructure.db.models import (
    AccountGroup,
    AccountGroupMember,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalVariantState,
    ContentPillar,
    Insight,
    MetricSnapshot,
    PostSet,
    PostVersion,
    PublishedPost,
    SocialAccount,
    Source,
    WeightChange,
    Workspace,
)
from mera.infrastructure.db.repositories.audit import AuditEntry, record

METRICS: Final = ("impressions", "reach", "likes", "comments", "shares", "saves", "clicks")
ENGAGEMENT: Final = ("likes", "comments", "shares", "saves")
BASELINE_DAYS: Final = 90
MAX_WINDOW: Final = timedelta(days=400)

Latest = dict[uuid.UUID, dict[str, Decimal | None]]


def _f(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


def engagement(m: dict[str, Decimal | None]) -> float | None:
    vals = [float(m[k]) for k in ENGAGEMENT if m.get(k) is not None]  # type: ignore[arg-type]
    return sum(vals) if vals else None


def summarize(posts: list[PublishedPost], latest: Latest) -> dict[str, Any]:
    out: dict[str, Any] = {"posts": len(posts)}
    for key in METRICS:
        vals = [latest.get(p.id, {}).get(key) for p in posts]
        reported = [float(v) for v in vals if v is not None]
        out[key] = sum(reported) if reported else None  # None: not reported by any post
    engs = [e for e in (engagement(latest.get(p.id, {})) for p in posts) if e is not None]
    out["engagement"] = sum(engs) if engs else None
    out["engagement_rate"] = (
        round(out["engagement"] / out["impressions"], 4)
        if out["engagement"] is not None and out["impressions"]
        else None
    )
    return out


class AnalyticsService(WorkspaceScopedService):
    # ------------------------------------------------------------------ helpers

    async def _principal(
        self, s: AsyncSession, user: AuthenticatedUser, workspace_id: uuid.UUID
    ) -> Principal:
        principal = await load_user_principal(s, user, workspace_id)
        rbac.require(principal, Permission.READ, workspace_id)
        return principal

    async def _visible(
        self, s: AsyncSession, principal: Principal, workspace_id: uuid.UUID
    ) -> set[uuid.UUID]:
        out = set()
        for acct in (await s.execute(select(SocialAccount.id))).scalars():
            groups = await approvals.account_groups(s, acct)
            try:
                self._require_for_account(principal, Permission.READ, workspace_id, acct, groups)
                out.add(acct)
            except PermissionDenied:
                continue
        return out

    async def _posts(
        self,
        s: AsyncSession,
        visible: set[uuid.UUID],
        *,
        frm: datetime | None,
        to: datetime | None,
        group_id: uuid.UUID | None = None,
        account_id: uuid.UUID | None = None,
        post_set_id: uuid.UUID | None = None,
    ) -> list[PublishedPost]:
        q = select(PublishedPost).where(PublishedPost.deleted_at.is_(None))
        if frm:
            q = q.where(PublishedPost.published_at >= frm)
        if to:
            q = q.where(PublishedPost.published_at < to)
        if group_id:
            members = select(AccountGroupMember.social_account_id).where(
                AccountGroupMember.account_group_id == group_id
            )
            q = q.where(
                (PublishedPost.account_group_id == group_id)
                | PublishedPost.social_account_id.in_(members)
            )
        if account_id:
            q = q.where(PublishedPost.social_account_id == account_id)
        if post_set_id:
            q = q.where(PublishedPost.post_set_id == post_set_id)
        rows = (await s.execute(q.order_by(PublishedPost.published_at))).scalars()
        return [p for p in rows if p.social_account_id in visible]

    async def _latest(self, s: AsyncSession, post_ids: list[uuid.UUID]) -> Latest:
        if not post_ids:
            return {}
        rows = (
            await s.execute(
                select(
                    MetricSnapshot.published_post_id,
                    MetricSnapshot.metric_key,
                    MetricSnapshot.value,
                )
                .where(
                    MetricSnapshot.subject_type == "post",
                    MetricSnapshot.published_post_id.in_(post_ids),
                )
                .ext(distinct_on(MetricSnapshot.published_post_id, MetricSnapshot.metric_key))
                .order_by(
                    MetricSnapshot.published_post_id,
                    MetricSnapshot.metric_key,
                    MetricSnapshot.captured_at.desc(),
                )
            )
        ).all()
        out: Latest = defaultdict(dict)
        for pid, key, value in rows:
            if pid is not None:
                out[pid][key] = value
        return out

    @staticmethod
    def _window(
        frm: datetime | None, to: datetime | None, default_days: int = 30
    ) -> tuple[datetime, datetime]:
        end = to or datetime.now(UTC)
        start = frm or end - timedelta(days=default_days)
        if start >= end or end - start > MAX_WINDOW:
            raise InvalidInput(
                "Pick a window of at most 400 days.", code="invalid_window", field="from"
            )
        return start, end

    async def _names(
        self, s: AsyncSession
    ) -> tuple[dict[uuid.UUID, SocialAccount], dict[uuid.UUID, AccountGroup]]:
        accts = {a.id: a for a in (await s.execute(select(SocialAccount))).scalars()}
        groups = {g.id: g for g in (await s.execute(select(AccountGroup))).scalars()}
        return accts, groups

    def _post_row(
        self,
        p: PublishedPost,
        m: dict[str, Decimal | None],
        accts: dict[uuid.UUID, SocialAccount],
        excerpt: str = "",
    ) -> dict[str, Any]:
        a = accts.get(p.social_account_id)
        return {
            "published_post_id": str(p.id),
            "post_set_id": str(p.post_set_id),
            "social_account_id": str(p.social_account_id),
            "account_nickname": a.nickname if a else "",
            "platform": p.platform,
            "account_group_id": str(p.account_group_id) if p.account_group_id else None,
            "published_at": p.published_at.isoformat(),
            "permalink": p.permalink,
            "excerpt": excerpt,
            "metrics": {k: _f(m.get(k)) for k in METRICS},
            "synced": bool(m),
            "engagement": engagement(m),
        }

    # ------------------------------------------------------------------- reads

    # ------------------------------------------------- insights and weights (P3-M1)

    async def _workspace_reader(
        self, s: AsyncSession, user: AuthenticatedUser, workspace_id: uuid.UUID
    ) -> Principal:
        """Insights describe the whole workspace (they compare accounts and groups), so
        they need unrestricted read: ``rbac.require`` with no subject refuses a member
        scoped to some groups."""
        principal = await load_user_principal(s, user, workspace_id)
        rbac.require(principal, Permission.READ, workspace_id)
        return principal

    async def insights(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        limit: int = 50,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self._tx(user, workspace_id, meta) as s:
            await self._workspace_reader(s, user, workspace_id)
            q = select(Insight).order_by(Insight.period_end.desc(), Insight.created_at.desc())
            if kind:
                q = q.where(Insight.kind == kind)
            rows = (await s.execute(q.limit(max(1, min(limit, 200))))).scalars()
            return [_insight_view(r) for r in rows]

    async def acknowledge_insight(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        insight_id: uuid.UUID,
    ) -> dict[str, Any]:
        async with self._tx(user, workspace_id, meta) as s:
            await self._workspace_reader(s, user, workspace_id)
            row = await s.get(Insight, insight_id, with_for_update=True)
            if row is None:
                raise NotFoundError("Insight not found.")
            if row.acknowledged_at is None:
                row.acknowledged_at = datetime.now(UTC)
            return _insight_view(row)

    async def weights(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> dict[str, Any]:
        async with self._tx(user, workspace_id, meta) as s:
            await self._workspace_reader(s, user, workspace_id)
            ws = await s.get(Workspace, workspace_id)
            sources = (await s.execute(select(Source.id, Source.name, Source.weight))).all()
            pillars = (
                await s.execute(select(ContentPillar.id, ContentPillar.name, ContentPillar.weight))
            ).all()
            changes = (
                await s.execute(
                    select(WeightChange).order_by(WeightChange.created_at.desc()).limit(200)
                )
            ).scalars()
            return {
                "auto_tune": auto_tune(ws),
                "max_step": MAX_STEP,
                "current": [
                    {"subject_type": "source", "subject_id": str(i), "name": n, "weight": float(w)}
                    for i, n, w in sources
                ]
                + [
                    {"subject_type": "pillar", "subject_id": str(i), "name": n, "weight": float(w)}
                    for i, n, w in pillars
                ],
                "changes": [_change_view(c) for c in changes],
            }

    async def revert_weight(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        change_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Put the weight back exactly as it was before this change. Refused if it has
        been moved again since (revert the later change first)."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.CONFIGURE_CONTENT, workspace_id)
            c = await s.get(WeightChange, change_id, with_for_update=True)
            if c is None:
                raise NotFoundError("Change not found.")
            if c.reverted_at is not None:
                raise ConflictError("Already reverted.", code="already_reverted")
            entity: Source | ContentPillar | None = (
                await s.get(Source, c.subject_id, with_for_update=True)
                if c.subject_type == "source"
                else await s.get(ContentPillar, c.subject_id, with_for_update=True)
            )
            if entity is None:
                raise NotFoundError("The source or pillar no longer exists.")
            if abs(float(entity.weight) - c.after) > 1e-9:
                raise ConflictError(
                    "The weight has changed since; revert the later change first.",
                    code="weight_moved",
                )
            entity.weight = c.before
            c.reverted_at, c.reverted_by_user_id = datetime.now(UTC), user.user_id
            await record(
                s,
                AuditEntry(
                    action="weight.reverted",
                    actor_type="user",
                    actor_id=user.user_id,
                    surface=meta.surface,
                    workspace_id=workspace_id,
                    subject_type=c.subject_type,
                    subject_id=c.subject_id,
                    before={"weight": c.after},
                    after={"weight": c.before},
                    ip_address=meta.ip_address,
                    request_id=meta.request_id,
                ),
            )
            return _change_view(c)

    async def set_auto_tune(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta, enabled: bool
    ) -> dict[str, Any]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.CONFIGURE_CONTENT, workspace_id)
            ws = await s.get(Workspace, workspace_id, with_for_update=True)
            assert ws is not None
            settings = dict(ws.settings or {})
            settings["analytics"] = {**(settings.get("analytics") or {}), "auto_tune": enabled}
            ws.settings = settings
            return {"auto_tune": enabled}

    async def comparison(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        days: int = 28,
    ) -> dict[str, Any]:
        """Group against group and account against account, each against its own typical
        post (P3-M1.3). Only what the caller may read; below the minimum, no ratio."""
        from mera.application.analytics.insights import InsightRun

        end = datetime.now(UTC)
        start = end - timedelta(days=max(1, min(days, 365)))
        async with self._tx(user, workspace_id, meta) as s:
            principal = await self._principal(s, user, workspace_id)
            visible = await self._visible(s, principal, workspace_id)
            facts, names = await InsightRun(self._sm)._facts(s, start, end)
        facts = [f for f in facts if f.account_id in visible]
        aggs = aggregate(facts)
        visible_groups = {f.group_id for f in facts if f.group_id}

        def rows(dim: str) -> list[dict[str, Any]]:
            out = []
            for a in aggs:
                if a.dimension != dim:
                    continue
                if dim == "group" and uuid.UUID(a.key) not in visible_groups:
                    continue
                enough = a.posts >= MIN_POSTS
                out.append(
                    {
                        "key": a.key,
                        "name": names.of(dim, a.key),
                        "posts": a.posts,
                        "mean_ratio": a.mean_ratio if enough else None,
                        "median_engagement": a.median_engagement,
                        "evidence": "measured" if enough else "not_enough_data",
                    }
                )
            return out

        return {
            "since": start.isoformat(),
            "min_posts": MIN_POSTS,
            "groups": rows("group"),
            "accounts": rows("account"),
            "platforms": rows("platform"),
        }

    async def confidence_calibration(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        account_id: uuid.UUID | None = None,
        days: int = 180,
    ) -> dict[str, Any]:
        """How often agent-written versions were approved as written, by confidence band
        (P3-M1.6). Assisted autopilot stays off while the score does not separate."""
        since = datetime.now(UTC) - timedelta(days=max(1, min(days, 730)))
        async with self._tx(user, workspace_id, meta) as s:
            principal = await self._principal(s, user, workspace_id)
            visible = await self._visible(s, principal, workspace_id)
            if account_id is not None:
                visible &= {account_id}
            rows = (
                await s.execute(
                    select(
                        ApprovalVariantState.social_account_id,
                        PostVersion.confidence_score,
                        ApprovalVariantState.state,
                    )
                    .join(PostVersion, PostVersion.id == ApprovalVariantState.post_version_id)
                    .where(
                        PostVersion.source == "agent",
                        PostVersion.confidence_score.is_not(None),
                        ApprovalVariantState.state.in_(
                            ("approved", "rejected", "expired", "superseded")
                        ),
                        ApprovalVariantState.decided_at >= since,
                        ApprovalVariantState.social_account_id.in_(visible or {uuid.UUID(int=0)}),
                    )
                )
            ).all()
        per: dict[uuid.UUID, list[tuple[float, bool]]] = defaultdict(list)
        for acc, conf, state in rows:
            if conf is not None:
                per[acc].append((float(conf), state == "approved"))
        everything = [o for outs in per.values() for o in outs]

        def view(c: Calibration) -> dict[str, Any]:
            return {
                "decided": c.decided,
                "separates": c.separates,
                "bands": [
                    {
                        "low": b.low,
                        "high": b.high,
                        "decided": b.decided,
                        "approved_unedited": b.approved_unedited,
                        "rate": b.rate,
                    }
                    for b in c.bands
                ],
            }

        return {
            "since": since.isoformat(),
            "min_per_band": MIN_PER_BAND,
            "min_gap": MIN_GAP,
            "workspace": view(calibrate(everything)),
            "accounts": {str(a): view(calibrate(outs)) for a, outs in sorted(per.items())},
        }

    async def overview(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        frm: datetime | None,
        to: datetime | None,
        group_id: uuid.UUID | None,
        account_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        start, end = self._window(frm, to)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await self._principal(s, user, workspace_id)
            visible = await self._visible(s, principal, workspace_id)
            posts = await self._posts(
                s, visible, frm=start, to=end, group_id=group_id, account_id=account_id
            )
            latest = await self._latest(s, [p.id for p in posts])
            accts, _ = await self._names(s)
            by_platform: dict[str, list[PublishedPost]] = defaultdict(list)
            for p in posts:
                by_platform[p.platform].append(p)
            return {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "totals": summarize(posts, latest),
                "accounts": len({p.social_account_id for p in posts}),
                "by_platform": [
                    {"platform": k, **summarize(v, latest)} for k, v in sorted(by_platform.items())
                ],
                "by_account": [
                    {
                        "social_account_id": str(a),
                        "nickname": accts[a].nickname if a in accts else "",
                        "platform": accts[a].platform if a in accts else "",
                        **summarize([p for p in posts if p.social_account_id == a], latest),
                    }
                    for a in sorted(
                        {p.social_account_id for p in posts},
                        key=lambda x: accts[x].nickname if x in accts else "",
                    )
                ],
            }

    async def timeseries(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        metric: str,
        frm: datetime | None,
        to: datetime | None,
        group_id: uuid.UUID | None,
        account_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        if metric not in (*METRICS, "engagement", "posts"):
            raise InvalidInput("Unknown metric.", code="unknown_metric", field="metric")
        start, end = self._window(frm, to)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await self._principal(s, user, workspace_id)
            ws = await s.get(Workspace, workspace_id)
            tz = zone(ws.timezone if ws else "UTC")
            visible = await self._visible(s, principal, workspace_id)
            posts = await self._posts(
                s, visible, frm=start, to=end, group_id=group_id, account_id=account_id
            )
            latest = await self._latest(s, [p.id for p in posts])
        days: dict[date, list[PublishedPost]] = defaultdict(list)
        for p in posts:
            days[p.published_at.astimezone(tz).date()].append(p)
        out = []
        d = start.astimezone(tz).date()
        while d <= (end - timedelta(seconds=1)).astimezone(tz).date():
            ps = days.get(d, [])
            if metric == "posts":
                value: float | None = float(len(ps))
            elif metric == "engagement":
                value = summarize(ps, latest)["engagement"] if ps else 0.0
            else:
                value = summarize(ps, latest)[metric] if ps else 0.0
            out.append({"date": d.isoformat(), "value": value, "posts": len(ps)})
            d += timedelta(days=1)
        return {"metric": metric, "granularity": "day", "points": out}

    async def post(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        post_id: uuid.UUID,
        meta: RequestMeta,
    ) -> dict[str, Any]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await self._principal(s, user, workspace_id)
            p = await s.get(PublishedPost, post_id)
            if p is None or p.social_account_id not in await self._visible(
                s, principal, workspace_id
            ):
                raise NotFoundError("Post not found.")
            latest = await self._latest(s, [p.id])
            accts, _ = await self._names(s)
            ver = await s.get(PostVersion, p.post_version_id)
            series = defaultdict(list)
            for snap in (
                await s.execute(
                    select(MetricSnapshot)
                    .where(MetricSnapshot.published_post_id == p.id)
                    .order_by(MetricSnapshot.captured_at)
                )
            ).scalars():
                series[snap.metric_key].append(
                    {"t": snap.captured_at.isoformat(), "value": _f(snap.value)}
                )
            return {
                **self._post_row(p, latest.get(p.id, {}), accts, (ver.body if ver else "")[:280]),
                "series": series,
            }

    async def post_set(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, set_id: uuid.UUID, meta: RequestMeta
    ) -> dict[str, Any]:
        """Combined performance **and** the per-account split, clearly separate (M10)."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await self._principal(s, user, workspace_id)
            ps = await s.get(PostSet, set_id)
            if ps is None:
                raise NotFoundError("Post set not found.")
            visible = await self._visible(s, principal, workspace_id)
            posts = await self._posts(s, visible, frm=None, to=None, post_set_id=set_id)
            latest = await self._latest(s, [p.id for p in posts])
            accts, _ = await self._names(s)
            return {
                "post_set_id": str(set_id),
                "title": ps.title,
                "combined": summarize(posts, latest),
                "accounts": [self._post_row(p, latest.get(p.id, {}), accts) for p in posts],
            }

    async def account_comparison(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        frm: datetime | None,
        to: datetime | None,
        group_id: uuid.UUID | None,
    ) -> list[dict[str, Any]]:
        start, end = self._window(frm, to)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await self._principal(s, user, workspace_id)
            visible = await self._visible(s, principal, workspace_id)
            accts, _ = await self._names(s)
            posts = await self._posts(
                s, visible, frm=start - timedelta(days=BASELINE_DAYS), to=end, group_id=group_id
            )
            latest = await self._latest(s, [p.id for p in posts])
        rows = []
        for a in sorted({p.social_account_id for p in posts}):
            mine = [p for p in posts if p.social_account_id == a]
            now_e = [
                e
                for p in mine
                if p.published_at >= start and (e := engagement(latest.get(p.id, {}))) is not None
            ]
            base_e = [
                e
                for p in mine
                if p.published_at < start and (e := engagement(latest.get(p.id, {}))) is not None
            ]
            current = statistics.median(now_e) if now_e else None
            baseline = statistics.median(base_e) if base_e else None
            acct = accts.get(a)
            rows.append(
                {
                    "social_account_id": str(a),
                    "nickname": acct.nickname if acct else "",
                    "platform": acct.platform if acct else "",
                    "posts": sum(1 for p in mine if p.published_at >= start),
                    "median_engagement": current,
                    "baseline_median_engagement": baseline,
                    "baseline_posts": len(base_e),
                    # >1: better than this account's own usual; None: no baseline yet.
                    "index_vs_baseline": round(current / baseline, 3)
                    if current is not None and baseline
                    else None,
                }
            )

        def rank(r: dict[str, Any]) -> tuple[bool, float]:
            idx = r["index_vs_baseline"]
            return (idx is None, -float(idx) if isinstance(idx, (int, float)) else 0.0)

        rows.sort(key=rank)
        return rows

    async def group_comparison(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        frm: datetime | None,
        to: datetime | None,
    ) -> list[dict[str, Any]]:
        accounts = await self.account_comparison(
            user, workspace_id, meta, frm=frm, to=to, group_id=None
        )
        by_acct = {r["social_account_id"]: r for r in accounts}
        async with self._tx(user, workspace_id, meta) as s:
            await self._principal(s, user, workspace_id)
            _, groups = await self._names(s)
            members = (
                await s.execute(
                    select(
                        AccountGroupMember.account_group_id, AccountGroupMember.social_account_id
                    )
                )
            ).all()
        out = []
        for gid, g in sorted(groups.items(), key=lambda kv: kv[1].name):
            rows = [by_acct[str(a)] for (grp, a) in members if grp == gid and str(a) in by_acct]
            idx = [r["index_vs_baseline"] for r in rows if r["index_vs_baseline"] is not None]
            out.append(
                {
                    "account_group_id": str(gid),
                    "name": g.name,
                    "color": g.color,
                    "accounts": len(rows),
                    "posts": sum(r["posts"] for r in rows),
                    "index_vs_baseline": round(statistics.mean(idx), 3) if idx else None,
                }
            )
        return out

    async def top_posts(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        frm: datetime | None,
        to: datetime | None,
        group_id: uuid.UUID | None,
        account_id: uuid.UUID | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        start, end = self._window(frm, to)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await self._principal(s, user, workspace_id)
            visible = await self._visible(s, principal, workspace_id)
            posts = await self._posts(
                s, visible, frm=start, to=end, group_id=group_id, account_id=account_id
            )
            latest = await self._latest(s, [p.id for p in posts])
            accts, _ = await self._names(s)
            ranked = sorted(
                posts, key=lambda p: engagement(latest.get(p.id, {})) or -1, reverse=True
            )[:limit]
            out = []
            for p in ranked:
                ver = await s.get(PostVersion, p.post_version_id)
                out.append(
                    self._post_row(
                        p,
                        latest.get(p.id, {}),
                        accts,
                        " ".join((ver.body if ver else "").split())[:160],
                    )
                )
            return out

    async def approval_turnaround(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        frm: datetime | None,
        to: datetime | None,
        group_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        """Minutes from request to first decision. Mera owns this data, so it works on every
        platform (M10.8)."""
        start, end = self._window(frm, to)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await self._principal(s, user, workspace_id)
            visible = await self._visible(s, principal, workspace_id)
            q = select(ApprovalRequest).where(
                ApprovalRequest.created_at >= start, ApprovalRequest.created_at < end
            )
            if group_id:
                q = q.where(ApprovalRequest.account_group_id == group_id)
            reqs = list((await s.execute(q)).scalars())
            ws = await s.get(Workspace, workspace_id)
            tz = zone(ws.timezone if ws else "UTC")
            _, groups = await self._names(s)
            minutes: list[float] = []
            by_day: dict[str, list[float]] = defaultdict(list)
            by_group: dict[str, list[float]] = defaultdict(list)
            undecided = 0
            for r in reqs:
                accounts = {
                    row.social_account_id for row in await approvals.variant_states(s, r.id)
                }
                if not accounts & visible:
                    continue
                first = await s.scalar(
                    select(ApprovalDecision.decided_at)
                    .where(
                        ApprovalDecision.approval_request_id == r.id,
                        ApprovalDecision.action.in_(["approve", "reject"]),
                    )
                    .order_by(ApprovalDecision.decided_at)
                    .limit(1)
                )
                if first is None:
                    undecided += 1
                    continue
                m = (first - r.created_at).total_seconds() / 60
                minutes.append(m)
                by_day[r.created_at.astimezone(tz).date().isoformat()].append(m)
                g = groups.get(r.account_group_id) if r.account_group_id else None
                by_group[g.name if g else "Direct"].append(m)

        def stats(xs: list[float]) -> dict[str, Any]:
            if not xs:
                return {"count": 0, "median_minutes": None, "p90_minutes": None}
            ordered = sorted(xs)
            return {
                "count": len(xs),
                "median_minutes": round(statistics.median(ordered), 1),
                "p90_minutes": round(ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))], 1),
            }

        return {
            **stats(minutes),
            "undecided": undecided,
            "by_day": [{"date": d, **stats(v)} for d, v in sorted(by_day.items())],
            "by_group": [{"group": g, **stats(v)} for g, v in sorted(by_group.items())],
        }

    async def export_csv(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        frm: datetime | None,
        to: datetime | None,
        group_id: uuid.UUID | None,
        account_id: uuid.UUID | None,
    ) -> str:
        """One row per published post in scope; an unreported metric is the text
        ``not reported`` rather than an empty cell or a zero (M10.9, 10.10)."""
        start, end = self._window(frm, to, default_days=90)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await self._principal(s, user, workspace_id)
            visible = await self._visible(s, principal, workspace_id)
            posts = await self._posts(
                s, visible, frm=start, to=end, group_id=group_id, account_id=account_id
            )
            latest = await self._latest(s, [p.id for p in posts])
            accts, groups = await self._names(s)
            sets = {
                ps.id: ps.title
                for ps in (
                    await s.execute(
                        select(PostSet).where(
                            PostSet.id.in_({p.post_set_id for p in posts} or {uuid.UUID(int=0)})
                        )
                    )
                ).scalars()
            }
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            [
                "published_at",
                "account",
                "platform",
                "group",
                "post_set",
                "permalink",
                *METRICS,
                "engagement",
            ]
        )
        for p in posts:
            m = latest.get(p.id, {})
            a = accts.get(p.social_account_id)
            g = groups.get(p.account_group_id) if p.account_group_id else None

            e = engagement(m)
            w.writerow(
                [
                    p.published_at.isoformat(),
                    _safe(a.nickname if a else ""),
                    p.platform,
                    _safe(g.name if g else ""),
                    _safe(sets.get(p.post_set_id, "")),
                    p.permalink or "",
                    *[_cell(m, k) for k in METRICS],
                    "not reported" if e is None else f"{e:g}",
                ]
            )
        return buf.getvalue()


def _cell(m: dict[str, Decimal | None], key: str) -> str:
    if not m:
        return "not synced"
    v = m.get(key)
    return "not reported" if v is None else f"{float(v):g}"


def _safe(text: str) -> str:
    """CSV injection guard: a cell starting with a formula character is prefixed."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _insight_view(r: Insight) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "kind": r.kind,
        "title": r.title,
        "body": r.body,
        "evidence": r.evidence,
        "confidence": float(r.confidence),
        "account_group_id": str(r.account_group_id) if r.account_group_id else None,
        "social_account_id": str(r.social_account_id) if r.social_account_id else None,
        "period_start": r.period_start.isoformat(),
        "period_end": r.period_end.isoformat(),
        "acknowledged_at": r.acknowledged_at.isoformat() if r.acknowledged_at else None,
    }


def _change_view(c: WeightChange) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "subject_type": c.subject_type,
        "subject_id": str(c.subject_id),
        "before": c.before,
        "after": c.after,
        "reason": c.reason,
        "insight_id": str(c.insight_id) if c.insight_id else None,
        "reverted_at": c.reverted_at.isoformat() if c.reverted_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }
