"""Metric sync (M10.2, 10.3): per published post, on an age-tapered cadence.

- Cadence: hourly for the first day, every six hours for the first week, daily to day 30,
  then never (the curve is flat by then; docs/03 §10).
- Snapshots are keyed by (subject, metric, hour), unique in the database, so a re-run in the
  same window writes nothing new (M10 acceptance).
- ``value`` NULL means **not reported** by the platform; it is never written as zero.
- Rate limits: a 429 reschedules that post after the platform's ``Retry-After`` instead of
  losing the window, and each run takes at most ``PER_ACCOUNT`` posts per account in
  round-robin order, so one busy account cannot starve the rest.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.events import emit
from mera.domain.context import WorkspaceContext
from mera.domain.ports.key_provider import KeyProvider
from mera.domain.ports.platform_adapter import (
    AuthExpired,
    MetricReading,
    PlatformAdapter,
    PublishError,
    RateLimited,
)
from mera.infrastructure.db.models import MetricSnapshot, PublishedPost, SocialAccount, Workspace
from mera.infrastructure.db.session import system_scan_transaction, workspace_transaction

log = structlog.get_logger(__name__)
PER_ACCOUNT = 20
CREDENTIALS_PURPOSE = "social_account.credentials"
METRIC_KEYS = frozenset(
    {
        "impressions",
        "reach",
        "likes",
        "comments",
        "shares",
        "saves",
        "clicks",
        "ctr",
        "engagement_rate",
        "followers",
        "video_views",
    }
)


def next_sync(published_at: datetime, now: datetime) -> datetime | None:
    age = now - published_at
    if age < timedelta(days=1):
        return now + timedelta(hours=1)
    if age < timedelta(days=7):
        return now + timedelta(hours=6)
    if age < timedelta(days=30):
        return now + timedelta(days=1)
    return None


def window(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0)


def _value(v: float | None) -> Decimal | None:
    return None if v is None else Decimal(str(round(v, 6)))


async def write_snapshots(
    s: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    subject_type: str,
    account_id: uuid.UUID,
    readings: list[MetricReading],
    captured_at: datetime,
    published: PublishedPost | None = None,
) -> int:
    rows = [
        {
            "workspace_id": workspace_id,
            "subject_type": subject_type,
            "published_post_id": published.id if published else None,
            "social_account_id": account_id,
            "post_set_id": published.post_set_id if published else None,
            "account_group_id": published.account_group_id if published else None,
            "metric_key": r.metric_key,
            "value": _value(r.value),
            "platform_field": r.platform_field[:200],
            "captured_at": captured_at,
        }
        for r in readings
        if r.metric_key in METRIC_KEYS
    ]
    if not rows:
        return 0
    result = await s.execute(
        insert(MetricSnapshot).values(rows).on_conflict_do_nothing().returning(MetricSnapshot.id)
    )
    return len(result.all())


class MetricsSync:
    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        adapters: dict[str, PlatformAdapter],
        key_provider: KeyProvider,
    ) -> None:
        self._sm = sm
        self._adapters = adapters
        self._kp = key_provider

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
                counts.update(await self.sync_workspace(ws, now))
            except Exception as exc:  # one workspace must not stop the others
                log.error(
                    "metrics.workspace_failed", workspace_id=str(ws), error_type=type(exc).__name__
                )
                counts["error"] += 1
        if counts:
            log.info("metrics.sync", **counts)
        return dict(counts)

    def _creds(self, account: SocialAccount) -> dict[str, Any]:
        raw = self._kp.decrypt(
            str(account.credentials_ct),
            workspace_id=account.workspace_id,
            purpose=CREDENTIALS_PURPOSE,
            record_id=account.id,
        )
        out: dict[str, Any] = json.loads(raw)
        return out

    async def sync_workspace(self, workspace_id: uuid.UUID, now: datetime) -> Counter[str]:
        counts: Counter[str] = Counter()
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            due = list(
                (
                    await s.execute(
                        select(PublishedPost)
                        .where(
                            PublishedPost.deleted_at.is_(None), PublishedPost.next_metrics_at <= now
                        )
                        .order_by(PublishedPost.next_metrics_at)
                        .limit(2000)
                    )
                ).scalars()
            )
            accounts = (
                {
                    a.id: a
                    for a in (
                        await s.execute(
                            select(SocialAccount).where(
                                SocialAccount.id.in_({p.social_account_id for p in due})
                            )
                        )
                    ).scalars()
                }
                if due
                else {}
            )
            follower_due = list(
                (
                    await s.execute(
                        select(SocialAccount).where(
                            SocialAccount.status == "active",
                            SocialAccount.credentials_ct.is_not(None),
                        )
                    )
                ).scalars()
            )
        per_account: dict[uuid.UUID, list[PublishedPost]] = defaultdict(list)
        for p in due:
            if len(per_account[p.social_account_id]) < PER_ACCOUNT:
                per_account[p.social_account_id].append(p)
        # Round robin across accounts.
        queue: list[PublishedPost] = []
        lists = list(per_account.values())
        for i in range(PER_ACCOUNT):
            queue.extend(lst[i] for lst in lists if i < len(lst))
        blocked: set[uuid.UUID] = set()
        for p in queue:
            account = accounts.get(p.social_account_id)
            adapter = self._adapters.get(p.platform)
            if (
                account is None
                or adapter is None
                or account.status != "active"
                or account.credentials_ct is None
            ):
                counts["skipped"] += 1
                await self._reschedule(workspace_id, p.id, now + timedelta(hours=6))
                continue
            if account.id in blocked:
                counts["deferred"] += 1
                continue
            try:
                readings = await adapter.post_metrics(
                    self._creds(account), account.platform_account_id, p.platform_post_id
                )
            except RateLimited as exc:
                blocked.add(account.id)
                await self._reschedule(
                    workspace_id, p.id, now + timedelta(seconds=max(60, exc.retry_after or 900))
                )
                counts["rate_limited"] += 1
                continue
            except AuthExpired:
                blocked.add(account.id)
                await self._reschedule(workspace_id, p.id, now + timedelta(hours=6))
                counts["auth"] += 1
                continue
            except PublishError:
                await self._reschedule(workspace_id, p.id, now + timedelta(hours=1))
                counts["failed"] += 1
                continue
            async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
                pp = await s.get(PublishedPost, p.id, with_for_update=True)
                if pp is None:
                    continue
                written = await write_snapshots(
                    s,
                    workspace_id=workspace_id,
                    subject_type="post",
                    account_id=pp.social_account_id,
                    readings=readings,
                    captured_at=window(now),
                    published=pp,
                )
                pp.metrics_synced_at = now
                pp.next_metrics_at = next_sync(pp.published_at, now)
                counts["snapshots"] += written
                counts["posts"] += 1
        # Followers, once a day per account.
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        for account in follower_due:
            adapter = self._adapters.get(account.platform)
            if adapter is None or account.id in blocked:
                continue
            async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
                have = await s.scalar(
                    select(MetricSnapshot.id).where(
                        MetricSnapshot.subject_type == "account",
                        MetricSnapshot.social_account_id == account.id,
                        MetricSnapshot.captured_at == day,
                    )
                )
            if have:
                continue
            try:
                readings = await adapter.account_metrics(
                    self._creds(account), account.platform_account_id
                )
            except PublishError:
                continue
            async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
                counts["account_snapshots"] += await write_snapshots(
                    s,
                    workspace_id=workspace_id,
                    subject_type="account",
                    account_id=account.id,
                    readings=readings,
                    captured_at=day,
                )
        if counts.get("posts"):
            async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
                await emit(s, workspace_id, "metrics.synced", payload={"posts": counts["posts"]})
        return counts

    async def _reschedule(self, workspace_id: uuid.UUID, post_id: uuid.UUID, at: datetime) -> None:
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            pp = await s.get(PublishedPost, post_id, with_for_update=True)
            if pp is not None:
                pp.next_metrics_at = at
