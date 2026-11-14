"""Shared plumbing for services that act inside one workspace.

``_tx`` opens the workspace transaction. Two failures are audited *after* that transaction
has rolled back and released its connection, so a request never holds one pooled
connection while waiting for another:

- ``PermissionDenied``: recorded in the workspace's own log.
- ``WorkspaceNotFound``: a caller probing a workspace it does not belong to. Recorded as a
  workspace-less security row (``workspace_id`` NULL, the probed id as subject), so the
  probed tenant's log is not polluted and the prober learns nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import ConflictError, Forbidden, WorkspaceNotFound
from mera.domain.context import WorkspaceContext
from mera.domain.errors import PermissionDenied
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.domain.principal import Principal
from mera.infrastructure.db.models import Workspace
from mera.infrastructure.db.rate_limit import Limit, take
from mera.infrastructure.db.repositories.audit import AuditEntry, record
from mera.infrastructure.db.session import global_transaction, workspace_transaction

log = structlog.get_logger(__name__)
AUDIT_FLOOD_LIMIT = Limit(capacity=30, per_seconds=60)


class WorkspaceScopedService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    @asynccontextmanager
    async def _tx(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> AsyncIterator[AsyncSession]:
        try:
            async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
                yield s
        except PermissionDenied as exc:
            if not await self._within_audit_budget("permission.denied", user):
                raise
            async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "permission.denied",
                        workspace_id=workspace_id,
                        after={"permission": exc.permission, "reason": exc.reason},
                    ),
                )
            raise
        except WorkspaceNotFound:
            if not await self._within_audit_budget("security.workspace_probe", user):
                raise
            async with global_transaction(self._sm) as s:
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "security.workspace_probe",
                        workspace_id=None,
                        subject_type="workspace",
                        subject_id=workspace_id,
                    ),
                )
            raise

    async def _within_audit_budget(self, action: str, user: AuthenticatedUser) -> bool:
        """Denials and probes are audited, but one actor cannot flood the log with them: past
        ``AUDIT_FLOOD_LIMIT`` a minute, further repeats are counted in the application log
        instead (carried forward from the Sprint 3 review)."""
        actor = user.api_key.api_key_id if user.api_key else user.user_id
        try:
            decision = await take(self._sm, f"audit:{action}:{actor}", AUDIT_FLOOD_LIMIT)
        except Exception:  # never let the cap itself hide a denial
            return True
        if not decision.allowed:
            log.warning("audit.suppressed", action=action, actor=str(actor))
        return decision.allowed

    @staticmethod
    def _require_for_account(
        principal: Principal,
        permission: Permission,
        workspace_id: uuid.UUID,
        account_id: uuid.UUID,
        group_ids: list[uuid.UUID],
    ) -> None:
        """An account in several groups is in a member's scope if the account itself, or
        *any* of its groups, is. ``rbac.require`` checks one (account, group) pair."""
        last: PermissionDenied | None = None
        for group_id in [None, *group_ids]:
            try:
                rbac.require(
                    principal,
                    permission,
                    workspace_id,
                    social_account_id=account_id,
                    account_group_id=group_id,
                )
                return
            except PermissionDenied as exc:
                last = exc
        assert last is not None
        raise last

    @staticmethod
    def _require_human(user: AuthenticatedUser) -> None:
        if user.is_api_key:
            raise Forbidden(
                "API keys cannot do this; sign in as a user.", code="user_credentials_required"
            )

    @staticmethod
    def _ensure_writable(workspace: Workspace) -> None:
        """Archived workspaces are read-only (docs/03 §3.2). Shrinking access — revoking a key
        or removing a member — stays allowed, because it is always safe."""
        if workspace.archived_at is not None:
            raise ConflictError(
                "This workspace is archived and read-only.", code="workspace_archived"
            )

    @staticmethod
    def _actor_entry(
        user: AuthenticatedUser,
        meta: RequestMeta,
        action: str,
        *,
        workspace_id: uuid.UUID | None,
        **kw: object,
    ) -> AuditEntry:
        key = user.api_key
        return AuditEntry(
            action=action,
            actor_type="api_key" if key else "user",
            actor_id=key.api_key_id if key else user.user_id,
            actor_label=user.display_name,
            surface=meta.surface,
            workspace_id=workspace_id,
            ip_address=meta.ip_address,
            request_id=meta.request_id,
            **kw,  # type: ignore[arg-type]
        )
