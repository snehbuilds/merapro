"""Telegram long polling for local development (M7.16): ``python -m mera.bots.poll``.

For machines without public HTTPS. It removes the webhook, then feeds every update into
the same ``approvals:telegram_update`` job the webhook would, so the handling path is
identical. Production refuses ``TELEGRAM_MODE=polling`` at startup.
"""

from __future__ import annotations

import asyncio
import sys

import structlog

from mera.config import get_settings
from mera.infrastructure.channels.telegram import TelegramChannel
from mera.infrastructure.queue.deferrer import ProcrastinateDeferrer
from mera.observability import configure_logging

log = structlog.get_logger(__name__)


async def run() -> None:
    settings = get_settings()
    if settings.telegram_bot_token is None:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    channel = TelegramChannel(settings.telegram_bot_token.get_secret_value())
    deferrer = ProcrastinateDeferrer(settings.libpq_dsn)
    await channel.delete_webhook()
    me = await channel.me()
    log.info("telegram.polling", bot=me.get("username"))
    offset: int | None = None
    while True:
        try:
            updates = await channel.get_updates(offset)
        except Exception as exc:  # network blips: wait and carry on
            log.warning("telegram.poll_failed", error_type=type(exc).__name__)
            await asyncio.sleep(5)
            continue
        for u in updates:
            offset = int(u["update_id"]) + 1
            await deferrer.defer(
                "approvals:telegram_update",
                queue="approvals",
                queueing_lock=f"tg:{u['update_id']}",
                update=u,
            )


def main() -> None:
    configure_logging(get_settings())
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())


if __name__ == "__main__":
    main()
