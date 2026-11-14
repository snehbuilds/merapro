"""Workspaces and memberships (M2.8): create, list, get, update, archive, delete, members.

Each method opens its transaction through ``_tx`` (see ``base``), binds the principal inside
it, calls ``rbac.require`` and only then acts.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application import autopilot, second_factor
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import ConflictError, InvalidInput, NotFoundError
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.application.settings_model import LevelSettings, merged
from mera.domain.context import WorkspaceContext
from mera.domain.errors import PermissionDenied
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.domain.principal import Principal
from mera.domain.value_objects.ids import uuid7
from mera.domain.value_objects.role import Role
from mera.domain.value_objects.workspace import WorkspaceType
from mera.infrastructure.db.errors import constraint_name
from mera.infrastructure.db.models import (
    AccountGroup,
    AccountGroupMember,
    ApiKey,
    MemberScope,
    Notification,
    SocialAccount,
    User,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMember,
)
from mera.infrastructure.db.repositories.audit import list_for_workspace as list_audit
from mera.infrastructure.db.repositories.audit import record
from mera.infrastructure.db.session import user_transaction, workspace_transaction

_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")


@dataclass(frozen=True, slots=True)
class WorkspaceView:
    id: uuid.UUID
    name: str
    slug: str
    workspace_type: str
    timezone: str
    role: str
    archived: bool = False
    settings: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class MemberView:
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    scope_restricted: bool
    joined_at: datetime | None
    away_until: datetime | None = None
    delegate_user_id: uuid.UUID | None = None


class WorkspaceService(WorkspaceScopedService):
    async def create(
        self,
        user: AuthenticatedUser,
        *,
        name: str,
        slug: str,
        workspace_type: WorkspaceType,
        timezone: str,
        meta: RequestMeta,
    ) -> WorkspaceView:
        self._require_human(user)
        if not _SLUG.match(slug):
            raise InvalidInput(
                "Use 1-48 lowercase letters, digits and hyphens.", code="invalid_slug", field="slug"
            )
        timezone = canonicalize_timezone(timezone)
        ws_id = uuid7()
        try:
            # Creating a tenant: the context is the new id, so RLS WITH CHECK admits it.
            async with workspace_transaction(self._sm, WorkspaceContext(ws_id)) as s:
                s.add(
                    Workspace(
                        id=ws_id,
                        name=name.strip(),
                        slug=slug,
                        workspace_type=workspace_type.value,
                        timezone=timezone,
                    )
                )
                await s.flush()
                s.add(
                    WorkspaceMember(
                        workspace_id=ws_id,
                        user_id=user.user_id,
                        role=Role.OWNER.value,
                        joined_at=datetime.now(UTC),
                    )
                )
                await s.flush()
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "workspace.created",
                        workspace_id=ws_id,
                        subject_type="workspace",
                        subject_id=ws_id,
                        after={"name": name, "slug": slug, "workspace_type": workspace_type.value},
                    ),
                )
        except IntegrityError as exc:
            if constraint_name(exc) != "uq_workspace_slug":
                raise
            raise ConflictError("That slug is taken.", code="slug_taken", field="slug") from exc
        return WorkspaceView(
            ws_id, name.strip(), slug, workspace_type.value, timezone, Role.OWNER.value
        )

    async def list_for_user(self, user: AuthenticatedUser) -> list[WorkspaceView]:
        async with user_transaction(self._sm, user.user_id) as s:
            stmt = (
                select(Workspace, WorkspaceMember.role)
                .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                .where(WorkspaceMember.user_id == user.user_id, Workspace.deleted_at.is_(None))
                .order_by(Workspace.name)
            )
            if user.api_key is not None:  # a key sees only its own workspace
                stmt = stmt.where(Workspace.id == user.api_key.workspace_id)
            rows = (await s.execute(stmt)).all()
        return [_view(ws, role) for ws, role in rows]

    async def get(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> WorkspaceView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            ws = await _workspace(s, workspace_id)
            role = principal.role.value if principal.role else ""
        return _view(ws, role)

    async def second_factor_policy(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> dict[str, bool]:
        """Which high-risk actions ask for a second factor here (all, by default)."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            ws = await _workspace(s, workspace_id)
            return {a: second_factor.required(ws, a) for a in second_factor.ACTIONS}

    async def set_second_factor_policy(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        changes: dict[str, bool],
    ) -> dict[str, bool]:
        """Admins may switch a protection on; only an Owner may switch one off, and the
        change is audited."""
        self._require_human(user)
        unknown = set(changes) - set(second_factor.ACTIONS)
        if unknown:
            raise InvalidInput(
                f"Unknown action: {', '.join(sorted(unknown))}.", code="invalid_action"
            )
        async with self._tx(user, workspace_id, meta) as s:
            ws = await _workspace(s, workspace_id, lock=True)
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.MANAGE_WORKSPACE, workspace_id)
            self._ensure_writable(ws)
            before = {a: second_factor.required(ws, a) for a in second_factor.ACTIONS}
            turning_off = [a for a, on in changes.items() if not on and before[a]]
            if turning_off and principal.role is not Role.OWNER:
                raise PermissionDenied(
                    "only an owner can switch a second factor off",
                    permission=Permission.MANAGE_WORKSPACE.value,
                )
            settings = dict(ws.settings or {})
            toggles = dict(settings.get("second_factor") or {})
            toggles.update(changes)
            settings["second_factor"] = toggles
            ws.settings = settings
            after = {a: second_factor.required(ws, a) for a in second_factor.ACTIONS}
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "workspace.second_factor_policy",
                    workspace_id=workspace_id,
                    subject_type="workspace",
                    subject_id=workspace_id,
                    before=before,
                    after=after,
                ),
            )
            return after

    async def update(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        name: str | None,
        timezone: str | None,
        workspace_type: WorkspaceType | None,
        settings: LevelSettings | None = None,
    ) -> WorkspaceView:
        if timezone is not None:
            timezone = canonicalize_timezone(timezone)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.MANAGE_WORKSPACE, workspace_id)
            ws = await _workspace(s, workspace_id, lock=True)
            self._ensure_writable(ws)
            before = {
                "name": ws.name,
                "timezone": ws.timezone,
                "workspace_type": ws.workspace_type,
                "settings": ws.settings,
            }
            if name is not None:
                ws.name = name.strip()
            if timezone is not None:
                ws.timezone = timezone
            if workspace_type is not None:
                ws.workspace_type = workspace_type.value
            if settings is not None:
                new_settings = merged(ws.settings, settings)
                await autopilot.guard_change(
                    s,
                    user,
                    principal,
                    workspace_id,
                    meta,
                    before=ws.settings,
                    after=new_settings,
                    scope_type="workspace",
                    scope_id=workspace_id,
                    accounts=await autopilot.scope_accounts(s, "workspace", None),
                )
                ws.settings = new_settings
            after = {
                "name": ws.name,
                "timezone": ws.timezone,
                "workspace_type": ws.workspace_type,
                "settings": ws.settings,
            }
            if before != after:
                await s.flush()
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "workspace.updated",
                        workspace_id=workspace_id,
                        subject_type="workspace",
                        subject_id=workspace_id,
                        before=before,
                        after=after,
                    ),
                )
            role = principal.role.value if principal.role else ""
        return _view(ws, role)

    async def set_archived(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta, *, archived: bool
    ) -> WorkspaceView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.ARCHIVE_WORKSPACE, workspace_id)
            ws = await _workspace(s, workspace_id, lock=True)
            if (ws.archived_at is not None) != archived:
                ws.archived_at = datetime.now(UTC) if archived else None
                await s.flush()
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "workspace.archived" if archived else "workspace.unarchived",
                        workspace_id=workspace_id,
                        subject_type="workspace",
                        subject_id=workspace_id,
                    ),
                )
            role = principal.role.value if principal.role else ""
        return _view(ws, role)

    async def delete(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> None:
        """Soft delete; the erasure walk after the grace period is docs/09 §9.4 (later).
        A second factor joins this in Phase 2 (docs/09 §5.3)."""
        self._require_human(user)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.DELETE_WORKSPACE, workspace_id)
            ws = await _workspace(s, workspace_id, lock=True)
            ws.deleted_at = datetime.now(UTC)
            await s.flush()
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "workspace.deleted",
                    workspace_id=workspace_id,
                    subject_type="workspace",
                    subject_id=workspace_id,
                ),
            )

    async def list_members(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> list[MemberView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            rows = (
                await s.execute(
                    select(WorkspaceMember, User)
                    .join(User, User.id == WorkspaceMember.user_id)
                    .where(WorkspaceMember.workspace_id == workspace_id)
                    .order_by(User.display_name)
                )
            ).all()
        return [
            MemberView(
                u.id,
                u.email,
                u.display_name,
                m.role,
                m.scope_restricted,
                m.joined_at,
                m.away_until,
                m.delegate_user_id,
            )
            for m, u in rows
        ]

    async def set_away(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        target_user_id: uuid.UUID,
        meta: RequestMeta,
        *,
        away_until: datetime | None,
        delegate_user_id: uuid.UUID | None,
    ) -> MemberView:
        """Out of office (P2-M2.8): while away, no new approval cards reach this person,
        and wherever a policy names them as an approver, the delegate decides instead. A
        member sets their own; an Admin can set anyone's."""
        self._require_human(user)
        now = datetime.now(UTC)
        if away_until is not None and (away_until <= now or away_until > now + timedelta(days=90)):
            raise InvalidInput(
                "Pick a return date within the next 90 days.",
                code="invalid_away",
                field="away_until",
            )
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            if target_user_id != user.user_id:
                rbac.require(principal, Permission.MANAGE_MEMBERS, workspace_id)
            member, target = await _member(s, workspace_id, target_user_id)
            if delegate_user_id is not None:
                if delegate_user_id == target_user_id:
                    raise InvalidInput(
                        "Choose someone else to cover.",
                        code="invalid_delegate",
                        field="delegate_user_id",
                    )
                delegate, _ = await _member(s, workspace_id, delegate_user_id)
                if delegate.role not in ("owner", "admin", "editor", "approver"):
                    raise InvalidInput(
                        "The delegate must be able to approve.",
                        code="invalid_delegate",
                        field="delegate_user_id",
                    )
            member.away_until = away_until
            member.delegate_user_id = delegate_user_id if away_until is not None else None
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "member.away",
                    workspace_id=workspace_id,
                    subject_type="user",
                    subject_id=target_user_id,
                    after={
                        "away_until": away_until.isoformat() if away_until else None,
                        "delegate_user_id": str(delegate_user_id) if delegate_user_id else None,
                    },
                ),
            )
            return MemberView(
                target.id,
                target.email,
                target.display_name,
                member.role,
                member.scope_restricted,
                member.joined_at,
                member.away_until,
                member.delegate_user_id,
            )

    async def change_role(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        target_user_id: uuid.UUID,
        new_role: Role,
        meta: RequestMeta,
    ) -> MemberView:
        self._require_human(user)  # keys never change who holds power (review H2)
        async with self._tx(user, workspace_id, meta) as s:
            # Serialise every membership change in this workspace: without it, two owners
            # demoting each other concurrently both see two owners and leave none (write
            # skew). The caller's own role is read after the lock, so it cannot be stale.
            ws = await _workspace(s, workspace_id, lock=True)
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.MANAGE_MEMBERS, workspace_id)
            self._ensure_writable(ws)
            member, target = await _member(s, workspace_id, target_user_id)
            old_role = Role(member.role)
            if Role.OWNER in (old_role, new_role) and principal.role is not Role.OWNER:
                raise PermissionDenied(
                    "only an owner can change ownership",
                    permission=Permission.MANAGE_MEMBERS.value,
                )
            if old_role is Role.OWNER and new_role is not Role.OWNER:
                await _ensure_another_owner(s, workspace_id)
            if old_role is not new_role:
                member.role = new_role.value
                # Authority they granted while holding the old role must not outlive it.
                await _revoke_pending_invitations(s, workspace_id, target_user_id)
                await s.flush()
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "member.role_changed",
                        workspace_id=workspace_id,
                        subject_type="user",
                        subject_id=target_user_id,
                        before={"role": old_role.value},
                        after={"role": new_role.value},
                    ),
                )
        return MemberView(
            target.id,
            target.email,
            target.display_name,
            new_role.value,
            member.scope_restricted,
            member.joined_at,
        )

    async def remove_member(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        target_user_id: uuid.UUID,
        meta: RequestMeta,
    ) -> None:
        """Removing a member also disables every API key they created: a key resolves its
        role from its creator's membership, which no longer exists."""
        self._require_human(user)
        async with self._tx(user, workspace_id, meta) as s:
            await _workspace(s, workspace_id, lock=True)
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.MANAGE_MEMBERS, workspace_id)
            member, _ = await _member(s, workspace_id, target_user_id)
            if member.role == Role.OWNER.value:
                if principal.role is not Role.OWNER:
                    raise PermissionDenied(
                        "only an owner can remove an owner",
                        permission=Permission.MANAGE_MEMBERS.value,
                    )
                await _ensure_another_owner(s, workspace_id)
                await second_factor.require(s, user, workspace_id, "owner_remove", meta)
            await s.delete(member)
            # Revoke explicitly: if they were re-added later, their old (possibly leaked)
            # keys must not silently come back to life (review M4).
            now = datetime.now(UTC)
            await s.execute(
                update(ApiKey)
                .where(
                    ApiKey.workspace_id == workspace_id,
                    ApiKey.created_by == target_user_id,
                    ApiKey.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            await _revoke_pending_invitations(s, workspace_id, target_user_id)
            await s.flush()
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "member.removed",
                    workspace_id=workspace_id,
                    subject_type="user",
                    subject_id=target_user_id,
                    before={"role": member.role},
                ),
            )

    # ---- member scopes (M2.9, moved to Sprint 4 so ids can be validated) ----

    async def get_scopes(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        target_user_id: uuid.UUID,
        meta: RequestMeta,
    ) -> MemberScopes:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            if target_user_id != user.user_id:
                rbac.require(principal, Permission.MANAGE_MEMBERS, workspace_id)
            member, _ = await _member(s, workspace_id, target_user_id)
            return await _scopes(s, member)

    async def set_scopes(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        target_user_id: uuid.UUID,
        meta: RequestMeta,
        *,
        account_group_ids: list[uuid.UUID],
        social_account_ids: list[uuid.UUID],
    ) -> MemberScopes:
        """Narrow a member to named groups and accounts; empty lists mean the whole
        workspace. Scopes only ever narrow a role (docs/03 §3.1)."""
        self._require_human(user)
        async with self._tx(user, workspace_id, meta) as s:
            await _workspace(s, workspace_id, lock=True)
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.MANAGE_MEMBERS, workspace_id)
            member, _ = await _member(s, workspace_id, target_user_id)
            if member.role == Role.OWNER.value:
                raise InvalidInput(
                    "Owners always hold the whole workspace.", code="invalid_scope_target"
                )
            groups = set(account_group_ids)
            accounts = set(social_account_ids)
            known_groups = set(
                (
                    await s.execute(select(AccountGroup.id).where(AccountGroup.id.in_(groups)))
                ).scalars()
            )
            known_accounts = set(
                (
                    await s.execute(select(SocialAccount.id).where(SocialAccount.id.in_(accounts)))
                ).scalars()
            )
            if known_groups != groups or known_accounts != accounts:
                raise InvalidInput(
                    "Unknown group or account.", code="unknown_scope_subject", field="scopes"
                )
            before = await _scopes(s, member)
            await s.execute(delete(MemberScope).where(MemberScope.workspace_member_id == member.id))
            for kind, ids in (("account_group", groups), ("social_account", accounts)):
                for scope_id in ids:
                    s.add(
                        MemberScope(
                            id=uuid7(),
                            workspace_id=workspace_id,
                            workspace_member_id=member.id,
                            scope_type=kind,
                            scope_id=scope_id,
                        )
                    )
            member.scope_restricted = bool(groups or accounts)
            await s.flush()
            after = await _scopes(s, member)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "member.scope_changed",
                    workspace_id=workspace_id,
                    subject_type="user",
                    subject_id=target_user_id,
                    before=before.as_dict(),
                    after=after.as_dict(),
                ),
            )
            return after

    # ---- notifications ----

    async def notifications(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        unread_only: bool,
    ) -> list[Notification]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            stmt = select(Notification).order_by(Notification.created_at.desc()).limit(100)
            if unread_only:
                stmt = stmt.where(Notification.read_at.is_(None))
            if principal.member_scope.restricted:
                # A scoped member sees notices about the accounts they can reach, directly or
                # through one of their groups, and nothing workspace-wide.
                stmt = stmt.where(
                    Notification.social_account_id.in_(await _reachable_accounts(s, principal))
                )
            return list((await s.execute(stmt)).scalars())

    async def mark_notification_read(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        notification_id: uuid.UUID,
        meta: RequestMeta,
    ) -> None:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            n = await s.get(Notification, notification_id, with_for_update=True)
            if n is None or (
                principal.member_scope.restricted
                and n.social_account_id not in await _reachable_accounts(s, principal)
            ):
                raise NotFoundError("Notification not found.")
            if n.read_at is None:
                n.read_at = datetime.now(UTC)
                n.read_by = user.user_id

    async def audit_log(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        limit: int,
        before_id: int | None,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """Entries with the actor's **current** name beside the recorded label, so a renamed
        user cannot impersonate someone in the log view (Sprint 2 carry-over)."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ_AUDIT_LOG, workspace_id)
            rows = await list_audit(s, workspace_id, limit=limit, before_id=before_id, **filters)
            ids = {r.actor_id for r in rows if r.actor_type == "user" and r.actor_id}
            names = (
                dict(
                    (
                        await s.execute(select(User.id, User.display_name).where(User.id.in_(ids)))
                    ).all()
                )
                if ids
                else {}
            )
            return [
                {
                    "id": r.id,
                    "created_at": r.created_at,
                    "actor_type": r.actor_type,
                    "actor_id": r.actor_id,
                    "actor_label": r.actor_label,
                    "actor_name": names.get(r.actor_id) if r.actor_id else None,
                    "surface": r.surface,
                    "action": r.action,
                    "subject_type": r.subject_type,
                    "subject_id": r.subject_id,
                    "social_account_id": r.social_account_id,
                    "account_group_id": r.account_group_id,
                    "subject_content_hash": r.subject_content_hash.hex()
                    if r.subject_content_hash
                    else None,
                    "before": r.before,
                    "after": r.after,
                    "request_id": r.request_id,
                }
                for r in rows
            ]


async def _workspace(s: AsyncSession, workspace_id: uuid.UUID, *, lock: bool = False) -> Workspace:
    stmt = select(Workspace).where(Workspace.id == workspace_id)
    if lock:
        stmt = stmt.with_for_update()
    ws: Workspace = (await s.execute(stmt)).scalar_one()
    return ws


async def _member(
    s: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[WorkspaceMember, User]:
    row = (
        await s.execute(
            select(WorkspaceMember, User)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id)
        )
    ).first()
    if row is None:
        raise NotFoundError("Member not found.")
    return row[0], row[1]


async def _revoke_pending_invitations(
    s: AsyncSession, workspace_id: uuid.UUID, inviter_id: uuid.UUID
) -> None:
    await s.execute(
        update(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.workspace_id == workspace_id,
            WorkspaceInvitation.invited_by == inviter_id,
            WorkspaceInvitation.accepted_at.is_(None),
            WorkspaceInvitation.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )


async def _ensure_another_owner(s: AsyncSession, workspace_id: uuid.UUID) -> None:
    owners = (
        await s.execute(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.role == Role.OWNER.value,
            )
        )
    ).scalar_one()
    if owners <= 1:
        raise ConflictError(
            "A workspace needs at least one owner.", code="last_owner", field="role"
        )


@dataclass(frozen=True, slots=True)
class MemberScopes:
    restricted: bool
    account_group_ids: list[uuid.UUID]
    social_account_ids: list[uuid.UUID]

    def as_dict(self) -> dict[str, Any]:
        return {
            "restricted": self.restricted,
            "account_group_ids": [str(i) for i in self.account_group_ids],
            "social_account_ids": [str(i) for i in self.social_account_ids],
        }


async def _reachable_accounts(s: AsyncSession, principal: Principal) -> set[uuid.UUID]:
    scope = principal.member_scope
    via_groups = set(
        (
            await s.execute(
                select(AccountGroupMember.social_account_id).where(
                    AccountGroupMember.account_group_id.in_(scope.group_ids)
                )
            )
        ).scalars()
    )
    return set(scope.account_ids) | via_groups


async def _scopes(s: AsyncSession, member: WorkspaceMember) -> MemberScopes:
    rows = (
        await s.execute(
            select(MemberScope.scope_type, MemberScope.scope_id).where(
                MemberScope.workspace_member_id == member.id
            )
        )
    ).all()
    return MemberScopes(
        restricted=member.scope_restricted,
        account_group_ids=sorted(i for k, i in rows if k == "account_group"),
        social_account_ids=sorted(i for k, i in rows if k == "social_account"),
    )


def _view(ws: Workspace, role: str) -> WorkspaceView:
    return WorkspaceView(
        ws.id,
        ws.name,
        ws.slug,
        ws.workspace_type,
        ws.timezone,
        role,
        ws.archived_at is not None,
        dict(ws.settings),
    )


_COMMON_TZ_ALIASES: dict[str, str] = {
    "Asia/Calcutta": "Asia/Kolkata",
    "Asia/Katmandu": "Asia/Kathmandu",
    "Asia/Saigon": "Asia/Ho_Chi_Minh",
    "Asia/Rangoon": "Asia/Yangon",
    "US/Eastern": "America/New_York",
    "US/Central": "America/Chicago",
    "US/Mountain": "America/Denver",
    "US/Pacific": "America/Los_Angeles",
}


def canonicalize_timezone(tz: str) -> str:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    candidate = _COMMON_TZ_ALIASES.get(tz, tz)
    try:
        ZoneInfo(candidate)
        return candidate
    except (ZoneInfoNotFoundError, ValueError):
        try:
            ZoneInfo(tz)
            return tz
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise InvalidInput("Unknown timezone.", code="invalid_timezone", field="timezone") from exc


def _validate_timezone(tz: str) -> None:
    canonicalize_timezone(tz)
