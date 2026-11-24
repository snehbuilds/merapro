"""Brand voices, post sets, per-account variants, immutable versions, guardrail results
(M5, docs/03 §6 and §9, ADR-0004, ADR-0011)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Double,
    ForeignKey,
    Integer,
    LargeBinary,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mera.domain.value_objects.content import EMBEDDING_DIM
from mera.infrastructure.db.base import Base, TenantScoped, Timestamps, UUIDPrimaryKey


def _jsonb(default: str = "'{}'::jsonb") -> Any:
    return mapped_column(JSONB, nullable=False, server_default=text(default))


def _uuid_fk(target: str, ondelete: str = "SET NULL", nullable: bool = True) -> Any:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable
    )


class BrandVoice(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    __tablename__ = "brand_voice"

    scope_type: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    tone: Mapped[str | None] = mapped_column(Text)
    target_audience: Mapped[str | None] = mapped_column(Text)
    cta_style: Mapped[str | None] = mapped_column(Text)
    emoji_policy: Mapped[str] = mapped_column(Text, nullable=False, server_default="sparing")
    banned_words: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    example_posts: Mapped[list[str]] = _jsonb("'[]'::jsonb")
    # Account profile for the platform skills: who is posting, and true stories and facts
    # the Writer may use (Sprint 7).
    about: Mapped[str | None] = mapped_column(Text)
    story_bank: Mapped[list[str]] = _jsonb("'[]'::jsonb")
    learned_preferences: Mapped[list[dict[str, Any]]] = _jsonb("'[]'::jsonb")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_by: Mapped[uuid.UUID | None] = _uuid_fk("user.id")


class PostSet(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    __tablename__ = "post_set"

    content_item_id: Mapped[uuid.UUID | None] = _uuid_fk("content_item.id")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    topic_brief: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    # A/B (P3-M2, migration 0028): B is its own set, linked to A's.
    ab_parent_id: Mapped[uuid.UUID | None] = _uuid_fk("post_set.id")
    ab_label: Mapped[str | None] = mapped_column(Text)
    ab_hypothesis: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="drafting")
    target_account_group_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    target_social_account_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    composite_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    generation_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="idle")
    generation_error: Mapped[str | None] = mapped_column(Text)
    generation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_id: Mapped[uuid.UUID | None] = _uuid_fk("agent_run.id")
    created_by_user_id: Mapped[uuid.UUID | None] = _uuid_fk("user.id")
    created_by_agent_run_id: Mapped[uuid.UUID | None] = _uuid_fk("agent_run.id")
    is_evergreen: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    recycle_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publish_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    requested_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_timezone: Mapped[str | None] = mapped_column(Text)


class PostVariant(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    __tablename__ = "post_variant"

    post_set_id: Mapped[uuid.UUID] = _uuid_fk("post_set.id", "CASCADE", nullable=False)
    social_account_id: Mapped[uuid.UUID] = _uuid_fk("social_account.id", "CASCADE", nullable=False)
    via_account_group_id: Mapped[uuid.UUID | None] = _uuid_fk("account_group.id")
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    brand_voice_id: Mapped[uuid.UUID | None] = _uuid_fk("brand_voice.id")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="drafting")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("post_version.id", ondelete="SET NULL", use_alter=True),
    )
    variant_label: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stagger_offset_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class PostVersion(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "post_version"

    post_variant_id: Mapped[uuid.UUID] = _uuid_fk("post_variant.id", "CASCADE", nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    parent_version_id: Mapped[uuid.UUID | None] = _uuid_fk("post_version.id")
    revision_instruction: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    thread_parts: Mapped[list[str] | None] = mapped_column(JSONB)
    hashtags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    link_url: Mapped[str | None] = mapped_column(Text)
    first_comment: Mapped[str | None] = mapped_column(Text)
    media: Mapped[list[dict[str, Any]]] = _jsonb("'[]'::jsonb")
    confidence_score: Mapped[float | None] = mapped_column(Double)
    compliance_flags: Mapped[list[dict[str, Any]]] = _jsonb("'[]'::jsonb")
    validation: Mapped[list[dict[str, Any]]] = _jsonb("'[]'::jsonb")
    # Writer and Editor notes shown to the approver: angle, hook, self-critique, voice match.
    agent_notes: Mapped[dict[str, Any]] = _jsonb()
    hashtag_suggestions: Mapped[list[dict[str, Any]]] = _jsonb("'[]'::jsonb")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    source: Mapped[str] = mapped_column(Text, nullable=False)  # agent or human
    created_by_agent_run_id: Mapped[uuid.UUID | None] = _uuid_fk("agent_run.id")
    created_by_user_id: Mapped[uuid.UUID | None] = _uuid_fk("user.id")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GuardrailResult(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "guardrail_result"

    agent_run_id: Mapped[uuid.UUID | None] = _uuid_fk("agent_run.id")
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)  # post_variant, post_set
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    post_set_id: Mapped[uuid.UUID] = _uuid_fk("post_set.id", "CASCADE", nullable=False)
    post_variant_id: Mapped[uuid.UUID | None] = _uuid_fk("post_variant.id", "CASCADE")
    post_version_id: Mapped[uuid.UUID | None] = _uuid_fk("post_version.id", "CASCADE")
    guardrail_key: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float | None] = mapped_column(Double)
    details: Mapped[dict[str, Any]] = _jsonb()
    # Set-scoped results are re-evaluated on every change; only the latest pass counts.
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
