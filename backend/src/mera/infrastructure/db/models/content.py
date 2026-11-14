"""Sources, content items, pillars, per-group scores (M4, docs/03 §5) and agent runs (docs/03 §9).

Scores and weights are double precision: they are ranking inputs, not money. Costs are
``numeric`` (docs/06 §8: spend must not drift with float rounding).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Double,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
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


def _texts() -> Any:
    return mapped_column(ARRAY(Text), nullable=False, server_default="{}")


class Source(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    __tablename__ = "source"

    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict[str, Any]] = _jsonb()
    include_keywords: Mapped[list[str]] = _texts()
    exclude_keywords: Mapped[list[str]] = _texts()
    language: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)
    fetch_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="3600"
    )
    weight: Mapped[float] = mapped_column(Double, nullable=False, server_default="1.0")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    # Conditional-request validators, echoed back exactly as the server sent them.
    http_etag: Mapped[str | None] = mapped_column(Text)
    http_last_modified: Mapped[str | None] = mapped_column(Text)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    last_yield: Mapped[int | None] = mapped_column(Integer)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )


class SourceGroupLink(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "source_group_link"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source.id", ondelete="CASCADE"), nullable=False
    )
    account_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account_group.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ContentItem(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "content_item"

    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source.id", ondelete="SET NULL")
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    url_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extracted_text: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    topics: Mapped[list[str]] = _jsonb("'[]'::jsonb")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    relevance_score: Mapped[float | None] = mapped_column(Double)
    freshness_score: Mapped[float | None] = mapped_column(Double)
    engagement_potential: Mapped[float | None] = mapped_column(Double)
    engagement_rationale: Mapped[str | None] = mapped_column(Text)
    risk_flags: Mapped[list[str]] = _texts()
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_item.id", ondelete="SET NULL")
    )
    duplicate_similarity: Mapped[float | None] = mapped_column(Double)
    # Every source that delivered this URL (exact duplicates are not stored twice, but
    # "covered by N sources" is itself a relevance signal, docs/03 §5).
    seen_source_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    og_image_url: Mapped[str | None] = mapped_column(Text)
    provenance: Mapped[dict[str, Any]] = _jsonb()
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    state_reason: Mapped[str | None] = mapped_column(Text)
    enrich_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ContentPillar(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    __tablename__ = "content_pillar"

    account_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account_group.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[list[str]] = _texts()
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(Text)
    weight: Mapped[float] = mapped_column(Double, nullable=False, server_default="1.0")


class ContentItemScore(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "content_item_score"

    content_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_item.id", ondelete="CASCADE"), nullable=False
    )
    account_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account_group.id", ondelete="CASCADE"), nullable=False
    )
    relevance_score: Mapped[float] = mapped_column(Double, nullable=False)
    pillar_scores: Mapped[dict[str, float]] = _jsonb()
    components: Mapped[dict[str, Any]] = _jsonb()
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentRun(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "agent_run"

    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_run.id", ondelete="SET NULL")
    )
    workflow_key: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    subject_type: Mapped[str | None] = mapped_column(Text)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="running")
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), nullable=False, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentStep(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "agent_step"

    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_run.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_key: Mapped[str] = mapped_column(Text, nullable=False)
    step_type: Mapped[str] = mapped_column(Text, nullable=False)
    post_variant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # FK in M5
    subject_type: Mapped[str | None] = mapped_column(Text)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    input_redacted: Mapped[dict[str, Any]] = _jsonb()
    output: Mapped[dict[str, Any]] = _jsonb()
    prompt_object_key: Mapped[str | None] = mapped_column(Text)  # object store arrives in M5
    model: Mapped[str | None] = mapped_column(Text)
    fallback_from: Mapped[str | None] = mapped_column(Text)
    price_source: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), nullable=False, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    state: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Insight(UUIDPrimaryKey, TenantScoped, Base):
    """One finding of the weekly analytics run (P3-M1.4, migration 0024)."""

    __tablename__ = "insight"

    account_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account_group.id", ondelete="CASCADE")
    )
    social_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("social_account.id", ondelete="CASCADE")
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = _jsonb()
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_run.id", ondelete="SET NULL")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WeightChange(UUIDPrimaryKey, TenantScoped, Base):
    """A move of a source or pillar weight by the feedback loop (P3-M1.5)."""

    __tablename__ = "weight_change"

    subject_type: Mapped[str] = mapped_column(Text, nullable=False)  # source, pillar
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    before: Mapped[float] = mapped_column(Double, nullable=False)
    after: Mapped[float] = mapped_column(Double, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    insight_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("insight.id", ondelete="SET NULL")
    )
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reverted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
