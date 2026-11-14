"""The ``ingest`` and ``agents`` queues for M4 (M4.9; docs/01 §2.1).

A periodic dispatcher runs every minute. It enumerates live workspaces through the
system-scan policy (ADR-0006 addendum 2), then, inside each workspace's own RLS context,
finds sources whose ``next_fetch_at`` has passed and workspaces with work waiting
(pending items, or pillars without a current embedding). Each fetch is its own job with a
per-source lock, so one slow feed never delays another; enrichment is one job per
workspace on the ``agents`` queue, serialised by a per-workspace lock.

"Fetch now" in the API only sets ``next_fetch_at``; this dispatcher is what runs it.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import procrastinate
import structlog
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.ingest.factory import build_ingestion
from mera.application.ingest.pipeline import Ingestion
from mera.config import get_settings
from mera.domain.context import WorkspaceContext
from mera.domain.value_objects.content import ItemState, SourceStatus, SourceType
from mera.infrastructure.db.models import ContentItem, ContentPillar, Source, Workspace
from mera.infrastructure.db.session import (
    create_engine,
    create_sessionmaker,
    system_scan_transaction,
    workspace_transaction,
)

blueprint = procrastinate.Blueprint()
log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Work:
    fetches: list[tuple[uuid.UUID, uuid.UUID]]  # (workspace_id, source_id)
    enrich: list[uuid.UUID]  # workspace ids


async def find_work(
    sm: async_sessionmaker[AsyncSession], *, embedding_model: str, now: datetime | None = None
) -> Work:
    now = now or datetime.now(UTC)
    async with system_scan_transaction(sm) as s:
        workspace_ids = list(
            (
                await s.execute(
                    select(Workspace.id).where(
                        Workspace.deleted_at.is_(None), Workspace.archived_at.is_(None)
                    )
                )
            ).scalars()
        )
    fetches: list[tuple[uuid.UUID, uuid.UUID]] = []
    enrich: list[uuid.UUID] = []
    for ws in workspace_ids:
        async with workspace_transaction(sm, WorkspaceContext(ws)) as s:
            due = (
                await s.execute(
                    select(Source.id).where(
                        Source.status != SourceStatus.PAUSED.value,
                        Source.source_type == SourceType.RSS.value,
                        Source.next_fetch_at.is_not(None),
                        Source.next_fetch_at <= now,
                    )
                )
            ).scalars()
            fetches.extend((ws, sid) for sid in due)
            waiting = await s.scalar(
                select(
                    or_(
                        exists().where(ContentItem.state == ItemState.PENDING.value),
                        exists().where(
                            or_(
                                ContentPillar.embedding.is_(None),
                                ContentPillar.embedding_model.is_distinct_from(embedding_model),
                            )
                        ),
                    )
                )
            )
            if waiting:
                enrich.append(ws)
    return Work(fetches, enrich)


Defer = Callable[..., Awaitable[None]]


async def dispatch(
    sm: async_sessionmaker[AsyncSession],
    *,
    embedding_model: str,
    defer_fetch: Defer,
    defer_enrich: Defer,
    now: datetime | None = None,
) -> dict[str, int]:
    work = await find_work(sm, embedding_model=embedding_model, now=now)
    for ws, sid in work.fetches:
        await defer_fetch(ws, sid)
    for ws in work.enrich:
        await defer_enrich(ws)
    counts = {"fetches": len(work.fetches), "enrich": len(work.enrich)}
    if work.fetches or work.enrich:
        log.info("ingest.dispatch", **counts)
    return counts


_deps: tuple[async_sessionmaker[AsyncSession], Ingestion] | None = None


def _worker_deps() -> tuple[async_sessionmaker[AsyncSession], Ingestion]:
    global _deps
    if _deps is None:
        settings = get_settings()
        sm = create_sessionmaker(create_engine(settings))
        ingestion = build_ingestion(settings, sm)
        from mera.workers.tasks.mcp import worker_registry

        ingestion.mcp = worker_registry()
        _deps = (sm, ingestion)
    return _deps


async def _defer_fetch(workspace_id: uuid.UUID, source_id: uuid.UUID) -> None:
    with contextlib.suppress(AlreadyEnqueued):
        await fetch_source_task.configure(
            queueing_lock=f"fetch:{source_id}", lock=f"fetch:{source_id}"
        ).defer_async(workspace_id=str(workspace_id), source_id=str(source_id))


async def _defer_enrich(workspace_id: uuid.UUID) -> None:
    with contextlib.suppress(AlreadyEnqueued):
        await enrich_task.configure(
            queueing_lock=f"enrich:{workspace_id}", lock=f"enrich:{workspace_id}"
        ).defer_async(workspace_id=str(workspace_id))


@blueprint.periodic(cron="* * * * *")
@blueprint.task(name="dispatch", queue="ingest", queueing_lock="ingest_dispatch")
async def dispatch_task(timestamp: int) -> None:
    sm, ingestion = _worker_deps()
    await dispatch(
        sm,
        embedding_model=ingestion.embedding_model,
        defer_fetch=_defer_fetch,
        defer_enrich=_defer_enrich,
    )


@blueprint.task(name="fetch_source", queue="ingest")
async def fetch_source_task(workspace_id: str, source_id: str) -> None:
    _sm, ingestion = _worker_deps()
    ws = uuid.UUID(workspace_id)
    outcome = await ingestion.fetch_source(ws, uuid.UUID(source_id))
    if outcome.new_items:
        await _defer_enrich(ws)


@blueprint.task(name="enrich", queue="agents")
async def enrich_task(workspace_id: str) -> None:
    _sm, ingestion = _worker_deps()
    ws = uuid.UUID(workspace_id)
    outcome = await ingestion.enrich_pending(ws)
    # The dispatcher would find the rest within a minute; deferring now drains a burst
    # sooner. The queueing lock makes this a no-op if one is already waiting.
    if outcome.remaining and not outcome.capped:
        await _defer_enrich(ws)
