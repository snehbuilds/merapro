"""The ``approvals`` queue (M6.8, M7.15): the clocked sweeper, outbound card dispatch and
inbound Telegram updates.

Webhook deliveries are queued by the ``bots`` service and handled here, so the webhook
answers 200 immediately and Telegram never retries a slow request into a duplicate
(docs/07 §8). Deduplication by ``update_id`` happens inside ``handle_update`` as well.
"""

from __future__ import annotations

import uuid
from typing import Any

import procrastinate
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.approvals.sweeper import sweep
from mera.application.channels.bot import BotRuntime
from mera.application.channels.factory import build_bot, build_whatsapp_bot
from mera.application.channels.whatsapp_state import WhatsAppState
from mera.application.drafting.factory import build_drafting
from mera.application.services.post_service import PostService
from mera.config import get_settings
from mera.infrastructure.db.session import create_engine, create_sessionmaker
from mera.infrastructure.queue.deferrer import ProcrastinateDeferrer

blueprint = procrastinate.Blueprint()
log = structlog.get_logger(__name__)

_deps: tuple[async_sessionmaker[AsyncSession], ProcrastinateDeferrer, BotRuntime] | None = None


def worker_bot() -> tuple[async_sessionmaker[AsyncSession], ProcrastinateDeferrer, BotRuntime]:
    global _deps
    if _deps is None:
        settings = get_settings()
        sm = create_sessionmaker(create_engine(settings))
        deferrer = ProcrastinateDeferrer(settings.libpq_dsn)
        posts = PostService(sm, build_drafting(settings, sm), deferrer)
        from mera.workers.tasks.engagement import worker_inbox
        from mera.workers.tasks.mcp import worker_registry
        from mera.workers.tasks.media import worker_media

        _deps = (
            sm,
            deferrer,
            build_bot(
                settings,
                sm,
                deferrer,
                posts=posts,
                media=worker_media(),
                inbox=worker_inbox(),
                registry=worker_registry(),
            ),
        )
    return _deps


@blueprint.periodic(cron="* * * * *")
@blueprint.task(name="sweep", queue="approvals", queueing_lock="approvals_sweep")
async def sweep_task(timestamp: int) -> None:
    sm, deferrer, _ = worker_bot()
    await sweep(sm, deferrer)


@blueprint.task(
    name="dispatch", queue="approvals", retry=procrastinate.RetryStrategy(max_attempts=3, wait=5)
)
async def dispatch_task(kind: str, workspace_id: str, subject_id: str) -> None:
    _, _, bot = worker_bot()
    n = await bot.dispatch(kind, uuid.UUID(workspace_id), uuid.UUID(subject_id))
    wa = worker_whatsapp()
    if wa is not None:
        # The same card on WhatsApp for approvers paired there (P2-M4).
        n += await wa[0].dispatch(kind, uuid.UUID(workspace_id), uuid.UUID(subject_id))
    log.info("approvals.dispatched", kind=kind, messages=n)


@blueprint.task(name="telegram_update", queue="approvals")
async def telegram_update_task(update: dict[str, Any]) -> None:
    _, _, bot = worker_bot()
    outcome = await bot.handle_update(update)
    log.info("telegram.update", outcome=outcome)


_wa: tuple[BotRuntime, WhatsAppState] | bool | None = False


def worker_whatsapp() -> tuple[BotRuntime, WhatsAppState] | None:
    """The WhatsApp runtime, or None when WhatsApp is not configured."""
    global _wa
    if _wa is False:
        settings = get_settings()
        sm, deferrer, _ = worker_bot()
        drafting = build_drafting(settings, sm)
        from mera.workers.tasks.engagement import worker_inbox
        from mera.workers.tasks.mcp import worker_registry
        from mera.workers.tasks.media import worker_media

        _wa = build_whatsapp_bot(
            settings,
            sm,
            deferrer,
            posts=PostService(sm, drafting, deferrer=deferrer),
            media=worker_media(),
            inbox=worker_inbox(),
            registry=worker_registry(),
        )
    return _wa if isinstance(_wa, tuple) else None


async def handle_whatsapp(
    runtime: BotRuntime, state: WhatsAppState, payload: dict[str, Any]
) -> list[str]:
    """A verified webhook payload: each message opens the window, then runs through the same
    runtime as Telegram. "Show me" on a template re-sends the open cards in full."""
    from mera.infrastructure.channels.whatsapp import REOPEN, updates_from_webhook

    outcomes = []
    for update in updates_from_webhook(payload):
        cq = update.get("callback_query")
        chat = str(
            ((cq or {}).get("message") or update.get("message") or {}).get("chat", {}).get("id", "")
        )
        if chat:
            await state.note_inbound(chat)
        if cq is not None and cq.get("data") == REOPEN:
            outcomes.append(f"resent:{await runtime.resend_cards(chat)}")
            continue
        outcomes.append(await runtime.handle_update(update))
        if chat:
            await state.note_inbound(chat)  # a binding the update just created
    return outcomes


@blueprint.task(name="whatsapp_update", queue="approvals")
async def whatsapp_update_task(payload: dict[str, Any]) -> None:
    wa = worker_whatsapp()
    if wa is None:
        log.warning("whatsapp.update_unconfigured")
        return
    outcomes = await handle_whatsapp(wa[0], wa[1], payload)
    log.info("whatsapp.update", outcomes=outcomes)
