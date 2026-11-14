"""External MCP tool calls (P2-M9): run allowed calls, expire the ones nobody decided."""

from __future__ import annotations

import uuid

import procrastinate
import structlog
from sqlalchemy import select

from mera.application.services.mcp_registry_service import McpRegistryService
from mera.config import get_settings
from mera.infrastructure.crypto.envelope import LocalKeyProvider
from mera.infrastructure.db.models import Workspace
from mera.infrastructure.db.session import (
    create_engine,
    create_sessionmaker,
    system_scan_transaction,
)
from mera.infrastructure.queue.deferrer import ProcrastinateDeferrer

log = structlog.get_logger(__name__)
blueprint = procrastinate.Blueprint()
_svc: McpRegistryService | None = None


def worker_registry() -> McpRegistryService:
    global _svc
    if _svc is None:
        settings = get_settings()
        _svc = McpRegistryService(
            create_sessionmaker(create_engine(settings)),
            settings,
            LocalKeyProvider.from_base64(settings.mera_master_key.get_secret_value()),
            ProcrastinateDeferrer(settings.libpq_dsn),
        )
    return _svc


@blueprint.task(name="execute_call", queue="integrations")
async def execute_call_task(workspace_id: str, call_id: str) -> None:
    out = await worker_registry().execute_call(uuid.UUID(workspace_id), uuid.UUID(call_id))
    log.info("mcp.call", call=call_id, state=out.state)


@blueprint.periodic(cron="* * * * *")
@blueprint.task(name="expire_calls", queue="integrations", queueing_lock="mcp_expire")
async def expire_calls_task(timestamp: int) -> None:
    """Expiry never executes: an undecided call becomes ``expired`` and the agent gets an error."""
    svc = worker_registry()
    async with system_scan_transaction(svc._sm) as s:
        ws_ids = list(
            (await s.execute(select(Workspace.id).where(Workspace.deleted_at.is_(None)))).scalars()
        )
    n = 0
    for ws in ws_ids:
        n += await svc.expire_calls(ws)
    if n:
        log.info("mcp.calls_expired", count=n)
