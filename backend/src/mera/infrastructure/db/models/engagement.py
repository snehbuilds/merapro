"""Comments, replies and engagement rules (P2-M6, docs/03 §8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
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


class Comment(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "comment"

    published_post_id: Mapped[uuid.UUID] = _fk("published_post.id", "CASCADE", False)
    # The account that owns the post: the only identity a reply may go out as.
    social_account_id: Mapped[uuid.UUID] = _fk("social_account.id", "CASCADE", False)
    account_group_id: Mapped[uuid.UUID | None] = _fk("account_group.id")
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    parent_comment_id: Mapped[uuid.UUID | None] = _fk("comment.id")
    platform_comment_id: Mapped[str] = mapped_column(Text, nullable=False)
    platform_parent_id: Mapped[str | None] = mapped_column(Text)
    author_platform_id: Mapped[str | None] = mapped_column(Text)
    author_handle: Mapped[str | None] = mapped_column(Text)
    author_erased_at: Mapped[datetime | None] = _ts()  # the commenter asked (0025)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    permalink: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    sentiment: Mapped[str | None] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(Text)
    toxicity_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    spam_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    language: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="new")
    escalation_reason: Mapped[str | None] = mapped_column(Text)
    matched_rule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    assigned_to_user_id: Mapped[uuid.UUID | None] = _fk("user.id")
    is_own: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at_platform: Mapped[datetime | None] = _ts()
    triaged_at: Mapped[datetime | None] = _ts()
    deleted_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Reply(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "reply"

    comment_id: Mapped[uuid.UUID] = _fk("comment.id", "CASCADE", False)
    social_account_id: Mapped[uuid.UUID] = _fk("social_account.id", "CASCADE", False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="human")
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    agent_run_id: Mapped[uuid.UUID | None] = _fk("agent_run.id")
    created_by_user_id: Mapped[uuid.UUID | None] = _fk("user.id")
    approved_by_user_id: Mapped[uuid.UUID | None] = _fk("user.id")
    approved_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    # The card last sent to approvers (migration 0022): its nonce is in the buttons, and a
    # tap counts only while ``content_hash`` is still ``card_hash``.
    card_nonce: Mapped[str | None] = mapped_column(Text)
    card_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    approved_at: Mapped[datetime | None] = _ts()
    platform_reply_id: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sent_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class EngagementRule(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "engagement_rule"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    scope_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="workspace")
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    match: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    action: Mapped[str] = mapped_column(Text, nullable=False)
    reply_template: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_by_user_id: Mapped[uuid.UUID | None] = _fk("user.id")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
