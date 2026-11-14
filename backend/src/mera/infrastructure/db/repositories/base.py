"""Workspace-scoped repository base (ADR-0006, layer 1).

Every query built through ``_select`` carries ``workspace_id = ctx.workspace_id``, and every
insert through ``_add`` stamps it. RLS (layer 2) holds even if a subclass bypasses these,
and a test proves that independently by removing this filter.
"""

from __future__ import annotations

from typing import Any, ClassVar, Generic, TypeVar

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from mera.domain.context import WorkspaceContext
from mera.infrastructure.db.base import TenantScoped

M = TypeVar("M", bound=TenantScoped)


class WorkspaceScopedRepository(Generic[M]):
    model: ClassVar[type[Any]]

    def __init__(self, session: AsyncSession, ctx: WorkspaceContext) -> None:
        self.session = session
        self.ctx = ctx

    def _select(self) -> Select[Any]:
        return select(self.model).where(self.model.workspace_id == self.ctx.workspace_id)

    async def _add(self, obj: M) -> M:
        obj.workspace_id = self.ctx.workspace_id
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def list(self) -> list[M]:
        result = await self.session.execute(self._select())
        return list(result.scalars().all())
