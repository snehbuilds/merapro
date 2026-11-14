"""Brand voices, post sets, variants, immutable versions, guardrail results (M5.2-5.4).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import enable_rls

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)
_ZERO = "'00000000-0000-0000-0000-000000000000'::uuid"


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _ws_fk() -> sa.Column:
    return sa.Column(
        "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )


def _jsonb(name: str, default: str = "'{}'::jsonb") -> sa.Column:
    return sa.Column(name, pg.JSONB, nullable=False, server_default=sa.text(default))


def _fk(name: str, target: str, ondelete: str = "SET NULL", nullable: bool = True) -> sa.Column:
    return sa.Column(name, _UUID, sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)


def _same_workspace_trigger(table: str, checks: list[tuple[str, str, bool]]) -> None:
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
    op.create_table(
        "brand_voice",
        sa.Column("id", _UUID, primary_key=True),
        _ws_fk(),
        sa.Column("scope_type", sa.Text, nullable=False),
        sa.Column("scope_id", _UUID),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("tone", sa.Text),
        sa.Column("target_audience", sa.Text),
        sa.Column("cta_style", sa.Text),
        sa.Column("emoji_policy", sa.Text, nullable=False, server_default="sparing"),
        sa.Column("banned_words", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        _jsonb("example_posts", "'[]'::jsonb"),
        _jsonb("learned_preferences", "'[]'::jsonb"),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        _fk("created_by", "user.id"),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint(
            "scope_type IN ('workspace', 'account_group', 'social_account')",
            name="ck_brand_voice_scope",
        ),
        sa.CheckConstraint(
            "(scope_type = 'workspace') = (scope_id IS NULL)", name="ck_brand_voice_scope_id"
        ),
        sa.CheckConstraint(
            "emoji_policy IN ('none', 'sparing', 'liberal')", name="ck_brand_voice_emoji"
        ),
    )
    op.create_index(
        "ix_brand_voice_scope", "brand_voice", ["workspace_id", "scope_type", "scope_id"]
    )
    # One default voice per scope (docs/03 §6).
    op.execute(
        "CREATE UNIQUE INDEX uq_brand_voice_default ON brand_voice "
        f"(workspace_id, scope_type, COALESCE(scope_id, {_ZERO})) WHERE is_default"
    )
    enable_rls("brand_voice")
    op.execute(
        """
        CREATE FUNCTION mera_brand_voice_scope() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF (NEW.scope_type = 'account_group' AND NOT EXISTS (
                SELECT 1 FROM account_group g
                WHERE g.id = NEW.scope_id AND g.workspace_id = NEW.workspace_id))
             OR (NEW.scope_type = 'social_account' AND NOT EXISTS (
                SELECT 1 FROM social_account a
                WHERE a.id = NEW.scope_id AND a.workspace_id = NEW.workspace_id)) THEN
            RAISE EXCEPTION 'brand_voice scope crosses workspaces';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER brand_voice_scope BEFORE INSERT OR UPDATE ON brand_voice "
        "FOR EACH ROW EXECUTE FUNCTION mera_brand_voice_scope()"
    )
    # The default-voice columns left open in M3 now get their foreign keys.
    for table in ("social_account", "account_group"):
        op.create_foreign_key(
            f"fk_{table}_default_brand_voice",
            table,
            "brand_voice",
            ["default_brand_voice_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "post_set",
        sa.Column("id", _UUID, primary_key=True),
        _ws_fk(),
        _fk("content_item_id", "content_item.id"),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("topic_brief", sa.Text),
        sa.Column("origin", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default="drafting"),
        sa.Column("target_account_group_ids", pg.ARRAY(_UUID), nullable=False, server_default="{}"),
        sa.Column(
            "target_social_account_ids", pg.ARRAY(_UUID), nullable=False, server_default="{}"
        ),
        sa.Column("composite_hash", sa.LargeBinary),
        sa.Column("generation_status", sa.Text, nullable=False, server_default="idle"),
        sa.Column("generation_error", sa.Text),
        sa.Column("generation_requested_at", sa.DateTime(timezone=True)),
        _fk("last_run_id", "agent_run.id"),
        _fk("created_by_user_id", "user.id"),
        _fk("created_by_agent_run_id", "agent_run.id"),
        sa.Column("is_evergreen", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("recycle_after", sa.DateTime(timezone=True)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint(
            "origin IN ('agent', 'manual', 'mcp', 'bot', 'recycled')", name="ck_post_set_origin"
        ),
        sa.CheckConstraint(
            "state IN ('drafting', 'needs_approval', 'partially_approved', 'scheduled', "
            "'published', 'partially_published', 'rejected', 'blocked', 'archived')",
            name="ck_post_set_state",
        ),
        sa.CheckConstraint(
            "generation_status IN ('idle', 'queued', 'running', 'failed')",
            name="ck_post_set_generation",
        ),
    )
    op.create_index(
        "ix_post_set_workspace_created", "post_set", ["workspace_id", sa.text("created_at DESC")]
    )
    op.create_index("ix_post_set_content_item", "post_set", ["content_item_id"])
    enable_rls("post_set")
    _same_workspace_trigger(
        "post_set",
        [
            ("content_item_id", "content_item", True),
            ("last_run_id", "agent_run", True),
            ("created_by_agent_run_id", "agent_run", True),
        ],
    )

    op.create_table(
        "post_variant",
        sa.Column("id", _UUID, primary_key=True),
        _ws_fk(),
        _fk("post_set_id", "post_set.id", "CASCADE", nullable=False),
        _fk("social_account_id", "social_account.id", "CASCADE", nullable=False),
        _fk("via_account_group_id", "account_group.id"),
        sa.Column("platform", sa.Text, nullable=False),
        _fk("brand_voice_id", "brand_voice.id"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("state", sa.Text, nullable=False, server_default="drafting"),
        sa.Column("current_version_id", _UUID),  # FK added after post_version exists
        sa.Column("variant_label", sa.Text),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("scheduled_for", sa.DateTime(timezone=True)),
        sa.Column("stagger_offset_seconds", sa.Integer, nullable=False, server_default="0"),
        _now("created_at"),
        _now("updated_at"),
        sa.UniqueConstraint("post_set_id", "social_account_id", name="uq_post_variant_account"),
        sa.CheckConstraint(
            "state IN ('drafting', 'needs_approval', 'approved', 'scheduled', 'publishing', "
            "'published', 'failed', 'rejected', 'blocked', 'skipped')",
            name="ck_post_variant_state",
        ),
    )
    op.create_index("ix_post_variant_set", "post_variant", ["post_set_id", "position"])
    op.create_index("ix_post_variant_account", "post_variant", ["social_account_id"])
    enable_rls("post_variant")

    op.create_table(
        "post_version",
        sa.Column("id", _UUID, primary_key=True),
        _ws_fk(),
        _fk("post_variant_id", "post_variant.id", "CASCADE", nullable=False),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("content_hash", sa.LargeBinary, nullable=False),
        _fk("parent_version_id", "post_version.id"),
        sa.Column("revision_instruction", sa.Text),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("thread_parts", pg.JSONB),
        sa.Column("hashtags", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("link_url", sa.Text),
        _jsonb("media", "'[]'::jsonb"),
        sa.Column("confidence_score", sa.Double),
        _jsonb("compliance_flags", "'[]'::jsonb"),
        _jsonb("validation", "'[]'::jsonb"),
        _jsonb("agent_notes"),
        _jsonb("hashtag_suggestions", "'[]'::jsonb"),
        sa.Column("embedding", Vector(1536)),
        sa.Column("source", sa.Text, nullable=False),
        _fk("created_by_agent_run_id", "agent_run.id"),
        _fk("created_by_user_id", "user.id"),
        _now("created_at"),
        sa.UniqueConstraint("post_variant_id", "version_no", name="uq_post_version_no"),
        sa.CheckConstraint("source IN ('agent', 'human')", name="ck_post_version_source"),
        sa.CheckConstraint("octet_length(content_hash) = 32", name="ck_post_version_hash"),
    )
    enable_rls("post_version")
    op.create_foreign_key(
        "fk_post_variant_current_version",
        "post_variant",
        "post_version",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    _same_workspace_trigger(
        "post_variant",
        [
            ("post_set_id", "post_set", False),
            ("social_account_id", "social_account", False),
            ("via_account_group_id", "account_group", True),
            ("brand_voice_id", "brand_voice", True),
            ("current_version_id", "post_version", True),
        ],
    )
    _same_workspace_trigger(
        "post_version",
        [
            ("post_variant_id", "post_variant", False),
            ("parent_version_id", "post_version", True),
            ("created_by_agent_run_id", "agent_run", True),
        ],
    )
    # A variant's identity is fixed (the content hash binds its platform and account), and
    # its current version must be one of its own versions (review M6).
    op.execute(
        """
        CREATE FUNCTION mera_post_variant_integrity() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'UPDATE' AND (NEW.post_set_id <> OLD.post_set_id
              OR NEW.social_account_id <> OLD.social_account_id
              OR NEW.platform <> OLD.platform) THEN
            RAISE EXCEPTION 'post_variant set, account and platform are fixed';
          END IF;
          IF NEW.current_version_id IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM post_version v
              WHERE v.id = NEW.current_version_id AND v.post_variant_id = NEW.id) THEN
            RAISE EXCEPTION 'post_variant current_version_id must be one of its own versions';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER post_variant_integrity BEFORE INSERT OR UPDATE ON post_variant "
        "FOR EACH ROW EXECUTE FUNCTION mera_post_variant_integrity()"
    )
    # ADR-0004: versions are insert-only. The only permitted UPDATE is the database itself
    # nulling a reference to a deleted user, agent run or parent (ON DELETE SET NULL runs as
    # a nested trigger, so pg_trigger_depth() > 1); every content column must be unchanged.
    # A DELETE is allowed only as a cascade from its variant, never directly.
    op.execute(
        """
        CREATE FUNCTION mera_post_version_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          refs text[] := ARRAY['created_by_user_id', 'created_by_agent_run_id', 'parent_version_id'];
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF pg_trigger_depth() > 1 THEN
              RETURN OLD;
            END IF;
            RAISE EXCEPTION 'post_version rows are immutable (delete)';
          END IF;
          IF pg_trigger_depth() > 1
             AND (to_jsonb(NEW) - refs) = (to_jsonb(OLD) - refs)
             AND (NEW.created_by_user_id IS NULL OR NEW.created_by_user_id = OLD.created_by_user_id)
             AND (NEW.created_by_agent_run_id IS NULL
                  OR NEW.created_by_agent_run_id = OLD.created_by_agent_run_id)
             AND (NEW.parent_version_id IS NULL OR NEW.parent_version_id = OLD.parent_version_id)
          THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'post_version rows are immutable (update)';
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER post_version_immutable BEFORE UPDATE OR DELETE ON post_version "
        "FOR EACH ROW EXECUTE FUNCTION mera_post_version_immutable()"
    )

    op.create_table(
        "guardrail_result",
        sa.Column("id", _UUID, primary_key=True),
        _ws_fk(),
        _fk("agent_run_id", "agent_run.id"),
        sa.Column("subject_type", sa.Text, nullable=False),
        sa.Column("subject_id", _UUID, nullable=False),
        _fk("post_set_id", "post_set.id", "CASCADE", nullable=False),
        _fk("post_variant_id", "post_variant.id", "CASCADE"),
        _fk("post_version_id", "post_version.id", "CASCADE"),
        sa.Column("guardrail_key", sa.Text, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("score", sa.Double),
        _jsonb("details"),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        _now("created_at"),
        sa.CheckConstraint("outcome IN ('pass', 'warn', 'block')", name="ck_guardrail_outcome"),
        sa.CheckConstraint(
            "subject_type IN ('post_variant', 'post_set')", name="ck_guardrail_subject"
        ),
    )
    op.create_index("ix_guardrail_result_set", "guardrail_result", ["post_set_id", "created_at"])
    op.create_index("ix_guardrail_result_version", "guardrail_result", ["post_version_id"])
    enable_rls("guardrail_result")
    _same_workspace_trigger(
        "guardrail_result",
        [
            ("post_set_id", "post_set", False),
            ("post_variant_id", "post_variant", True),
            ("post_version_id", "post_version", True),
            ("agent_run_id", "agent_run", True),
        ],
    )
    # Results are evidence: only ``superseded_at`` may be set (once); the version must belong
    # to the variant and the variant to the set (review M6).
    op.execute(
        """
        CREATE FUNCTION mera_guardrail_result_integrity() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            IF pg_trigger_depth() > 1 THEN
              RETURN NEW;  -- ON DELETE SET NULL of a referenced agent run
            END IF;
            IF (to_jsonb(NEW) - 'superseded_at') <> (to_jsonb(OLD) - 'superseded_at')
               OR OLD.superseded_at IS NOT NULL THEN
              RAISE EXCEPTION 'guardrail_result rows are immutable';
            END IF;
            RETURN NEW;
          END IF;
          IF NEW.post_variant_id IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM post_variant pv
              WHERE pv.id = NEW.post_variant_id AND pv.post_set_id = NEW.post_set_id) THEN
            RAISE EXCEPTION 'guardrail_result variant is not in its set';
          END IF;
          IF NEW.post_version_id IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM post_version v
              WHERE v.id = NEW.post_version_id AND v.post_variant_id = NEW.post_variant_id) THEN
            RAISE EXCEPTION 'guardrail_result version is not the variant''s';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER guardrail_result_integrity BEFORE INSERT OR UPDATE ON guardrail_result "
        "FOR EACH ROW EXECUTE FUNCTION mera_guardrail_result_integrity()"
    )

    # Per-account cost attribution (docs/03 §9): the step's variant is now a real reference.
    op.create_foreign_key(
        "fk_agent_step_post_variant",
        "agent_step",
        "post_variant",
        ["post_variant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_agent_step_variant", "agent_step", ["post_variant_id"])
    # The cross-workspace checks M3/M4 tables lacked for their new references.
    op.execute(
        """
        CREATE FUNCTION mera_agent_step_variant_same_workspace() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.post_variant_id IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM post_variant p
              WHERE p.id = NEW.post_variant_id AND p.workspace_id = NEW.workspace_id) THEN
            RAISE EXCEPTION 'agent_step crosses workspaces';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER agent_step_variant_same_workspace BEFORE INSERT OR UPDATE ON agent_step "
        "FOR EACH ROW EXECUTE FUNCTION mera_agent_step_variant_same_workspace()"
    )
    op.execute(
        """
        CREATE FUNCTION mera_default_voice_same_workspace() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.default_brand_voice_id IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM brand_voice v
              WHERE v.id = NEW.default_brand_voice_id AND v.workspace_id = NEW.workspace_id) THEN
            RAISE EXCEPTION '% default voice crosses workspaces', TG_TABLE_NAME;
          END IF;
          RETURN NEW;
        END $$
        """
    )
    for table in ("social_account", "account_group"):
        op.execute(
            f"CREATE TRIGGER {table}_default_voice_same_workspace BEFORE INSERT OR UPDATE "
            f"OF default_brand_voice_id ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION mera_default_voice_same_workspace()"
        )


def downgrade() -> None:
    for table in ("social_account", "account_group"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_default_voice_same_workspace ON {table}")
    op.execute("DROP FUNCTION IF EXISTS mera_default_voice_same_workspace()")
    op.execute("DROP TRIGGER IF EXISTS agent_step_variant_same_workspace ON agent_step")
    op.execute("DROP FUNCTION IF EXISTS mera_agent_step_variant_same_workspace()")
    op.drop_index("ix_agent_step_variant", table_name="agent_step")
    op.drop_constraint("fk_agent_step_post_variant", "agent_step", type_="foreignkey")
    op.drop_table("guardrail_result")
    op.execute("DROP FUNCTION IF EXISTS mera_guardrail_result_same_workspace()")
    op.execute("DROP FUNCTION IF EXISTS mera_guardrail_result_integrity()")
    op.execute("DROP TRIGGER IF EXISTS post_variant_integrity ON post_variant")
    op.execute("DROP FUNCTION IF EXISTS mera_post_variant_integrity()")
    op.drop_constraint("fk_post_variant_current_version", "post_variant", type_="foreignkey")
    op.drop_table("post_version")
    op.execute("DROP FUNCTION IF EXISTS mera_post_version_immutable()")
    op.execute("DROP FUNCTION IF EXISTS mera_post_version_same_workspace()")
    op.drop_table("post_variant")
    op.execute("DROP FUNCTION IF EXISTS mera_post_variant_same_workspace()")
    op.drop_table("post_set")
    op.execute("DROP FUNCTION IF EXISTS mera_post_set_same_workspace()")
    for table in ("social_account", "account_group"):
        op.drop_constraint(f"fk_{table}_default_brand_voice", table, type_="foreignkey")
    op.drop_table("brand_voice")
    op.execute("DROP FUNCTION IF EXISTS mera_brand_voice_scope()")
