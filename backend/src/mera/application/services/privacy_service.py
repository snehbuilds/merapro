"""Data rights (P3-M7.2, docs/09 §9.3 and §9.4).

- **Access, a person**: ``export_me`` returns what Mera holds about the caller as JSON.
- **Access and portability, a workspace**: an Owner asks for an export. A worker walks
  every table with a ``workspace_id`` (secrets, hashes and embeddings left out), adds the
  media files, and stores one zip in the object store. The download is a time-limited
  link; the export expires after seven days.
- **Erasure, a commenter**: their handle and platform id are removed from the comment; the
  text and its classification stay, for analytics continuity.
- **Erasure, a person**: ``delete_me`` (with a factor the person has) signs them out
  everywhere and starts a 30-day grace. Then ``erase_due`` replaces their personal data
  with a pseudonym, removes their sign-in methods, bindings and memberships, and
  pseudonymises their name on audit entries, which are kept: an audit log with holes in it
  is not an audit log.
- **Erasure, a workspace**: 30 days after deletion its media leave the object store and its
  rows are deleted. Audit entries are kept (they have no foreign key to the workspace).

The two erasure steps that the app role may not do (rewrite audit labels, delete a
workspace row) run with the migration role, in workers only.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import Table, delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import ConflictError, Forbidden, NotFoundError
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.domain.context import WorkspaceContext
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.domain.ports.object_store import ObjectStore
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.db.base import Base
from mera.infrastructure.db.models import (
    AccountGroupMember,
    ApproverBinding,
    Comment,
    DataExport,
    MediaAsset,
    OAuthIdentity,
    RefreshToken,
    SecurityEvent,
    User,
    Workspace,
    WorkspaceMember,
)
from mera.infrastructure.db.repositories.audit import AuditEntry, record
from mera.infrastructure.db.session import (
    global_transaction,
    system_scan_transaction,
    user_transaction,
    workspace_transaction,
)
from mera.infrastructure.queue.deferrer import Deferrer

log = structlog.get_logger(__name__)

GRACE: Final = timedelta(days=30)
EXPORT_TTL: Final = timedelta(days=7)
LINK_TTL: Final = timedelta(minutes=15)
# Never exported: credentials, secrets, hashes, embeddings.
_SECRET_PARTS: Final = (
    "credential",
    "secret",
    "_hash",
    "token",
    "password",
    "totp",
    "_ct",
    "embedding",
    "verifier",
    "recovery",
    "pin",
)


def _secret(column: str) -> bool:
    return any(p in column for p in _SECRET_PARTS)


def _plain(v: Any) -> Any:
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, bytes):
        return v.hex()
    return v


def tenant_tables() -> list[Table]:
    return sorted(
        (t for t in Base.metadata.tables.values() if "workspace_id" in t.c),
        key=lambda t: t.name,
    )


class PrivacyService(WorkspaceScopedService):
    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        object_store: ObjectStore | None,
        deferrer: Deferrer | None,
        security: Any | None = None,
        migration_url: str | None = None,
    ) -> None:
        super().__init__(sm)
        self._store = object_store
        self._deferrer = deferrer
        self._security = security
        self._migration_url = migration_url

    # ------------------------------------------------------------------ a person

    async def export_me(self, user: AuthenticatedUser) -> dict[str, Any]:
        async with user_transaction(self._sm, user.user_id) as s:
            u = await s.get(User, user.user_id)
            if u is None:
                raise NotFoundError("Not found.")
            members = (
                await s.execute(
                    select(Workspace.name, WorkspaceMember.role, WorkspaceMember.created_at)
                    .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
                    .where(WorkspaceMember.user_id == u.id)
                )
            ).all()
            bindings = (
                await s.execute(
                    select(
                        ApproverBinding.channel_type,
                        ApproverBinding.status,
                        ApproverBinding.created_at,
                    ).where(ApproverBinding.user_id == u.id)
                )
            ).all()
            events = (
                await s.execute(
                    select(SecurityEvent.kind, SecurityEvent.created_at, SecurityEvent.ip_address)
                    .where(SecurityEvent.user_id == u.id)
                    .order_by(SecurityEvent.id.desc())
                    .limit(1000)
                )
            ).all()
            identities = (
                await s.execute(select(OAuthIdentity.provider).where(OAuthIdentity.user_id == u.id))
            ).scalars()
            return {
                "exported_at": datetime.now(UTC).isoformat(),
                "profile": {
                    "email": u.email,
                    "display_name": u.display_name,
                    "email_verified_at": _plain(u.email_verified_at),
                    "created_at": _plain(u.created_at),
                    "two_step_sign_in": u.mfa_enabled_at is not None,
                    "approval_pin_set": u.approval_pin_hash is not None,
                },
                "sign_in_providers": sorted(set(identities)),
                "memberships": [
                    {"workspace": n, "role": r, "since": _plain(c)} for n, r, c in members
                ],
                "approval_channels": [
                    {"channel": c, "status": st, "since": _plain(at)} for c, st, at in bindings
                ],
                "security_events": [
                    {"kind": k, "at": _plain(at), "ip": ip} for k, at, ip in events
                ],
            }

    async def delete_me(self, user: AuthenticatedUser, proof: str, meta: RequestMeta) -> None:
        if user.is_api_key:
            raise Forbidden(
                "Sign in as yourself to delete your account.", code="user_credentials_required"
            )
        async with user_transaction(self._sm, user.user_id) as s:
            owned = (
                (
                    await s.execute(
                        select(WorkspaceMember.workspace_id).where(
                            WorkspaceMember.user_id == user.user_id, WorkspaceMember.role == "owner"
                        )
                    )
                )
                .scalars()
                .all()
            )
            for ws in owned:
                others = await s.scalar(
                    select(WorkspaceMember.id)
                    .where(
                        WorkspaceMember.workspace_id == ws,
                        WorkspaceMember.role == "owner",
                        WorkspaceMember.user_id != user.user_id,
                    )
                    .limit(1)
                )
                if others is None:
                    raise ConflictError(
                        "You are the only Owner of a workspace. Make someone else an Owner, "
                        "or delete the workspace, first.",
                        code="sole_owner",
                    )
        async with global_transaction(self._sm) as s:
            u = await s.get(User, user.user_id, with_for_update=True)
            if u is None or u.deleted_at is not None:
                raise NotFoundError("Not found.")
            if self._security is None:  # never without a proof (security review #7)
                raise RuntimeError("PrivacyService.delete_me needs a SecurityService")
            failed = await self._security._reauth(s, u, proof, meta)
            if failed is None:
                now = datetime.now(UTC)
                u.deleted_at = now
                u.token_version = (u.token_version or 0) + 1
                await s.execute(
                    update(RefreshToken)
                    .where(RefreshToken.user_id == u.id, RefreshToken.revoked_at.is_(None))
                    .values(revoked_at=now, revoked_reason="account_deleted")
                )
                s.add(
                    SecurityEvent(user_id=u.id, kind="account.deleted", ip_address=meta.ip_address)
                )
        if failed is not None:
            raise failed

    # ------------------------------------------------------------------ a commenter

    async def erase_comment_author(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        comment_id: uuid.UUID,
    ) -> None:
        async with self._tx(user, workspace_id, meta) as s:
            c = await s.get(Comment, comment_id, with_for_update=True)
            if c is None:
                raise NotFoundError("Comment not found.")
            principal = await load_user_principal(s, user, workspace_id)
            groups = list(
                (
                    await s.execute(
                        select(AccountGroupMember.account_group_id).where(
                            AccountGroupMember.social_account_id == c.social_account_id
                        )
                    )
                ).scalars()
            )
            self._require_for_account(
                principal, Permission.REPLY, workspace_id, c.social_account_id, groups
            )
            c.author_handle, c.author_platform_id = None, None
            c.author_erased_at = datetime.now(UTC)
            await record(
                s,
                AuditEntry(
                    action="comment.author_erased",
                    actor_type="user",
                    actor_id=user.user_id,
                    actor_label=user.display_name,
                    surface=meta.surface,
                    workspace_id=workspace_id,
                    subject_type="comment",
                    subject_id=c.id,
                    social_account_id=c.social_account_id,
                    ip_address=meta.ip_address,
                    request_id=meta.request_id,
                ),
            )

    # ------------------------------------------------------------------ a workspace

    async def request_export(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> DataExport:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.MANAGE_WORKSPACE, workspace_id)
            row = DataExport(
                id=uuid7(), workspace_id=workspace_id, requested_by_user_id=user.user_id
            )
            s.add(row)
            await record(
                s,
                AuditEntry(
                    action="workspace.export_requested",
                    actor_type="user",
                    actor_id=user.user_id,
                    actor_label=user.display_name,
                    surface=meta.surface,
                    workspace_id=workspace_id,
                    subject_type="data_export",
                    subject_id=row.id,
                    ip_address=meta.ip_address,
                    request_id=meta.request_id,
                ),
            )
            await s.flush()
            await s.refresh(row)
        if self._deferrer is not None:
            await self._deferrer.defer(
                "privacy:build_export",
                queue="notify",
                workspace_id=str(workspace_id),
                export_id=str(row.id),
            )
        return row

    async def list_exports(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> list[DataExport]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.MANAGE_WORKSPACE, workspace_id)
            return list(
                (
                    await s.execute(
                        select(DataExport).order_by(DataExport.created_at.desc()).limit(20)
                    )
                ).scalars()
            )

    async def export_link(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        export_id: uuid.UUID,
    ) -> str:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.MANAGE_WORKSPACE, workspace_id)
            row = await s.get(DataExport, export_id)
            if row is None:
                raise NotFoundError("Export not found.")
            if row.state != "ready" or not row.object_key:
                raise ConflictError("The export is not ready.", code="export_not_ready")
            if row.expires_at is not None and row.expires_at <= datetime.now(UTC):
                raise ConflictError(
                    "The export has expired; ask for a new one.", code="export_expired"
                )
            key = row.object_key
        assert self._store is not None
        return await self._store.presigned_url(key, LINK_TTL)

    async def build_export(self, workspace_id: uuid.UUID, export_id: uuid.UUID) -> str:
        """Worker: every tenant table and the media files, as one zip."""
        ctx = WorkspaceContext(workspace_id)
        buf = io.BytesIO()
        try:
            async with workspace_transaction(self._sm, ctx) as s:
                media = list((await s.execute(select(MediaAsset.id, MediaAsset.object_key))).all())
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    manifest: dict[str, int] = {}
                    for t in tenant_tables():
                        cols = [c for c in t.c if not _secret(c.name)]
                        rows = (await s.execute(select(*cols))).mappings().all()
                        manifest[t.name] = len(rows)
                        z.writestr(
                            f"tables/{t.name}.json",
                            json.dumps(
                                [{k: _plain(v) for k, v in r.items()} for r in rows],
                                indent=1,
                                default=str,
                            ),
                        )
                    for mid, key in media:
                        if self._store is None:
                            break
                        try:
                            z.writestr(f"media/{mid}", await self._store.get(key))
                        except Exception:
                            manifest.setdefault("media_missing", 0)
                            manifest["media_missing"] += 1
                    z.writestr(
                        "manifest.json",
                        json.dumps(
                            {
                                "workspace_id": str(workspace_id),
                                "exported_at": datetime.now(UTC).isoformat(),
                                "rows": manifest,
                                "left_out": "credentials, secrets, hashes and embeddings",
                            },
                            indent=1,
                        ),
                    )
            assert self._store is not None
            key = f"exports/{workspace_id}/{export_id}.zip"
            stored = await self._store.put(key, buf.getvalue(), "application/zip")
            state, error = "ready", None
        except Exception as exc:
            key, stored, state, error = None, None, "failed", type(exc).__name__
        async with workspace_transaction(self._sm, ctx) as s:
            row = await s.get(DataExport, export_id, with_for_update=True)
            if row is not None:
                row.state, row.error, row.object_key = state, error, key
                row.byte_size = stored.byte_size if stored else None
                row.finished_at = datetime.now(UTC)
                row.expires_at = datetime.now(UTC) + EXPORT_TTL if state == "ready" else None
        return state

    # ------------------------------------------------------------------ erasure

    async def erase_due(self, now: datetime | None = None) -> dict[str, int]:
        """Worker, daily: people and workspaces deleted more than 30 days ago."""
        now = now or datetime.now(UTC)
        out = {"users": 0, "workspaces": 0}
        async with global_transaction(self._sm) as s:
            users = list(
                (
                    await s.execute(
                        select(User.id).where(
                            User.deleted_at.is_not(None),
                            User.deleted_at < now - GRACE,
                            User.erased_at.is_(None),
                        )
                    )
                ).scalars()
            )
        for uid in users:
            await self._erase_user(uid, now)
            out["users"] += 1
        async with system_scan_transaction(self._sm) as s:
            spaces = list(
                (
                    await s.execute(
                        select(Workspace.id).where(
                            Workspace.deleted_at.is_not(None), Workspace.deleted_at < now - GRACE
                        )
                    )
                ).scalars()
            )
        for ws in spaces:
            await self._erase_workspace(ws)
            out["workspaces"] += 1
        return out

    async def _erase_user(self, uid: uuid.UUID, now: datetime) -> None:
        async with user_transaction(self._sm, uid) as s:
            spaces = list(
                (
                    await s.execute(
                        select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == uid)
                    )
                ).scalars()
            )
            await s.execute(delete(ApproverBinding).where(ApproverBinding.user_id == uid))
        for ws in spaces:
            async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
                await s.execute(delete(WorkspaceMember).where(WorkspaceMember.user_id == uid))
        async with global_transaction(self._sm) as s:
            u = await s.get(User, uid, with_for_update=True)
            if u is None:
                return
            await s.execute(delete(OAuthIdentity).where(OAuthIdentity.user_id == uid))
            await s.execute(delete(RefreshToken).where(RefreshToken.user_id == uid))
            u.email = f"erased-{uid.hex}@erased.invalid"
            u.display_name = "Deleted user"
            u.avatar_url = None
            u.password_hash = None
            u.totp_secret_ct = u.totp_pending_ct = None
            u.recovery_code_hashes = []
            u.approval_pin_hash = None
            u.mfa_enabled_at = None
            u.erased_at = now
        await self._owner_sql(
            "ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update",
            "UPDATE audit_log SET actor_label = 'Deleted user' WHERE actor_id = :uid",
            "ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update",
            uid=uid,
        )
        log.info("privacy.user_erased", user_id=str(uid))

    async def _erase_workspace(self, ws: uuid.UUID) -> None:
        async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
            keys = list((await s.execute(select(MediaAsset.object_key))).scalars())
            exports = list(
                (
                    await s.execute(
                        select(DataExport.object_key).where(DataExport.object_key.is_not(None))
                    )
                ).scalars()
            )
        if self._store is not None:
            for key in [*keys, *exports]:
                try:
                    await self._store.delete(str(key))
                except Exception:
                    log.warning("privacy.object_delete_failed", workspace_id=str(ws))
        # Rows: the workspace and everything that cascades from it. audit_log has no
        # foreign key to the workspace and is kept.
        await self._owner_sql("DELETE FROM workspace WHERE id = :ws", ws=ws)
        log.info("privacy.workspace_erased", workspace_id=str(ws))

    async def _owner_sql(self, *statements: str, **params: Any) -> None:
        if not self._migration_url:
            raise RuntimeError("erasure needs DATABASE_MIGRATION_URL in the worker")
        engine = create_async_engine(self._migration_url)
        try:
            async with engine.begin() as c:
                for sql in statements:
                    names = {k: v for k, v in params.items() if f":{k}" in sql}
                    await c.execute(text(sql), names)
        finally:
            await engine.dispose()


__all__ = ["GRACE", "PrivacyService", "tenant_tables"]
