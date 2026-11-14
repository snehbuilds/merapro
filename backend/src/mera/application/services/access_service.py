"""API keys (M2.6) and invitations (M2.8).

API keys: workspace-bound, hashed, shown once. Only a signed-in user can create one (a key
cannot mint keys), and the requested scopes must be known. A key never exceeds its
creator: it resolves to the creator's *current* role and member scope, intersected with the
key's scopes, on every request.

Invitations: a single-use token for one email address and one role. Inviting needs
``MANAGE_MEMBERS``, and nobody can invite into a role above their own; ownership is never
granted by invitation. The accept URL is emailed when a Notifier is configured, and is
also returned to the inviter once, so it can be shared by hand if the email goes astray.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.emails import invitation_email
from mera.application.errors import ConflictError, Forbidden, InvalidInput, NotFoundError
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.domain.context import WorkspaceContext
from mera.domain.errors import PermissionDenied
from mera.domain.policies import rbac
from mera.domain.policies.rbac import KNOWN_SCOPES, Permission
from mera.domain.ports.notifier import Notifier, NotifierError
from mera.domain.value_objects.ids import uuid7
from mera.domain.value_objects.role import Role
from mera.infrastructure.crypto import api_keys
from mera.infrastructure.db.errors import constraint_name
from mera.infrastructure.db.models import (
    ApiKey,
    User,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMember,
)
from mera.infrastructure.db.repositories.audit import record
from mera.infrastructure.db.session import secret_lookup_transaction, workspace_transaction

INVITATION_TTL = timedelta(days=7)
MAX_KEY_LIFETIME_DAYS = 365


@dataclass(frozen=True, slots=True)
class ApiKeyView:
    id: uuid.UUID
    name: str
    key_prefix: str
    scopes: list[str]
    created_by: uuid.UUID
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class CreatedApiKey:
    key: ApiKeyView
    secret: str  # shown exactly once


@dataclass(frozen=True, slots=True)
class InvitationView:
    id: uuid.UUID
    email: str
    role: str
    invited_by: uuid.UUID | None
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CreatedInvitation:
    invitation: InvitationView
    token: str  # shown exactly once
    accept_url: str
    email_sent: bool = False


class AccessService(WorkspaceScopedService):
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        sandbox: bool,
        public_url: str,
        notifier: Notifier | None = None,
    ) -> None:
        super().__init__(sessionmaker)
        self._notifier = notifier
        self._sandbox = sandbox
        self._public_url = public_url.rstrip("/")

    # ---- API keys ----

    async def create_api_key(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        name: str,
        scopes: list[str],
        expires_in_days: int | None,
    ) -> CreatedApiKey:
        self._require_human(user)
        unknown = sorted(set(scopes) - KNOWN_SCOPES)
        if not scopes or unknown:
            raise InvalidInput(
                f"Unknown scopes: {', '.join(unknown)}"
                if unknown
                else "Choose at least one scope.",
                code="invalid_scopes",
                field="scopes",
            )
        if expires_in_days is not None and not 1 <= expires_in_days <= MAX_KEY_LIFETIME_DAYS:
            raise InvalidInput(
                f"Expiry must be 1-{MAX_KEY_LIFETIME_DAYS} days.",
                code="invalid_expiry",
                field="expires_in_days",
            )
        secret = api_keys.new_key(sandbox=self._sandbox)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.MANAGE_MEMBERS, workspace_id)
            self._ensure_writable(await _workspace(s, workspace_id, lock=True))
            now = datetime.now(UTC)
            key = ApiKey(
                id=uuid7(),
                workspace_id=workspace_id,
                name=name.strip(),
                key_prefix=api_keys.display_prefix(secret),
                key_hash=api_keys.hash_key(secret),
                scopes=sorted(set(scopes)),
                created_by=user.user_id,
                expires_at=now + timedelta(days=expires_in_days) if expires_in_days else None,
                created_at=now,
            )
            s.add(key)
            await s.flush()
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "api_key.created",
                    workspace_id=workspace_id,
                    subject_type="api_key",
                    subject_id=key.id,
                    after={"name": key.name, "scopes": key.scopes, "prefix": key.key_prefix},
                ),
            )
            view = _key_view(key)
        return CreatedApiKey(key=view, secret=secret)

    async def list_api_keys(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> list[ApiKeyView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.MANAGE_MEMBERS, workspace_id)
            rows = (
                await s.execute(
                    select(ApiKey)
                    .where(ApiKey.workspace_id == workspace_id)
                    .order_by(ApiKey.created_at.desc())
                )
            ).scalars()
            return [_key_view(k) for k in rows]

    async def revoke_api_key(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, key_id: uuid.UUID, meta: RequestMeta
    ) -> None:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.MANAGE_MEMBERS, workspace_id)
            key = (
                await s.execute(
                    select(ApiKey)
                    .where(ApiKey.workspace_id == workspace_id, ApiKey.id == key_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if key is None:
                raise NotFoundError("API key not found.")
            if key.revoked_at is None:
                key.revoked_at = datetime.now(UTC)
                await s.flush()
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "api_key.revoked",
                        workspace_id=workspace_id,
                        subject_type="api_key",
                        subject_id=key.id,
                    ),
                )

    # ---- invitations ----

    async def invite(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        email: str,
        role: Role,
    ) -> CreatedInvitation:
        # A key must not be able to add people: a leaked key would leave a member behind
        # that outlives the key's revocation (Sprint 3 review H2).
        self._require_human(user)
        if role is Role.OWNER:
            raise InvalidInput(
                "Ownership is transferred, not invited.", code="invalid_role", field="role"
            )
        token = api_keys.new_invitation_token()
        try:
            async with self._tx(user, workspace_id, meta) as s:
                ws = await _workspace(s, workspace_id, lock=True)
                principal = await load_user_principal(s, user, workspace_id)
                rbac.require(principal, Permission.MANAGE_MEMBERS, workspace_id)
                if principal.role is None or role > principal.role:
                    raise PermissionDenied(
                        "cannot invite into a role above your own",
                        permission=Permission.MANAGE_MEMBERS.value,
                    )
                self._ensure_writable(ws)
                existing = (
                    await s.execute(
                        select(WorkspaceMember.id)
                        .join(User, User.id == WorkspaceMember.user_id)
                        .where(WorkspaceMember.workspace_id == workspace_id, User.email == email)
                    )
                ).first()
                if existing is not None:
                    raise ConflictError(
                        "That person is already a member.", code="already_member", field="email"
                    )
                now = datetime.now(UTC)
                # An expired pending invitation would otherwise block re-inviting forever.
                await s.execute(
                    update(WorkspaceInvitation)
                    .where(
                        WorkspaceInvitation.workspace_id == workspace_id,
                        WorkspaceInvitation.email == email.strip(),
                        WorkspaceInvitation.accepted_at.is_(None),
                        WorkspaceInvitation.revoked_at.is_(None),
                        WorkspaceInvitation.expires_at <= now,
                    )
                    .values(revoked_at=now)
                )
                inv = WorkspaceInvitation(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    email=email.strip(),
                    role=role.value,
                    token_hash=api_keys.hash_key(token),
                    invited_by=user.user_id,
                    expires_at=now + INVITATION_TTL,
                    created_at=now,
                )
                s.add(inv)
                await s.flush()
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "invitation.created",
                        workspace_id=workspace_id,
                        subject_type="invitation",
                        subject_id=inv.id,
                        after={"email": inv.email, "role": inv.role},
                    ),
                )
                view = _invite_view(inv)
                workspace_name = ws.name
        except IntegrityError as exc:
            if constraint_name(exc) != "uq_workspace_invitation_pending":
                raise
            raise ConflictError(
                "An invitation to that address is already pending.",
                code="invitation_pending",
                field="email",
            ) from exc
        url = f"{self._public_url}/invite/{token}"
        sent = False
        if self._notifier is not None:
            message = invitation_email(
                view.email,
                url,
                workspace=workspace_name,
                inviter=user.display_name,
                role=role.value,
                days=INVITATION_TTL.days,
            )
            try:
                await self._notifier.send(message)
                sent = True
            except NotifierError:
                # The invitation stands; the inviter still has the link to share.
                sent = False
        return CreatedInvitation(invitation=view, token=token, accept_url=url, email_sent=sent)

    async def list_invitations(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> list[InvitationView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.MANAGE_MEMBERS, workspace_id)
            rows = (
                await s.execute(
                    select(WorkspaceInvitation)
                    .where(
                        WorkspaceInvitation.workspace_id == workspace_id,
                        WorkspaceInvitation.accepted_at.is_(None),
                        WorkspaceInvitation.revoked_at.is_(None),
                        WorkspaceInvitation.expires_at > datetime.now(UTC),
                    )
                    .order_by(WorkspaceInvitation.created_at.desc())
                )
            ).scalars()
            return [_invite_view(i) for i in rows]

    async def revoke_invitation(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        invitation_id: uuid.UUID,
        meta: RequestMeta,
    ) -> None:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.MANAGE_MEMBERS, workspace_id)
            inv = (
                await s.execute(
                    select(WorkspaceInvitation)
                    .where(
                        WorkspaceInvitation.workspace_id == workspace_id,
                        WorkspaceInvitation.id == invitation_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if inv is None:
                raise NotFoundError("Invitation not found.")
            if inv.revoked_at is None and inv.accepted_at is None:
                inv.revoked_at = datetime.now(UTC)
                await s.flush()
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "invitation.revoked",
                        workspace_id=workspace_id,
                        subject_type="invitation",
                        subject_id=inv.id,
                    ),
                )

    async def accept_invitation(
        self, user: AuthenticatedUser, token: str, meta: RequestMeta
    ) -> uuid.UUID:
        """Returns the joined workspace id. The invitation must be addressed to the signed-in
        user's *verified* email, and its inviter must still hold the authority to grant the
        role; anything else looks like an invalid link."""
        self._require_human(user)
        if not user.email_verified:
            raise Forbidden(
                "Verify your email address before accepting an invitation.",
                code="email_unverified",
            )
        token_hash = api_keys.hash_key(token)
        async with secret_lookup_transaction(
            self._sm, "mera.invitation_token_hash", token_hash
        ) as s:
            found = (
                await s.execute(
                    select(WorkspaceInvitation.id, WorkspaceInvitation.workspace_id).where(
                        WorkspaceInvitation.token_hash == token_hash
                    )
                )
            ).first()
        invalid = NotFoundError(
            "This invitation link is invalid or has expired.", code="invalid_invitation"
        )
        if found is None:
            raise invalid
        inv_id, ws_id = found
        async with workspace_transaction(self._sm, WorkspaceContext(ws_id)) as s:
            inv = (
                await s.execute(
                    select(WorkspaceInvitation)
                    .where(WorkspaceInvitation.id == inv_id)
                    .with_for_update()
                )
            ).scalar_one()
            now = datetime.now(UTC)
            if (
                inv.accepted_at is not None
                or inv.revoked_at is not None
                or inv.expires_at <= now
                or inv.email.lower() != user.email.lower()
            ):
                raise invalid
            ws = await _workspace(s, ws_id, lock=True)
            if ws.deleted_at is not None:
                raise invalid
            # The invitation carries its inviter's authority: if they have since been
            # demoted below the invited role, or removed, it is void (review M3).
            inviter_role = (
                await s.execute(
                    select(WorkspaceMember.role).where(
                        WorkspaceMember.workspace_id == ws_id,
                        WorkspaceMember.user_id == inv.invited_by,
                    )
                )
            ).scalar_one_or_none()
            if (
                inviter_role is None
                or Role(inviter_role) < Role(inv.role)
                or not Role(inviter_role).at_least(Role.ADMIN)
            ):
                raise invalid
            self._ensure_writable(ws)
            already = (
                await s.execute(
                    select(WorkspaceMember.id).where(
                        WorkspaceMember.workspace_id == ws_id,
                        WorkspaceMember.user_id == user.user_id,
                    )
                )
            ).first()
            if already is not None:
                raise ConflictError("You are already a member.", code="already_member")
            s.add(
                WorkspaceMember(
                    workspace_id=ws_id,
                    user_id=user.user_id,
                    role=inv.role,
                    invited_by=inv.invited_by,
                    joined_at=now,
                )
            )
            inv.accepted_at = now
            inv.accepted_by = user.user_id
            await s.flush()
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "member.joined",
                    workspace_id=ws_id,
                    subject_type="invitation",
                    subject_id=inv.id,
                    after={"role": inv.role},
                ),
            )
        return ws_id


async def _workspace(s: AsyncSession, workspace_id: uuid.UUID, *, lock: bool = False) -> Workspace:
    stmt = select(Workspace).where(Workspace.id == workspace_id)
    if lock:
        stmt = stmt.with_for_update()
    ws: Workspace = (await s.execute(stmt)).scalar_one()
    return ws


def _key_view(k: ApiKey) -> ApiKeyView:
    return ApiKeyView(
        k.id,
        k.name,
        k.key_prefix,
        list(k.scopes),
        k.created_by,
        k.created_at,
        k.expires_at,
        k.last_used_at,
        k.revoked_at,
    )


def _invite_view(i: WorkspaceInvitation) -> InvitationView:
    return InvitationView(i.id, i.email, i.role, i.invited_by, i.created_at, i.expires_at)
