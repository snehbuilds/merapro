"""Who is acting (docs/04 §2, docs/09 §5.2).

Every credential type (JWT user, API key, later MCP token and bot binding) resolves to one
``Principal``. A principal is always bound to at most one workspace: authorization never
reasons across workspaces.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from mera.domain.value_objects.role import Role


class PrincipalKind(StrEnum):
    USER = "user"
    API_KEY = "api_key"


@dataclass(frozen=True, slots=True)
class MemberScopeSet:
    """``restricted=False`` means the whole workspace (the default, docs/03 §3.1)."""

    restricted: bool = False
    group_ids: frozenset[uuid.UUID] = frozenset()
    account_ids: frozenset[uuid.UUID] = frozenset()

    def covers(
        self, *, social_account_id: uuid.UUID | None, account_group_id: uuid.UUID | None
    ) -> bool:
        if not self.restricted:
            return True
        if account_group_id is not None and account_group_id in self.group_ids:
            return True
        return social_account_id is not None and social_account_id in self.account_ids


@dataclass(frozen=True, slots=True)
class Principal:
    kind: PrincipalKind
    user_id: uuid.UUID
    workspace_id: uuid.UUID | None = None
    role: Role | None = None
    # None for users (role-based); the key's scope list for API keys.
    token_scopes: frozenset[str] | None = None
    member_scope: MemberScopeSet = field(default_factory=MemberScopeSet)
    label: str | None = None
