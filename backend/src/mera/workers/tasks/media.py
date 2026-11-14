"""The ``media`` queue (P2-M5.3): one attempt of one generation job per task.

The job row is the state; the task only advances it. A retryable failure re-queues the
job under a new lock (at most three attempts), a refusal fails it, and the provider call
is bounded by ``MEDIA_JOB_TIMEOUT_S``.
"""

from __future__ import annotations

import uuid

import procrastinate
import structlog

from mera.application.drafting.factory import build_drafting
from mera.application.media_factory import build_media_fetcher, build_object_store
from mera.application.services.approval_service import ApprovalService
from mera.application.services.media_generation_service import MediaGenerationService
from mera.application.services.media_service import MediaService
from mera.config import get_settings
from mera.infrastructure.db.session import create_engine, create_sessionmaker
from mera.infrastructure.media.generators import build_media_generator
from mera.infrastructure.queue.deferrer import ProcrastinateDeferrer

log = structlog.get_logger(__name__)
blueprint = procrastinate.Blueprint()

_svc: MediaGenerationService | None = None


def worker_media() -> MediaGenerationService:
    global _svc
    if _svc is None:
        settings = get_settings()
        sm = create_sessionmaker(create_engine(settings))
        drafting = build_drafting(settings, sm)
        media = MediaService(
            sm, build_object_store(settings), build_media_fetcher(settings), drafting=drafting
        )
        deferrer = ProcrastinateDeferrer(settings.libpq_dsn)
        _svc = MediaGenerationService(
            sm,
            settings,
            build_media_generator(settings),
            media,
            deferrer,
            drafting,
            approvals=ApprovalService(sm, deferrer),
        )
        from mera.workers.tasks.mcp import worker_registry

        _svc._registry = worker_registry()
    return _svc


@blueprint.task(name="generate", queue="media")
async def generate_task(workspace_id: str, job_id: str) -> None:
    state = await worker_media().run_job(uuid.UUID(workspace_id), uuid.UUID(job_id))
    log.info("media_job.ran", job=job_id, state=state)
