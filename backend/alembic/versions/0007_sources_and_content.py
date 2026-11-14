"""Sources, content items, pillars, per-group scores (M4.1) and agent runs (M4.11).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import enable_rls

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)
_DIM = 1536
_SOURCE_TYPES = (
    "'rss', 'newsapi', 'gnews', 'google_news', 'reddit', 'youtube', 'hackernews', 'email', 'mcp'"
)


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _ws_fk() -> sa.Column:
    return sa.Column(
        "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )


def _jsonb(name: str, default: str = "'{}'::jsonb") -> sa.Column:
    return sa.Column(name, pg.JSONB, nullable=False, server_default=sa.text(default))


def _texts(name: str) -> sa.Column:
    return sa.Column(name, pg.ARRAY(sa.Text), nullable=False, server_default="{}")


def _same_workspace_trigger(table: str, checks: list[tuple[str, str, bool]]) -> None:
    """Each (column, parent table, nullable) must point at a row in the same workspace
    (docs/03 §11: RLS hides other tenants' rows, but a raw id could still be inserted)."""
    conditions = []
    for column, parent, nullable in checks:
        exists = (
            f"NOT EXISTS (SELECT 1 FROM {parent} p "
            f"WHERE p.id = NEW.{column} AND p.workspace_id = NEW.workspace_id)"
        )
        conditions.append(f"(NEW.{column} IS NOT NULL AND {exists})" if nullable else exists)
    fn = f"mera_{table}_same_workspace"
    op.execute(
        f"""
        CREATE FUNCTION {fn}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF {" OR ".join(conditions)} THEN
            RAISE EXCEPTION '{table} crosses workspaces';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        f"CREATE TRIGGER {table}_same_workspace BEFORE INSERT OR UPDATE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {fn}()"
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "source",
        sa.Column("id", _UUID, primary_key=True),
        _ws_fk(),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        _jsonb("config"),
        _texts("include_keywords"),
        _texts("exclude_keywords"),
        sa.Column("language", sa.Text),
        sa.Column("region", sa.Text),
        sa.Column("fetch_interval_seconds", sa.Integer, nullable=False, server_default="3600"),
        sa.Column("weight", sa.Double, nullable=False, server_default="1.0"),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("http_etag", sa.Text),
        sa.Column("http_last_modified", sa.Text),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.Column("last_http_status", sa.Integer),
        sa.Column("last_yield", sa.Integer),
        sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failing_since", sa.DateTime(timezone=True)),
        sa.Column("next_fetch_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint(f"source_type IN ({_SOURCE_TYPES})", name="ck_source_type"),
        sa.CheckConstraint("status IN ('active', 'paused', 'failing')", name="ck_source_status"),
        sa.CheckConstraint(
            "fetch_interval_seconds BETWEEN 300 AND 604800", name="ck_source_fetch_interval"
        ),
        sa.CheckConstraint("weight >= 0 AND weight <= 4", name="ck_source_weight"),
    )
    op.create_index("ix_source_workspace_id", "source", ["workspace_id"])
    # The service checks first for a friendly error; the index makes concurrent creates safe.
    op.execute(
        "CREATE UNIQUE INDEX uq_source_canonical_url ON source "
        "(workspace_id, (config->>'canonical_url'))"
    )
    op.create_index(
        "ix_source_due",
        "source",
        ["next_fetch_at"],
        postgresql_where=sa.text("status <> 'paused' AND next_fetch_at IS NOT NULL"),
    )
    enable_rls("source")

    op.create_table(
        "source_group_link",
        sa.Column("id", _UUID, primary_key=True),
        _ws_fk(),
        sa.Column(
            "source_id", _UUID, sa.ForeignKey("source.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "account_group_id",
            _UUID,
            sa.ForeignKey("account_group.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _now("created_at"),
        sa.UniqueConstraint("source_id", "account_group_id", name="uq_source_group_link"),
    )
    op.create_index("ix_source_group_link_group", "source_group_link", ["account_group_id"])
    enable_rls("source_group_link")
    _same_workspace_trigger(
        "source_group_link",
        [("source_id", "source", False), ("account_group_id", "account_group", False)],
    )

    op.create_table(
        "content_item",
        sa.Column("id", _UUID, primary_key=True),
        _ws_fk(),
        sa.Column("source_id", _UUID, sa.ForeignKey("source.id", ondelete="SET NULL")),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("canonical_url", sa.Text, nullable=False),
        sa.Column("url_hash", sa.LargeBinary, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("author", sa.Text),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("extracted_text", sa.Text),
        sa.Column("summary", sa.Text),
        sa.Column("language", sa.Text),
        _jsonb("topics", "'[]'::jsonb"),
        sa.Column("embedding", Vector(_DIM)),
        sa.Column("relevance_score", sa.Double),
        sa.Column("freshness_score", sa.Double),
        sa.Column("engagement_potential", sa.Double),
        sa.Column("engagement_rationale", sa.Text),
        _texts("risk_flags"),
        sa.Column("duplicate_of", _UUID, sa.ForeignKey("content_item.id", ondelete="SET NULL")),
        sa.Column("duplicate_similarity", sa.Double),
        sa.Column("seen_source_ids", pg.ARRAY(_UUID), nullable=False, server_default="{}"),
        sa.Column("og_image_url", sa.Text),
        _jsonb("provenance"),
        sa.Column("state", sa.Text, nullable=False, server_default="pending"),
        sa.Column("state_reason", sa.Text),
        sa.Column("enrich_attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("enriched_at", sa.DateTime(timezone=True)),
        sa.Column("dismissed_at", sa.DateTime(timezone=True)),
        sa.Column("dismissed_by", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        _now("fetched_at"),
        sa.UniqueConstraint("workspace_id", "url_hash", name="uq_content_item_url"),
        sa.CheckConstraint(
            "state IN ('pending', 'ready', 'duplicate', 'filtered', 'failed')",
            name="ck_content_item_state",
        ),
        sa.CheckConstraint(
            # One direction only: deleting the original sets duplicate_of NULL (ON DELETE
            # SET NULL) and must not then violate the check.
            "duplicate_of IS NULL OR state = 'duplicate'",
            name="ck_content_item_duplicate",
        ),
    )
    op.create_index(
        "ix_content_item_workspace_fetched",
        "content_item",
        ["workspace_id", sa.text("fetched_at DESC")],
    )
    op.create_index(
        "ix_content_item_pending",
        "content_item",
        ["workspace_id", "fetched_at"],
        postgresql_where=sa.text("state = 'pending'"),
    )
    op.create_index("ix_content_item_source", "content_item", ["source_id", "fetched_at"])
    op.create_index("ix_content_item_duplicate_of", "content_item", ["duplicate_of"])
    # docs/03 §12 and ADR-0007. The dedupe query itself scans the 14-day window exactly
    # (see application/ingest); the index serves nearest-neighbour lookups at larger scale.
    op.execute(
        "CREATE INDEX ix_content_item_embedding ON content_item "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    enable_rls("content_item")
    _same_workspace_trigger(
        "content_item", [("source_id", "source", True), ("duplicate_of", "content_item", True)]
    )

    op.create_table(
        "content_pillar",
        sa.Column("id", _UUID, primary_key=True),
        _ws_fk(),
        sa.Column("account_group_id", _UUID, sa.ForeignKey("account_group.id", ondelete="CASCADE")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        _texts("keywords"),
        sa.Column("embedding", Vector(_DIM)),
        sa.Column("embedding_model", sa.Text),
        sa.Column("weight", sa.Double, nullable=False, server_default="1.0"),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint("weight >= 0 AND weight <= 4", name="ck_content_pillar_weight"),
    )
    op.create_index(
        "ix_content_pillar_workspace", "content_pillar", ["workspace_id", "account_group_id"]
    )
    enable_rls("content_pillar")
    _same_workspace_trigger("content_pillar", [("account_group_id", "account_group", True)])

    op.create_table(
        "content_item_score",
        sa.Column("id", _UUID, primary_key=True),
        _ws_fk(),
        sa.Column(
            "content_item_id",
            _UUID,
            sa.ForeignKey("content_item.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_group_id",
            _UUID,
            sa.ForeignKey("account_group.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relevance_score", sa.Double, nullable=False),
        _jsonb("pillar_scores"),
        _jsonb("components"),
        _now("computed_at"),
        sa.UniqueConstraint("content_item_id", "account_group_id", name="uq_content_item_score"),
    )
    op.create_index(
        "ix_content_item_score_group",
        "content_item_score",
        ["account_group_id", sa.text("relevance_score DESC")],
    )
    enable_rls("content_item_score")
    _same_workspace_trigger(
        "content_item_score",
        [("content_item_id", "content_item", False), ("account_group_id", "account_group", False)],
    )

    op.create_table(
        "agent_run",
        sa.Column("id", _UUID, primary_key=True),
        _ws_fk(),
        sa.Column("parent_run_id", _UUID, sa.ForeignKey("agent_run.id", ondelete="SET NULL")),
        sa.Column("workflow_key", sa.Text, nullable=False),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column("subject_type", sa.Text),
        sa.Column("subject_id", _UUID),
        sa.Column("state", sa.Text, nullable=False, server_default="running"),
        sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(14, 8), nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        _now("started_at"),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'awaiting_human', 'succeeded', 'failed', 'aborted')",
            name="ck_agent_run_state",
        ),
        sa.CheckConstraint(
            "trigger IN ('schedule', 'event', 'manual', 'mcp', 'bot')", name="ck_agent_run_trigger"
        ),
    )
    op.create_index(
        "ix_agent_run_workspace_started", "agent_run", ["workspace_id", sa.text("started_at DESC")]
    )
    enable_rls("agent_run")
    _same_workspace_trigger("agent_run", [("parent_run_id", "agent_run", True)])

    op.create_table(
        "agent_step",
        sa.Column("id", _UUID, primary_key=True),
        _ws_fk(),
        sa.Column(
            "agent_run_id", _UUID, sa.ForeignKey("agent_run.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("agent_key", sa.Text, nullable=False),
        sa.Column("step_type", sa.Text, nullable=False),
        sa.Column("post_variant_id", _UUID),
        sa.Column("subject_type", sa.Text),
        sa.Column("subject_id", _UUID),
        _jsonb("input_redacted"),
        _jsonb("output"),
        sa.Column("prompt_object_key", sa.Text),
        sa.Column("model", sa.Text),
        sa.Column("fallback_from", sa.Text),
        sa.Column("price_source", sa.Text),
        sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(14, 8), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("error", sa.Text),
        _now("started_at"),
        sa.UniqueConstraint("agent_run_id", "seq", name="uq_agent_step_seq"),
        sa.CheckConstraint(
            "step_type IN ('llm_call', 'embedding', 'tool_call', 'guardrail', 'handoff', "
            "'human_checkpoint')",
            name="ck_agent_step_type",
        ),
    )
    # The per-agent daily cost cap sums today's steps (docs/03 §9).
    op.create_index("ix_agent_step_cost", "agent_step", ["workspace_id", "agent_key", "started_at"])
    enable_rls("agent_step")
    _same_workspace_trigger("agent_step", [("agent_run_id", "agent_run", False)])


def downgrade() -> None:
    for table in (
        "agent_step",
        "agent_run",
        "content_item_score",
        "content_pillar",
        "content_item",
        "source_group_link",
        "source",
    ):
        op.drop_table(table)
        op.execute(f"DROP FUNCTION IF EXISTS mera_{table}_same_workspace()")
    # The vector extension stays: dropping it could break other databases' expectations
    # and costs nothing to keep.
