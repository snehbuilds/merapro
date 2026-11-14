"""Bind an authenticated user to a workspace (M2.4).

Runs inside the caller's workspace transaction, so the role used for the permission check
is the role at the moment of the action, never a cached claim.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application.context import AuthenticatedUser
from mera.application.errors import WorkspaceNotFound
from mera.domain.principal import MemberScopeSet, Principal, PrincipalKind
from mera.domain.value_objects.role import Role
from mera.infrastructure.db.models import MemberScope, Workspace, WorkspaceMember


def acting_user(u: Any, *, mfa_enforced: bool) -> AuthenticatedUser:
    """A person acting through a chat or a worker, built from their stored row rather than
    a token. The same MFA cap applies as for a signed-in request (P2-M2.3), so a chat is no
    way round enrolment (release review #13)."""
    return AuthenticatedUser(
        user_id=u.id,
        email=u.email,
        display_name=u.display_name,
        mfa_missing=mfa_enforced and u.mfa_enabled_at is None,
    )


async def load_user_principal(
    session: AsyncSession, user: AuthenticatedUser, workspace_id: uuid.UUID
) -> Principal:
    member = (
        await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user.user_id,
            )
        )
    ).scalar_one_or_none()
    workspace = (
        await session.execute(
            select(Workspace).where(Workspace.id == workspace_id, Workspace.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    key = user.api_key
    if member is None or workspace is None or (key and key.workspace_id != workspace_id):
        # Not a member, a key for another workspace, and does not exist all look the same
        # (docs/04 §3.1).
        raise WorkspaceNotFound("Workspace not found.")

    scope = MemberScopeSet()
    if member.scope_restricted:
        rows = (
            await session.execute(
                select(MemberScope.scope_type, MemberScope.scope_id).where(
                    MemberScope.workspace_member_id == member.id
                )
            )
        ).all()
        scope = MemberScopeSet(
            restricted=True,
            group_ids=frozenset(sid for kind, sid in rows if kind == "account_group"),
            account_ids=frozenset(sid for kind, sid in rows if kind == "social_account"),
        )

    role = Role(member.role)
    if user.mfa_missing and role >= Role.ADMIN:
        # MFA is enforced for Admins and Owners (P2-M2.3): until they enrol, they keep an
        # Editor's reach and nothing above it.
        role = Role.EDITOR
    return Principal(
        kind=PrincipalKind.API_KEY if key else PrincipalKind.USER,
        user_id=user.user_id,
        workspace_id=workspace_id,
        role=role,
        # A key is capped by its creator's *current* role and scope, and by its own scopes.
        token_scopes=key.scopes if key else None,
        member_scope=scope,
        label=user.display_name,
    )
