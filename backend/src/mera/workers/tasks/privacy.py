"""Governance jobs (P3-M7): security alerts every minute, workspace exports on request,
erasure after the grace period daily, and external MCP token refresh every five minutes."""

from __future__ import annotations

import uuid
from typing import Any

import procrastinate
from sqlalchemy import select

from mera.application.security_alerts import SecurityAlerts
from mera.application.services.privacy_service import PrivacyService
from mera.config import get_settings
from mera.infrastructure.db.models import Workspace
from mera.infrastructure.db.session import (
    create_engine,
    create_sessionmaker,
    system_scan_transaction,
)

blueprint = procrastinate.Blueprint()
_alerts: SecurityAlerts | None = None
_privacy: PrivacyService | None = None


def worker_alerts() -> SecurityAlerts:
    global _alerts
    if _alerts is None:
        from mera.infrastructure.notify import build_notifier

        settings = get_settings()
        _alerts = SecurityAlerts(
            create_sessionmaker(create_engine(settings)), build_notifier(settings)
        )
    return _alerts


def worker_privacy() -> PrivacyService:
    global _privacy
    if _privacy is None:
        from mera.application.media_factory import build_object_store

        settings = get_settings()
        _privacy = PrivacyService(
            create_sessionmaker(create_engine(settings)),
            build_object_store(settings),
            None,
            migration_url=settings.database_migration_url,
        )
    return _privacy


@blueprint.periodic(cron="* * * * *")
@blueprint.task(name="security_alerts", queue="notify", queueing_lock="security_alerts")
async def security_alerts_task(timestamp: int) -> None:
    await worker_alerts().run()


@blueprint.task(name="build_export", queue="notify")
async def build_export_task(workspace_id: str, export_id: str) -> None:
    await worker_privacy().build_export(uuid.UUID(workspace_id), uuid.UUID(export_id))


@blueprint.periodic(cron="10 4 * * *")
@blueprint.task(name="erase_due", queue="notify", queueing_lock="erase_due")
async def erase_due_task(timestamp: int) -> None:
    await worker_privacy().erase_due()


@blueprint.periodic(cron="*/5 * * * *")
@blueprint.task(name="mcp_token_refresh", queue="integrations", queueing_lock="mcp_token_refresh")
async def mcp_token_refresh_task(timestamp: int) -> None:
    from mera.workers.tasks.mcp import worker_registry

    registry = worker_registry()
    async with system_scan_transaction(registry._sm) as s:
        spaces = list(
            (
                await s.execute(
                    select(Workspace.id).where(
                        Workspace.deleted_at.is_(None), Workspace.archived_at.is_(None)
                    )
                )
            ).scalars()
        )
    for ws in spaces:
        await registry.refresh_tokens(ws)


@blueprint.periodic(cron="*/15 * * * *")
@blueprint.task(name="digests", queue="approvals", queueing_lock="digests")
async def digests_task(timestamp: int) -> None:
    """Daily approval digests at each member's local time (P3-M4.1)."""
    global _digests
    if _digests is None:
        from mera.application.digests import Digests
        from mera.infrastructure.notify import build_notifier
        from mera.workers.tasks.approvals import worker_bot, worker_whatsapp

        _, _, bot = worker_bot()
        wa = worker_whatsapp()
        settings = get_settings()
        _digests = Digests(
            create_sessionmaker(create_engine(settings)),
            [bot, wa[0] if wa else None],
            build_notifier(settings),
        )
    await _digests.run()


_digests: Any = None


_hooks: Any = None


@blueprint.periodic(cron="* * * * *")
@blueprint.task(name="webhooks", queue="integrations", queueing_lock="webhooks")
async def webhooks_task(timestamp: int) -> None:
    """Outbound webhooks: new events become deliveries; due ones are sent (P3-M4.3)."""
    global _hooks
    if _hooks is None:
        from mera.application.services.webhook_service import WebhookService
        from mera.infrastructure.crypto.envelope import LocalKeyProvider

        settings = get_settings()
        _hooks = WebhookService(
            create_sessionmaker(create_engine(settings)),
            LocalKeyProvider.from_base64(settings.mera_master_key.get_secret_value()),
            allow_http=settings.sandbox_mode,
        )
    await _hooks.run()


@blueprint.periodic(cron="30 5 * * *")
@blueprint.task(name="evergreen", queue="agents", queueing_lock="evergreen")
async def evergreen_task(timestamp: int) -> None:
    """Daily: evergreen posts past their cool-down come back as fresh drafts (P3-M5.3)."""
    from mera.application.drafting.factory import build_drafting
    from mera.application.services.evergreen_service import EvergreenService
    from mera.application.services.post_service import PostService
    from mera.workers.tasks.approvals import worker_bot

    sm, deferrer, _ = worker_bot()
    settings = get_settings()
    posts = PostService(sm, build_drafting(settings, sm), deferrer)
    await EvergreenService(sm, posts, mfa_enforced=settings.mfa_enforced).run()
