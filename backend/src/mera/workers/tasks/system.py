"""Infrastructure tasks. ``echo`` exists to prove the enqueue → worker → result path (M1
acceptance) and doubles as a liveness probe for a worker pool."""

from __future__ import annotations

import procrastinate
import structlog
from sqlalchemy import text

from mera.config import get_settings
from mera.infrastructure.db.session import create_engine

blueprint = procrastinate.Blueprint()
log = structlog.get_logger(__name__)


@blueprint.task(name="echo", queue="notify", pass_context=True)
async def echo(context: procrastinate.JobContext, message: str) -> str:
    job_id = context.job.id if context.job else None
    log.info("echo", message=message, job_id=job_id)
    return message


# Operational tables that grow with traffic and have no tenant data (Sprint 3 review M2).
# A full bucket idle for an hour is indistinguishable from no bucket.
PURGE_STATEMENTS = (
    "DELETE FROM rate_limit_bucket WHERE refilled_at < now() - interval '1 hour'",
    "DELETE FROM idempotency_key WHERE expires_at < now()",
    "DELETE FROM oauth_flow WHERE expires_at < now() - interval '1 hour'",
    "DELETE FROM email_verification WHERE expires_at < now() - interval '7 days'",
)


async def purge(dsn_url: str | None = None) -> dict[str, int]:
    engine = create_engine(get_settings(), url=dsn_url)
    counts: dict[str, int] = {}
    try:
        async with engine.begin() as conn:
            for stmt in PURGE_STATEMENTS:
                result = await conn.execute(text(stmt))
                counts[stmt.split()[2]] = result.rowcount or 0
    finally:
        await engine.dispose()
    log.info("purge_expired", **counts)
    return counts


@blueprint.periodic(cron="*/10 * * * *")
@blueprint.task(name="purge_expired", queue="notify", queueing_lock="purge_expired")
async def purge_expired(timestamp: int) -> None:
    await purge()


async def ensure_audit_partitions(dsn_url: str | None = None, months_ahead: int = 2) -> int:
    """Create the coming months' audit_log partitions with their RLS (migration 0013)."""
    engine = create_engine(get_settings(), url=dsn_url)
    try:
        async with engine.begin() as conn:
            created = (
                await conn.execute(
                    text("SELECT mera_ensure_audit_partitions(:m)"), {"m": months_ahead}
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    log.info("audit_partitions", created=created)
    return int(created)


# Daily, well ahead of each month boundary: two months are kept ready.
@blueprint.periodic(cron="17 3 * * *")
@blueprint.task(name="audit_partitions", queue="notify", queueing_lock="audit_partitions")
async def audit_partitions(timestamp: int) -> None:
    await ensure_audit_partitions()
