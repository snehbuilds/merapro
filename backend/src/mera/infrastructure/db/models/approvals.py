"""Approval core and automation state (M6; docs/03 §7 and §10, ADR-0014)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Double,
    ForeignKey,
    Integer,
    Interval,
    LargeBinary,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mera.infrastructure.db.base import Base, TenantScoped, UUIDPrimaryKey


def _fk(target: str, ondelete: str = "SET NULL", nullable: bool = True) -> Any:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable
    )


def _ts() -> Any:
    return mapped_column(DateTime(timezone=True))


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ApprovalPolicy(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "approval_policy"

    scope_type: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    subject_types: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{post_set}'")
    )
    required_approver_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    quorum_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="any_of")
    approver_user_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default=text("'{}'")
    )
    # P2-M2.8: named people to escalate to; empty means the workspace's Admins.
    escalate_to_user_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    escalation_chain: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    remind_after: Mapped[timedelta] = mapped_column(Interval, nullable=False)
    escalate_after: Mapped[timedelta] = mapped_column(Interval, nullable=False)
    expires_after: Mapped[timedelta] = mapped_column(Interval, nullable=False)
    approval_validity_window: Mapped[timedelta] = mapped_column(Interval, nullable=False)
    quiet_hours: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    second_factor_actions: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    digest_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="immediate")
    updated_by_user_id: Mapped[uuid.UUID | None] = _fk("user.id")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class ApprovalRequest(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "approval_request"

    subject_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="post_set")
    post_set_id: Mapped[uuid.UUID] = _fk("post_set.id", "CASCADE", nullable=False)
    account_group_id: Mapped[uuid.UUID | None] = _fk("account_group.id")
    content_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    remind_at: Mapped[datetime | None] = _ts()
    escalate_at: Mapped[datetime | None] = _ts()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reminded_at: Mapped[datetime | None] = _ts()
    escalated_at: Mapped[datetime | None] = _ts()
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    approved_at: Mapped[datetime | None] = _ts()
    decided_at: Mapped[datetime | None] = _ts()
    superseded_by: Mapped[uuid.UUID | None] = _fk("approval_request.id")
    scheduled_for: Mapped[datetime | None] = _ts()
    requested_by_user_id: Mapped[uuid.UUID | None] = _fk("user.id")
    created_at: Mapped[datetime] = _now()


class ApprovalVariantState(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "approval_variant_state"

    approval_request_id: Mapped[uuid.UUID] = _fk("approval_request.id", "CASCADE", False)
    post_variant_id: Mapped[uuid.UUID] = _fk("post_variant.id", "CASCADE", False)
    post_version_id: Mapped[uuid.UUID] = _fk("post_version.id", "RESTRICT", False)
    social_account_id: Mapped[uuid.UUID] = _fk("social_account.id", "CASCADE", False)
    content_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    required_count: Mapped[int] = mapped_column(Integer, nullable=False)
    quorum_mode: Mapped[str] = mapped_column(Text, nullable=False)
    approve_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    reject_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    decided_at: Mapped[datetime | None] = _ts()


class ApprovalDecision(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "approval_decision"

    approval_request_id: Mapped[uuid.UUID] = _fk("approval_request.id", "CASCADE", False)
    approver_user_id: Mapped[uuid.UUID | None] = _fk("user.id")
    channel_type: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    instruction_text: Mapped[str | None] = mapped_column(Text)
    variant_scope: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    token_jti: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(INET)
    decided_at: Mapped[datetime] = _now()


class ApprovalToken(TenantScoped, Base):
    __tablename__ = "approval_token"

    jti: Mapped[str] = mapped_column(Text, primary_key=True)
    approval_request_id: Mapped[uuid.UUID] = _fk("approval_request.id", "CASCADE", False)
    approver_user_id: Mapped[uuid.UUID] = _fk("user.id", "CASCADE", False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    variant_scope: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    content_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = _ts()
    revoked_at: Mapped[datetime | None] = _ts()


class AutomationState(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "automation_state"

    scope_type: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    autopilot_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    assisted_confidence_threshold: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    kill_switch_engaged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    kill_switch_by_user_id: Mapped[uuid.UUID | None] = _fk("user.id")
    kill_switch_surface: Mapped[str | None] = mapped_column(Text)
    kill_switch_at: Mapped[datetime | None] = _ts()
    kill_switch_reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _now()


class AutopilotAuthorization(UUIDPrimaryKey, TenantScoped, Base):
    """A post allowed to publish without a person (P3-M3, migration 0027)."""

    __tablename__ = "autopilot_authorization"

    approval_request_id: Mapped[uuid.UUID] = _fk("approval_request.id", "CASCADE", False)
    post_variant_id: Mapped[uuid.UUID] = _fk("post_variant.id", "CASCADE", False)
    post_version_id: Mapped[uuid.UUID] = _fk("post_version.id", "RESTRICT", False)
    social_account_id: Mapped[uuid.UUID] = _fk("social_account.id", "CASCADE", False)
    content_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Double)
    reasons: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    revoked_at: Mapped[datetime | None] = _ts()
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
