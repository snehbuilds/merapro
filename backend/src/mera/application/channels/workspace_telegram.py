"""Encrypted Telegram bot credentials owned by a workspace."""

from __future__ import annotations

import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.domain.context import WorkspaceContext
from mera.infrastructure.crypto.envelope import LocalKeyProvider
from mera.infrastructure.db.models import Workspace
from mera.infrastructure.db.session import workspace_transaction

KEY = "telegram_bot"


async def credentials(
    sm: async_sessionmaker[AsyncSession], key: LocalKeyProvider, workspace_id: uuid.UUID
) -> dict[str, str] | None:
    async with workspace_transaction(sm, WorkspaceContext(workspace_id)) as session:
        workspace = await session.get(Workspace, workspace_id)
        sealed = (workspace.oauth_apps_ct or {}).get(KEY) if workspace else None
    if not sealed:
        return None
    return json.loads(key.decrypt(sealed, workspace_id=workspace_id, purpose=KEY).decode())


def seal(key: LocalKeyProvider, workspace_id: uuid.UUID, data: dict[str, str]) -> str:
    return key.encrypt(json.dumps(data).encode(), workspace_id=workspace_id, purpose=KEY)
