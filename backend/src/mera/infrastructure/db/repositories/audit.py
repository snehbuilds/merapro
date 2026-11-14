"""Append-only audit writer (M2.10, docs/09 §10). There is deliberately no update or delete
method; the database rejects both anyway."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from mera.infrastructure.db.models import AuditLog


@dataclass(frozen=True, slots=True)
class AuditEntry:
    action: str
    actor_type: str
    surface: str
    workspace_id: uuid.UUID | None = None
    actor_id: uuid.UUID | None = None
    actor_label: str | None = None
    subject_type: str | None = None
    subject_id: uuid.UUID | None = None
    social_account_id: uuid.UUID | None = None
    account_group_id: uuid.UUID | None = None
    subject_content_hash: bytes | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    ip_address: str | None = None
    request_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


async def record(session: AsyncSession, entry: AuditEntry) -> None:
    """Plain INSERT with no RETURNING: an INSERT ... RETURNING must also pass the SELECT
    policy, which a workspace-less row (login events) deliberately does not."""
    after = entry.after
    if entry.extra:
        after = {**(after or {}), **entry.extra}
    await session.execute(
        insert(AuditLog).values(
            workspace_id=entry.workspace_id,
            actor_type=entry.actor_type,
            actor_id=entry.actor_id,
            actor_label=entry.actor_label,
            surface=entry.surface,
            action=entry.action,
            subject_type=entry.subject_type,
            subject_id=entry.subject_id,
            social_account_id=entry.social_account_id,
            account_group_id=entry.account_group_id,
            subject_content_hash=entry.subject_content_hash,
            before=entry.before,
            after=after,
            ip_address=entry.ip_address,
            request_id=entry.request_id,
        )
    )


async def list_for_workspace(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    limit: int = 50,
    before_id: int | None = None,
    action: str | None = None,
    actor_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None = None,
    surface: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[AuditLog]:
    """Newest first, keyset-paginated by id. ``action`` matches a prefix (``approval.``)."""
    stmt = (
        select(AuditLog)
        .where(AuditLog.workspace_id == workspace_id)
        .order_by(AuditLog.id.desc())
        .limit(limit)
    )
    if before_id is not None:
        stmt = stmt.where(AuditLog.id < before_id)
    if action:
        stmt = stmt.where(AuditLog.action.startswith(action, autoescape=True))
    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if account_id is not None:
        stmt = stmt.where(AuditLog.social_account_id == account_id)
    if surface:
        stmt = stmt.where(AuditLog.surface == surface)
    if since is not None:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until is not None:
        stmt = stmt.where(AuditLog.created_at < until)
    return list((await session.execute(stmt)).scalars().all())
