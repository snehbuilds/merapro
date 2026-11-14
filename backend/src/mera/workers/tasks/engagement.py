"""Comment polling and reply sending (P2-M6).

The poll runs every five minutes on the ``metrics`` queue (read-only platform sync, like
metrics); each post carries its own next poll time, so the taper is in the data, not the
cron. Replies are sent on the ``publish`` queue, one task per approved reply, under a
queueing lock so a reply is never sent twice.
"""

from __future__ import annotations

import uuid

import procrastinate
import structlog

from mera.application.drafting.factory import build_drafting
from mera.application.services.inbox_service import InboxService
from mera.config import get_settings
from mera.infrastructure.crypto.envelope import LocalKeyProvider
from mera.infrastructure.db.session import create_engine, create_sessionmaker
from mera.infrastructure.platforms.engagement import build_engagement
from mera.infrastructure.queue.deferrer import ProcrastinateDeferrer

log = structlog.get_logger(__name__)
blueprint = procrastinate.Blueprint()
_svc: InboxService | None = None


def worker_inbox() -> InboxService:
    global _svc
    if _svc is None:
        settings = get_settings()
        sm = create_sessionmaker(create_engine(settings))
        _svc = InboxService(
            sm,
            build_engagement(settings),
            LocalKeyProvider.from_base64(settings.mera_master_key.get_secret_value()),
            ProcrastinateDeferrer(settings.libpq_dsn),
            build_drafting(settings, sm),
            triage_per_poll=settings.engagement_triage_per_poll,
            mfa_enforced=settings.mfa_enforced,
        )
    return _svc


@blueprint.periodic(cron="*/5 * * * *")
@blueprint.task(name="poll", queue="metrics", queueing_lock="inbox_poll")
async def poll_task(timestamp: int) -> None:
    await worker_inbox().poll_all()


@blueprint.task(name="send_reply", queue="publish")
async def send_reply_task(workspace_id: str, reply_id: str) -> None:
    out = await worker_inbox().send_reply(uuid.UUID(workspace_id), uuid.UUID(reply_id))
    log.info("reply.send", reply=reply_id, outcome=out)
