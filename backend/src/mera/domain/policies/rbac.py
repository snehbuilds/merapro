"""The single authorization check (M2.5, M2.9; docs/04 §2.5-2.6, docs/09 §5.2).

``require(principal, permission, workspace_id, social_account_id=..., account_group_id=...)``
is called at the service layer by every surface. Effective permission is
``role AND token_scopes AND member_scope`` (an intersection); each factor can only narrow.

Member-scope rules (fail closed):
- Account-scoped permissions with a subject: the subject must fall inside the scope.
- Account-scoped permissions without a subject: only ``READ`` is allowed (list endpoints
  filter to the scope); anything else is denied, because a workspace-wide action by a
  scoped member would act outside their groups.
- Workspace-level permissions (members, accounts, MCP, autopilot, workspace lifecycle):
  denied to any scope-restricted principal.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Final

from mera.domain.errors import PermissionDenied
from mera.domain.principal import Principal
from mera.domain.value_objects.role import Role


class Permission(StrEnum):
    READ = "read"
    EDIT_POSTS = "edit_posts"
    UPLOAD_MEDIA = "upload_media"
    APPROVE = "approve"
    PUBLISH = "publish"
    REPLY = "reply"
    CONFIGURE_CONTENT = "configure_content"  # sources, brand voice, hashtags, agents
    MANAGE_GROUPS = "manage_groups"
    CONNECT_ACCOUNTS = "connect_accounts"  # social accounts and approval channels
    MANAGE_MCP = "manage_mcp"
    MANAGE_MEMBERS = "manage_members"  # members, scopes, API keys, webhooks
    MANAGE_WORKSPACE = "manage_workspace"  # name, timezone, type
    ENGAGE_KILL_SWITCH = "engage_kill_switch"
    CHANGE_AUTOPILOT = "change_autopilot"
    READ_AUDIT_LOG = "read_audit_log"
    ARCHIVE_WORKSPACE = "archive_workspace"
    DELETE_WORKSPACE = "delete_workspace"  # also clone


# docs/04 §2.6: the minimum role for each capability. The table is the ceiling.
MIN_ROLE: Final[dict[Permission, Role]] = {
    Permission.READ: Role.VIEWER,
    Permission.APPROVE: Role.APPROVER,
    Permission.ENGAGE_KILL_SWITCH: Role.APPROVER,  # a stop is always safe
    Permission.EDIT_POSTS: Role.EDITOR,
    Permission.UPLOAD_MEDIA: Role.EDITOR,
    Permission.PUBLISH: Role.EDITOR,
    Permission.REPLY: Role.EDITOR,
    Permission.CONFIGURE_CONTENT: Role.EDITOR,
    Permission.MANAGE_GROUPS: Role.EDITOR,
    Permission.CONNECT_ACCOUNTS: Role.ADMIN,
    Permission.MANAGE_MCP: Role.ADMIN,
    Permission.MANAGE_MEMBERS: Role.ADMIN,
    Permission.MANAGE_WORKSPACE: Role.ADMIN,
    Permission.CHANGE_AUTOPILOT: Role.ADMIN,
    Permission.READ_AUDIT_LOG: Role.ADMIN,
    Permission.ARCHIVE_WORKSPACE: Role.ADMIN,
    Permission.DELETE_WORKSPACE: Role.OWNER,
}

# Permissions that act on account or group data, and so are narrowed by member scope.
ACCOUNT_SCOPED: Final[frozenset[Permission]] = frozenset(
    {
        Permission.READ,
        Permission.EDIT_POSTS,
        Permission.UPLOAD_MEDIA,
        Permission.APPROVE,
        Permission.PUBLISH,
        Permission.REPLY,
        Permission.CONFIGURE_CONTENT,
        Permission.MANAGE_GROUPS,
        Permission.ENGAGE_KILL_SWITCH,
    }
)

# docs/04 §2.5: what each API-key scope can grant (still capped by the creator's role).
SCOPE_GRANTS: Final[dict[str, frozenset[Permission]]] = {
    "sources:read": frozenset({Permission.READ}),
    "sources:write": frozenset({Permission.CONFIGURE_CONTENT}),
    "content:read": frozenset({Permission.READ}),
    "accounts:read": frozenset({Permission.READ}),
    "accounts:write": frozenset({Permission.CONNECT_ACCOUNTS}),
    "groups:read": frozenset({Permission.READ}),
    "groups:write": frozenset({Permission.MANAGE_GROUPS}),
    "drafts:read": frozenset({Permission.READ}),
    "drafts:write": frozenset({Permission.EDIT_POSTS}),
    "media:write": frozenset({Permission.UPLOAD_MEDIA}),
    "approvals:read": frozenset({Permission.READ}),
    "approvals:decide": frozenset({Permission.APPROVE}),
    "publishing:read": frozenset({Permission.READ}),
    "publishing:write": frozenset({Permission.PUBLISH}),
    "engagement:read": frozenset({Permission.READ}),
    "engagement:write": frozenset({Permission.REPLY}),
    "analytics:read": frozenset({Permission.READ}),
    "automation:write": frozenset({Permission.ENGAGE_KILL_SWITCH, Permission.CHANGE_AUTOPILOT}),
    "agents:read": frozenset({Permission.READ}),
    "agents:write": frozenset({Permission.CONFIGURE_CONTENT}),
    "admin": frozenset(
        {
            Permission.READ,
            Permission.MANAGE_MEMBERS,
            Permission.MANAGE_WORKSPACE,
            Permission.CONNECT_ACCOUNTS,
            Permission.MANAGE_MCP,
            Permission.READ_AUDIT_LOG,
        }
    ),
}

KNOWN_SCOPES: Final[frozenset[str]] = frozenset(SCOPE_GRANTS)


def role_allows(role: Role, permission: Permission) -> bool:
    return role.at_least(MIN_ROLE[permission])


def scopes_allow(scopes: frozenset[str], permission: Permission) -> bool:
    return any(permission in SCOPE_GRANTS.get(s, frozenset()) for s in scopes)


def require(
    principal: Principal,
    permission: Permission,
    workspace_id: uuid.UUID,
    *,
    social_account_id: uuid.UUID | None = None,
    account_group_id: uuid.UUID | None = None,
) -> None:
    """Raise ``PermissionDenied`` unless every factor allows the action."""
    reason = _denial_reason(
        principal,
        permission,
        workspace_id,
        social_account_id=social_account_id,
        account_group_id=account_group_id,
    )
    if reason is not None:
        raise PermissionDenied(reason, permission=permission.value)


def _denial_reason(
    principal: Principal,
    permission: Permission,
    workspace_id: uuid.UUID,
    *,
    social_account_id: uuid.UUID | None,
    account_group_id: uuid.UUID | None,
) -> str | None:
    if principal.workspace_id is None or principal.workspace_id != workspace_id:
        return "principal is not bound to this workspace"
    if principal.role is None:
        return "principal has no role in this workspace"
    if not role_allows(principal.role, permission):
        return f"role {principal.role} below {MIN_ROLE[permission]} for {permission}"
    if principal.token_scopes is not None and not scopes_allow(principal.token_scopes, permission):
        return f"token scopes do not grant {permission}"

    scope = principal.member_scope
    if not scope.restricted:
        return None
    if permission not in ACCOUNT_SCOPED:
        return f"{permission} is workspace-level; principal is scope-restricted"
    if social_account_id is None and account_group_id is None:
        if permission is Permission.READ:
            return None  # list reads are filtered to the scope by the caller
        return f"{permission} without a subject is outside a restricted scope"
    if not scope.covers(social_account_id=social_account_id, account_group_id=account_group_id):
        return "subject is outside the principal's member scope"
    return None


def allowed(
    principal: Principal,
    permission: Permission,
    workspace_id: uuid.UUID,
    *,
    social_account_id: uuid.UUID | None = None,
    account_group_id: uuid.UUID | None = None,
) -> bool:
    try:
        require(
            principal,
            permission,
            workspace_id,
            social_account_id=social_account_id,
            account_group_id=account_group_id,
        )
    except PermissionDenied:
        return False
    return True
