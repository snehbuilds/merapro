"""The ``publish`` queue (M8.5): a minute dispatcher and the per-post publish job.

The dispatcher finds due slots in every workspace and defers one job each, with
``lock=account:<id>`` (two posts for one account serialise, M8 acceptance) and
``queueing_lock=publish:<slot>`` (a slot is never queued twice). The job itself re-checks
everything under a row lock, so an early or duplicate job is harmless.
"""

from __future__ import annotations

import uuid

import procrastinate
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.media_factory import build_object_store
from mera.application.publishing.publisher import Publisher
from mera.config import get_settings
from mera.infrastructure.crypto.envelope import LocalKeyProvider
from mera.infrastructure.db.session import create_engine, create_sessionmaker
from mera.infrastructure.platforms.adapters.registry import build_adapters
from mera.infrastructure.queue.deferrer import ProcrastinateDeferrer

blueprint = procrastinate.Blueprint()
log = structlog.get_logger(__name__)

_deps: tuple[async_sessionmaker[AsyncSession], Publisher] | None = None


def worker_publisher() -> Publisher:
    global _deps
    if _deps is None:
        settings = get_settings()
        sm = create_sessionmaker(create_engine(settings))
        publisher = Publisher(
            sm,
            build_adapters(settings),
            LocalKeyProvider.from_base64(settings.mera_master_key.get_secret_value()),
            build_object_store(settings),
            deferrer=ProcrastinateDeferrer(settings.libpq_dsn),
            sandbox=settings.sandbox_mode,
        )
        _deps = (sm, publisher)
    return _deps[1]


@blueprint.periodic(cron="* * * * *")
@blueprint.task(name="dispatch_due", queue="publish", queueing_lock="publish_dispatch")
async def dispatch_due(timestamp: int) -> None:
    n = await worker_publisher().enqueue_due()
    if n:
        log.info("publish.dispatched", jobs=n)


@blueprint.task(name="publish", queue="publish", retry=False)
async def publish_task(workspace_id: str, scheduled_post_id: str) -> None:
    outcome = await worker_publisher().publish(
        uuid.UUID(workspace_id), uuid.UUID(scheduled_post_id)
    )
    log.info("publish.outcome", scheduled_post_id=scheduled_post_id, outcome=outcome)


@blueprint.task(name="comment", queue="publish", retry=False)
async def comment_task(workspace_id: str, published_comment_id: str) -> None:
    outcome = await worker_publisher().publish_comment(
        uuid.UUID(workspace_id), uuid.UUID(published_comment_id)
    )
    log.info("publish.comment_outcome", published_comment_id=published_comment_id, outcome=outcome)
