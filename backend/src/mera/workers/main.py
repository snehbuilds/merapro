"""Worker entry point: ``python -m mera.workers.main --queues publish,accounts``.

Queue selection from argv lets one image serve every pool (docs/01 §2.1). SIGTERM drains:
procrastinate finishes the in-flight job before exiting.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import structlog

from mera.config import get_settings
from mera.infrastructure.db.session import assert_rls_bound, create_engine, ensure_app_role
from mera.infrastructure.queue.app import QUEUES, build_app
from mera.observability import configure_logging, configure_tracing
from mera.workers.health import HEARTBEAT_PATH, heartbeat

log = structlog.get_logger(__name__)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="mera-worker")
    parser.add_argument(
        "--queues",
        default=",".join(QUEUES),
        help=f"comma-separated subset of: {', '.join(QUEUES)}",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args(argv)
    queues = [q.strip() for q in args.queues.split(",") if q.strip()]
    unknown = sorted(set(queues) - set(QUEUES))
    if unknown:
        parser.error(f"unknown queue(s): {', '.join(unknown)}")
    args.queue_list = queues
    return args


async def _run(queues: list[str], concurrency: int) -> None:
    settings = get_settings()
    await ensure_app_role(settings)
    engine = create_engine(settings)
    try:
        await assert_rls_bound(engine)  # workers hold the same RLS obligations as the API
    finally:
        await engine.dispose()
    app = build_app(settings.libpq_dsn)
    beat = asyncio.create_task(heartbeat(HEARTBEAT_PATH))
    try:
        async with app.open_async():
            log.info("worker.start", queues=queues, concurrency=concurrency)
            await app.run_worker_async(queues=queues, concurrency=concurrency)
    finally:
        beat.cancel()


def main(argv: list[str] | None = None) -> None:
    args = _parse(argv)
    settings = get_settings()
    configure_logging(settings)
    configure_tracing(settings, "mera-worker")
    if sys.platform == "win32":  # local dev only; psycopg async needs a selector loop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(_run(args.queue_list, args.concurrency))


if __name__ == "__main__":
    main()
