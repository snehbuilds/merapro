"""Scheduling, publishing, dead letters, events, metrics, sandbox capture and the hashtag
registry (M8, M9.4, M9.17, M10; docs/03 §8 and §10)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mera.infrastructure.db.base import Base, TenantScoped, UUIDPrimaryKey


def _fk(target: str, ondelete: str = "CASCADE", nullable: bool = False) -> Any:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable
    )


def _ts() -> Any:
    return mapped_column(DateTime(timezone=True))


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _jsonb() -> Any:
    return mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class ScheduledPost(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "scheduled_post"

    post_set_id: Mapped[uuid.UUID] = _fk("post_set.id")
    post_variant_id: Mapped[uuid.UUID] = _fk("post_variant.id")
    post_version_id: Mapped[uuid.UUID] = _fk("post_version.id", "RESTRICT")
    social_account_id: Mapped[uuid.UUID] = _fk("social_account.id")
    account_group_id: Mapped[uuid.UUID | None] = _fk("account_group.id", "SET NULL", True)
    approval_request_id: Mapped[uuid.UUID | None] = _fk("approval_request.id", "SET NULL", True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stagger_offset_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    placement: Mapped[dict[str, Any]] = _jsonb()
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime | None] = _ts()
    last_error: Mapped[str | None] = mapped_column(Text)
    dead_lettered_at: Mapped[datetime | None] = _ts()
    published_at: Mapped[datetime | None] = _ts()
    created_by_user_id: Mapped[uuid.UUID | None] = _fk("user.id", "SET NULL", True)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class PublishedPost(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "published_post"

    post_set_id: Mapped[uuid.UUID] = _fk("post_set.id")
    scheduled_post_id: Mapped[uuid.UUID] = _fk("scheduled_post.id")
    post_variant_id: Mapped[uuid.UUID] = _fk("post_variant.id")
    post_version_id: Mapped[uuid.UUID] = _fk("post_version.id", "RESTRICT")
    social_account_id: Mapped[uuid.UUID] = _fk("social_account.id")
    account_group_id: Mapped[uuid.UUID | None] = _fk("account_group.id", "SET NULL", True)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    platform_post_id: Mapped[str] = mapped_column(Text, nullable=False)
    permalink: Mapped[str | None] = mapped_column(Text)
    extra_ids: Mapped[dict[str, Any]] = _jsonb()
    content_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    via_autopilot: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edited_at: Mapped[datetime | None] = _ts()
    deleted_at: Mapped[datetime | None] = _ts()
    metrics_synced_at: Mapped[datetime | None] = _ts()
    next_metrics_at: Mapped[datetime | None] = _ts()
    # The comment poll (P2-M6.2): when next, and the newest comment time seen so far.
    next_comments_at: Mapped[datetime | None] = _ts()
    comment_cursor_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _now()


class PublishedComment(UUIDPrimaryKey, TenantScoped, Base):
    """A first comment is a separate delivery with its own idempotency and retries."""

    __tablename__ = "published_comment"

    published_post_id: Mapped[uuid.UUID] = _fk("published_post.id")
    social_account_id: Mapped[uuid.UUID] = _fk("social_account.id")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    platform_comment_id: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime | None] = _ts()
    last_error: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class DeadLetter(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "dead_letter"

    queue: Mapped[str] = mapped_column(Text, nullable=False)
    task_name: Mapped[str] = mapped_column(Text, nullable=False)
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    social_account_id: Mapped[uuid.UUID | None] = _fk("social_account.id", "SET NULL", True)
    post_set_id: Mapped[uuid.UUID | None] = _fk("post_set.id", "SET NULL", True)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failed_at: Mapped[datetime] = _now()
    resolved_at: Mapped[datetime | None] = _ts()
    resolved_by_user_id: Mapped[uuid.UUID | None] = _fk("user.id", "SET NULL", True)
    resolution: Mapped[str | None] = mapped_column(Text)


class Event(TenantScoped, Base):
    __tablename__ = "event"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_type: Mapped[str | None] = mapped_column(Text)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    social_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    account_group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict[str, Any]] = _jsonb()
    created_at: Mapped[datetime] = _now()


class MetricSnapshot(TenantScoped, Base):
    __tablename__ = "metric_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    published_post_id: Mapped[uuid.UUID | None] = _fk("published_post.id", "CASCADE", True)
    social_account_id: Mapped[uuid.UUID] = _fk("social_account.id")
    post_set_id: Mapped[uuid.UUID | None] = _fk("post_set.id", "SET NULL", True)
    account_group_id: Mapped[uuid.UUID | None] = _fk("account_group.id", "SET NULL", True)
    metric_key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[Decimal | None] = mapped_column(Numeric)
    platform_field: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SandboxCapture(TenantScoped, Base):
    __tablename__ = "sandbox_capture"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    social_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict[str, Any]] = _jsonb()
    created_at: Mapped[datetime] = _now()


class Hashtag(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "hashtag"

    tag: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="allowed")
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    first_used_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class HashtagStat(UUIDPrimaryKey, TenantScoped, Base):
    """How a tag did on one account (P3-M1.1, migration 0023). Recomputed daily."""

    __tablename__ = "hashtag_stat"

    social_account_id: Mapped[uuid.UUID] = _fk("social_account.id")
    tag: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    post_count: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_engagement: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    lift_vs_account_median: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    computed_at: Mapped[datetime] = _now()


class PostTemplate(UUIDPrimaryKey, TenantScoped, Base):
    """A reusable Composer starting point (migration 0014, P2-M1.4)."""

    __tablename__ = "post_template"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    outline: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    target_group_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    created_by: Mapped[uuid.UUID | None] = _fk("user.id", "SET NULL", True)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class ApprovalChannel(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "approval_channel"

    channel_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    config: Mapped[dict[str, Any]] = _jsonb()
    constraints_snapshot: Mapped[dict[str, Any]] = _jsonb()
    credentials_ct: Mapped[str | None] = mapped_column(Text)
    last_test_at: Mapped[datetime | None] = _ts()
    last_test_result: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = _fk("user.id", "SET NULL", True)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class ApproverBinding(UUIDPrimaryKey, Base):
    """Global: a chat bound to a user (docs/07 §6), across every workspace they belong to."""

    __tablename__ = "approver_binding"

    user_id: Mapped[uuid.UUID] = _fk("user.id")
    channel_type: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    external_label: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    preferences: Mapped[dict[str, Any]] = _jsonb()
    verified_at: Mapped[datetime] = _now()
    revoked_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _now()
    # WhatsApp's 24-hour window (migration 0017): when this chat last wrote to Mera.
    last_inbound_at: Mapped[datetime | None] = _ts()


class ChannelSendLog(Base):
    """Messages Mera sent on a paid channel (migration 0017). Append only for the app."""

    __tablename__ = "channel_send_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    channel_type: Mapped[str] = mapped_column(Text, nullable=False)
    chat_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    template: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class PairingCode(UUIDPrimaryKey, Base):
    __tablename__ = "pairing_code"

    user_id: Mapped[uuid.UUID] = _fk("user.id")
    channel_type: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_id: Mapped[uuid.UUID | None] = _fk("workspace.id", "CASCADE", True)
    code_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _now()


class ChannelPairingAttempt(Base):
    __tablename__ = "channel_pairing_attempt"

    channel_type: Mapped[str] = mapped_column(Text, primary_key=True)
    external_id: Mapped[str] = mapped_column(Text, primary_key=True)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    window_start: Mapped[datetime] = _now()
    locked_until: Mapped[datetime | None] = _ts()


class ChannelMessage(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "channel_message"

    approval_request_id: Mapped[uuid.UUID | None] = _fk("approval_request.id", "CASCADE", True)
    approver_binding_id: Mapped[uuid.UUID | None] = _fk("approver_binding.id", "SET NULL", True)
    user_id: Mapped[uuid.UUID | None] = _fk("user.id", "SET NULL", True)
    channel_type: Mapped[str] = mapped_column(Text, nullable=False)
    external_chat_id: Mapped[str] = mapped_column(Text, nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(Text)
    message_kind: Mapped[str] = mapped_column(Text, nullable=False)
    render_state: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    view: Mapped[dict[str, Any]] = _jsonb()
    cost_estimate: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    sent_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class ChannelUpdate(Base):
    __tablename__ = "channel_update"

    channel_type: Mapped[str] = mapped_column(Text, primary_key=True)
    update_id: Mapped[str] = mapped_column(Text, primary_key=True)
    received_at: Mapped[datetime] = _now()


class WebhookEndpoint(UUIDPrimaryKey, TenantScoped, Base):
    """An outbound webhook (P3-M4.3, migration 0026)."""

    __tablename__ = "webhook_endpoint"

    url: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    events: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    secret_ct: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    last_event_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = _fk("user.id", "SET NULL", True)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class WebhookDelivery(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "webhook_delivery"

    endpoint_id: Mapped[uuid.UUID] = _fk("webhook_endpoint.id")
    event_id: Mapped[int | None] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = _jsonb()
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime] = _now()
    last_status: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _now()
