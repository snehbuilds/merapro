"""Domain events (docs/04 §5): one row in ``event`` per state change worth telling a client.

The table is the SSE stream's source and its replay log (``Last-Event-ID``). Events are
written in the same transaction as the change they describe, so a client never hears about
something that rolled back. Every event about a post carries the account and group, so a
client watching one group can filter without a lookup.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mera.infrastructure.db.models import Event


async def emit(
    s: AsyncSession,
    workspace_id: uuid.UUID,
    event_type: str,
    *,
    subject_type: str | None = None,
    subject_id: uuid.UUID | None = None,
    social_account_id: uuid.UUID | None = None,
    account_group_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    body = {
        "workspace_id": str(workspace_id),
        **({"social_account_id": str(social_account_id)} if social_account_id else {}),
        **({"account_group_id": str(account_group_id)} if account_group_id else {}),
        **(payload or {}),
    }
    s.add(
        Event(
            workspace_id=workspace_id,
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            social_account_id=social_account_id,
            account_group_id=account_group_id,
            payload=body,
        )
    )
