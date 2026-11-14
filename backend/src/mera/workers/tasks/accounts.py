"""The ``accounts`` queue (M3.7, 3.8; docs/01 §2.1): token refresh and health checks.

A periodic sweep enumerates workspaces through the system-scan policy (ADR-0006 addendum
2), then works inside each workspace's own RLS context. It runs on its own queue so health
checks keep going while publish jobs hold per-account locks (docs/01 §2.1). One sweep runs
at a time (queueing lock); accounts are handled one by one, which is ample at the 25-per-
workspace ceiling and keeps the platform-call rate gentle.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import procrastinate
import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.services.account_service import AccountMaintenance, _refresh_due
from mera.config import get_settings
from mera.domain.context import WorkspaceContext
from mera.domain.value_objects.platform import AccountStatus
from mera.infrastructure.crypto.envelope import LocalKeyProvider
from mera.infrastructure.db.models import AccountConnectFlow, SocialAccount, Workspace
from mera.infrastructure.db.session import (
    create_engine,
    create_sessionmaker,
    system_scan_transaction,
    workspace_transaction,
)
from mera.infrastructure.platforms.connectors import build_connectors

blueprint = procrastinate.Blueprint()
log = structlog.get_logger(__name__)

HEALTH_INTERVAL = timedelta(hours=6)


@dataclass(frozen=True, slots=True)
class Due:
    workspace_id: uuid.UUID
    account_id: uuid.UUID
    refresh: bool
    health: bool


async def find_due(sm: async_sessionmaker[AsyncSession], now: datetime | None = None) -> list[Due]:
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
    due: list[Due] = []
    for ws_id in workspace_ids:
        async with workspace_transaction(sm, WorkspaceContext(ws_id)) as s:
            # Connect flows are tenant rows, so the global purge cannot see them.
            await s.execute(
                delete(AccountConnectFlow).where(
                    AccountConnectFlow.expires_at < now - timedelta(hours=1)
                )
            )
            accounts = (
                await s.execute(
                    select(SocialAccount).where(SocialAccount.status == AccountStatus.ACTIVE.value)
                )
            ).scalars()
            for a in accounts:
                refresh = _refresh_due(a, now)
                health = (
                    a.last_health_check_at is None
                    or now - a.last_health_check_at >= HEALTH_INTERVAL
                )
                if refresh or health:
                    due.append(Due(ws_id, a.id, refresh, health))
    return due


async def sweep(
    sm: async_sessionmaker[AsyncSession], maintenance: AccountMaintenance
) -> dict[str, int]:
    outcomes: Counter[str] = Counter()
    for item in await find_due(sm):
        try:
            ws, account = item.workspace_id, item.account_id
            if item.refresh:
                outcomes[f"refresh:{await maintenance.refresh_if_due(ws, account)}"] += 1
            if item.health:
                outcomes[f"health:{await maintenance.check_health(ws, account)}"] += 1
        except Exception as exc:  # one bad account must not stop the sweep for the rest
            log.error(
                "accounts.sweep_failed",
                account_id=str(item.account_id),
                error_type=type(exc).__name__,
            )
            outcomes["error"] += 1
    log.info("accounts.sweep", **outcomes)
    return dict(outcomes)


_deps: tuple[async_sessionmaker[AsyncSession], AccountMaintenance] | None = None


def _worker_deps() -> tuple[async_sessionmaker[AsyncSession], AccountMaintenance]:
    global _deps
    if _deps is None:
        settings = get_settings()
        sm = create_sessionmaker(create_engine(settings))
        maintenance = AccountMaintenance(
            sm,
            build_connectors(settings),
            LocalKeyProvider.from_base64(settings.mera_master_key.get_secret_value()),
        )
        _deps = (sm, maintenance)
    return _deps


@blueprint.periodic(cron="*/5 * * * *")
@blueprint.task(name="sweep", queue="accounts", queueing_lock="accounts_sweep")
async def sweep_task(timestamp: int) -> None:
    sm, maintenance = _worker_deps()
    await sweep(sm, maintenance)
