"""Social accounts, account groups and their ordered membership (M3, docs/03 §4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mera.infrastructure.db.base import Base, TenantScoped, Timestamps, UUIDPrimaryKey


def _jsonb() -> Any:
    return mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class SocialAccount(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    __tablename__ = "social_account"

    platform: Mapped[str] = mapped_column(Text, nullable=False)
    platform_account_id: Mapped[str] = mapped_column(Text, nullable=False)
    handle: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    nickname: Mapped[str] = mapped_column(Text, nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    account_type: Mapped[str] = mapped_column(Text, nullable=False)
    credentials_ct: Mapped[str | None] = mapped_column(Text)
    granted_scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    token_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    status_reason: Mapped[str | None] = mapped_column(Text)
    capabilities_snapshot: Mapped[dict[str, Any]] = _jsonb()
    default_brand_voice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    default_posting_times: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    settings: Mapped[dict[str, Any]] = _jsonb()
    rate_budget: Mapped[dict[str, Any]] = _jsonb()
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    health: Mapped[dict[str, Any]] = _jsonb()
    connected_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )
    connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountGroup(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    __tablename__ = "account_group"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(Text)
    default_brand_voice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    posting_schedule: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    content_pillar_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    stagger_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="random_spread")
    stagger_min_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="180")
    stagger_max_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="900")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    settings: Mapped[dict[str, Any]] = _jsonb()


class AccountGroupMember(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    __tablename__ = "account_group_member"

    account_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account_group.id", ondelete="CASCADE"), nullable=False
    )
    social_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("social_account.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled_by_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )


class AccountConnectFlow(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "account_connect_flow"

    platform: Mapped[str] = mapped_column(Text, nullable=False)
    state_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    code_verifier: Mapped[str] = mapped_column(Text, nullable=False)
    initiated_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    reconnect_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("social_account.id", ondelete="CASCADE")
    )
    return_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connect_code_hash: Mapped[str | None] = mapped_column(Text, unique=True)
    pending_identity_ct: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # What finishing needs besides the code: the Mastodon instance (P2-M3).
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class MastodonApp(Base):
    """Mera's OAuth app on one Mastodon instance (migration 0015). Not tenant data."""

    __tablename__ = "mastodon_app"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    instance: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    client_secret_ct: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Notification(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "notification"

    kind: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    subject_type: Mapped[str | None] = mapped_column(Text)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    social_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )
