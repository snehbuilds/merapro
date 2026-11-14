"""The ``metrics`` queue (M10.2): metric sync every fifteen minutes, hashtag stats daily."""

from __future__ import annotations

import procrastinate

from mera.application.analytics.hashtag_stats import HashtagStats
from mera.application.analytics.insights import InsightRun
from mera.application.analytics.sync import MetricsSync
from mera.config import get_settings
from mera.infrastructure.crypto.envelope import LocalKeyProvider
from mera.infrastructure.db.session import create_engine, create_sessionmaker
from mera.infrastructure.platforms.adapters.registry import build_adapters

blueprint = procrastinate.Blueprint()
_sync: MetricsSync | None = None


def worker_sync() -> MetricsSync:
    global _sync
    if _sync is None:
        settings = get_settings()
        _sync = MetricsSync(
            create_sessionmaker(create_engine(settings)),
            build_adapters(settings),
            LocalKeyProvider.from_base64(settings.mera_master_key.get_secret_value()),
        )
    return _sync


@blueprint.periodic(cron="*/15 * * * *")
@blueprint.task(name="sync", queue="metrics", queueing_lock="metrics_sync")
async def sync_task(timestamp: int) -> None:
    await worker_sync().run()


_tags: HashtagStats | None = None


def worker_hashtag_stats() -> HashtagStats:
    global _tags
    if _tags is None:
        _tags = HashtagStats(create_sessionmaker(create_engine(get_settings())))
    return _tags


@blueprint.periodic(cron="20 3 * * *")
@blueprint.task(name="hashtag_stats", queue="metrics", queueing_lock="hashtag_stats")
async def hashtag_stats_task(timestamp: int) -> None:
    """Daily (03:20 UTC): hashtag lift per account from the last ninety days (P3-M1.1)."""
    await worker_hashtag_stats().run()


_insights: InsightRun | None = None


def worker_insights() -> InsightRun:
    global _insights
    if _insights is None:
        from mera.application.drafting.factory import build_drafting
        from mera.infrastructure.notify import build_notifier

        settings = get_settings()
        sm = create_sessionmaker(create_engine(settings))
        _insights = InsightRun(sm, build_drafting(settings, sm), build_notifier(settings))
    return _insights


@blueprint.periodic(cron="40 3 * * 1")
@blueprint.task(name="insights", queue="metrics", queueing_lock="insights")
async def insights_task(timestamp: int) -> None:
    """Weekly (Mondays 03:40 UTC): findings, summary and weight moves (P3-M1.3 to 1.5)."""
    await worker_insights().run()
