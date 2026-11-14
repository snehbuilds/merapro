from __future__ import annotations

import uuid

from mera.domain.value_objects.role import Role
from mera.infrastructure.db.models import WorkspaceMember
from mera.infrastructure.db.repositories.base import WorkspaceScopedRepository


class WorkspaceMemberRepository(WorkspaceScopedRepository[WorkspaceMember]):
    model = WorkspaceMember

    async def add(self, user_id: uuid.UUID, role: Role) -> WorkspaceMember:
        return await self._add(WorkspaceMember(user_id=user_id, role=role.value))

    async def get_by_user(self, user_id: uuid.UUID) -> WorkspaceMember | None:
        result = await self.session.execute(
            self._select().where(WorkspaceMember.user_id == user_id)
        )
        member: WorkspaceMember | None = result.scalar_one_or_none()
        return member
