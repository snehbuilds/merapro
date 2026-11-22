"""Identity and tenancy tables for M1.5: ``workspace``, ``user``, ``workspace_member``.

Shapes follow docs/03 §3. Later identity tables (``member_scope``, ``api_key``,
``refresh_token``, ``oauth_identity``) arrive in M2.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mera.infrastructure.db.base import Base, TenantScoped, Timestamps, UUIDPrimaryKey


class Workspace(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "workspace"
    __table_args__ = (
        CheckConstraint("workspace_type IN ('personal', 'team', 'agency')", name="workspace_type"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    workspace_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="team")
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="UTC")
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    oauth_apps_ct: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    cloned_from_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace.id", ondelete="SET NULL")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 0025


class User(UUIDPrimaryKey, Timestamps, Base):
    """Global, not tenant-scoped: one person may belong to many workspaces."""

    __tablename__ = "user"

    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)  # null for OAuth-only
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    totp_secret_ct: Mapped[str | None] = mapped_column(Text)  # envelope-encrypted (M2.11)
    # P2-M2: enrolment awaiting confirmation, the active time, the last counter used (no
    # replay within a window), recovery codes (hashed, consumed on use), the approval PIN.
    totp_pending_ct: Mapped[str | None] = mapped_column(Text)
    mfa_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    totp_last_counter: Mapped[int | None] = mapped_column(BigInteger)
    recovery_code_hashes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    approval_pin_hash: Mapped[str | None] = mapped_column(Text)
    pin_failed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    pin_locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Authenticator and recovery codes lock out like the PIN (migration 0022).
    totp_failed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    totp_locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Proof of control over ``email`` (a link, or a provider assertion). Linking a
    # sign-in method and accepting an invitation both require it.
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bumped to invalidate every outstanding access token for this user at once.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 0025


class WorkspaceMember(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    __tablename__ = "workspace_member"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member_workspace_user"),
        CheckConstraint("role IN ('owner', 'admin', 'editor', 'approver', 'viewer')", name="role"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    scope_restricted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Out of office (P2-M2.8): approvals that would reach this person go to the delegate.
    away_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Daily digest (P3-M4.1, migration 0026): immediate, digest or both, at local HH:MM.
    digest_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="immediate")
    digest_time: Mapped[str] = mapped_column(Text, nullable=False, server_default="08:30")
    digest_last_sent_on: Mapped[date | None] = mapped_column(Date)
    delegate_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )


class MemberScope(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    """Narrows a membership to named groups or accounts (docs/03 §3.1). Narrowing only."""

    __tablename__ = "member_scope"
    __table_args__ = (
        CheckConstraint("scope_type IN ('account_group', 'social_account')", name="scope_type"),
        UniqueConstraint(
            "workspace_member_id", "scope_type", "scope_id", name="uq_member_scope_member_subject"
        ),
    )

    workspace_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace_member.id", ondelete="CASCADE"), nullable=False
    )
    scope_type: Mapped[str] = mapped_column(Text, nullable=False)
    # No FK yet: account_group and social_account arrive in M3.
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class OAuthIdentity(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "oauth_identity"
    __table_args__ = (
        CheckConstraint("provider IN ('google', 'microsoft')", name="provider"),
        UniqueConstraint("provider", "provider_subject", name="uq_oauth_identity_provider_subject"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_subject: Mapped[str] = mapped_column(Text, nullable=False)


class RefreshToken(UUIDPrimaryKey, Base):
    """Stored hashed; rotated on every use. ``family_id`` groups one login's rotation chain
    so that presenting a rotated token revokes the whole family (theft detection)."""

    __tablename__ = "refresh_token"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    rotated_from: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("refresh_token.id", ondelete="SET NULL")
    )
    device_label: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    family_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)


class EmailVerification(UUIDPrimaryKey, Base):
    __tablename__ = "email_verification"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MfaChallenge(UUIDPrimaryKey, Base):
    """A sign-in that passed its first factor and owes a TOTP code (migration 0016)."""

    __tablename__ = "mfa_challenge"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    device_label: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SecondFactorGrant(UUIDPrimaryKey, Base):
    """Single use, one user, one workspace, one action, a few minutes (docs/09 §5.3)."""

    __tablename__ = "second_factor_grant"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BotSecondFactor(UUIDPrimaryKey, Base):
    """An action waiting in a chat for ``/pin`` (migration 0016)."""

    __tablename__ = "bot_second_factor"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    channel_type: Mapped[str] = mapped_column(Text, nullable=False)
    chat_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SecurityEvent(Base):
    """MFA, PIN and second-factor events (migration 0016). Append only for the app."""

    __tablename__ = "security_event"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    ip_address: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReauthProof(UUIDPrimaryKey, Base):
    """A provider-only account re-confirmed through its provider (P3-M7.3, 0025)."""

    __tablename__ = "reauth_proof"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SecurityAlert(UUIDPrimaryKey, Base):
    """An alert the scanner sent (P3-M7.1, 0025)."""

    __tablename__ = "security_alert"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[int | None] = mapped_column(BigInteger)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    recipients: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SecurityScanCursor(Base):
    __tablename__ = "security_scan_cursor"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    last_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
