"""Publishing (M8), approval channels and pairing (M7), events (M9.4), metrics (M10),
the sandbox capture log (M9.17) and the hashtag registry (M5 carry-over).

- ``scheduled_post``: one per approved account. ``idempotency_key`` is unique, so "approve
  twice, schedule twice" is impossible at the database level (docs/03 §8). The anchor
  columns (variant, version, account, key) never change.
- ``published_post``: one per successful publish. ``content_hash`` records exactly which
  bytes went out.
- ``dead_letter``: a publish that will not be retried, with its error, per account.
- ``event``: the SSE stream and its replay log (docs/04 §5).
- ``metric_snapshot``: normalised metrics. Unique per subject, metric and sync window, so a
  re-run of a sync writes nothing new (M10 acceptance). ``value`` NULL is "not reported".
- ``approval_channel``: which channels a workspace uses. The Telegram bot itself is one per
  deployment (``TELEGRAM_BOT_TOKEN``), because one bot serves every workspace (docs/07 §6).
- ``approver_binding``: a chat bound to a **user**, not a workspace (docs/07 §6). Global
  table, readable by its own user, by a workspace that user belongs to, and by the bot
  worker looking up the inbound chat id.
- ``pairing_code``: hashed, single use, ten minutes. ``channel_pairing_attempt``: lockout.
- ``channel_message``: what was sent where, so cards can be edited in place and replies
  correlated (docs/07 §8: correlation state lives here).
- ``channel_update``: inbound update ids already processed (at-least-once delivery).
- ``sandbox_capture``: every fake platform call and channel message, for ``/sandbox``.
- ``hashtag``: the registry (allowed, banned, preferred).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import USER_SETTING_SQL, enable_rls

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)
_TENANT = "NULLIF(current_setting('mera.workspace_id', true), '')::uuid"


def _ws() -> sa.Column:
    return sa.Column(
        "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )


def _fk(name: str, target: str, ondelete: str = "CASCADE", nullable: bool = False) -> sa.Column:
    return sa.Column(name, _UUID, sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True))


def _same_workspace(table: str, checks: list[tuple[str, str]]) -> None:
    conditions = " OR ".join(
        f"(NEW.{col} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {parent} p "
        f"WHERE p.id = NEW.{col} AND p.workspace_id = NEW.workspace_id))"
        for col, parent in checks
    )
    op.execute(
        f"""
        CREATE FUNCTION mera_{table}_same_workspace() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF {conditions} THEN
            RAISE EXCEPTION '{table} crosses workspaces';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        f"CREATE TRIGGER {table}_same_workspace BEFORE INSERT OR UPDATE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION mera_{table}_same_workspace()"
    )


SCHEDULED_STATES = "('queued', 'publishing', 'published', 'failed', 'dead_lettered', 'cancelled')"


def upgrade() -> None:
    # An edit from an approval card re-asks only the revised accounts while the original
    # request stays open for the rest (M6.10), so a set may have several open requests on
    # disjoint accounts. The service enforces disjointness under the post set's row lock.
    op.drop_index("uq_approval_request_open", table_name="approval_request")
    op.create_index(
        "ix_approval_request_open",
        "approval_request",
        ["post_set_id"],
        postgresql_where=sa.text("state IN ('pending', 'awaiting_edit')"),
    )

    # ------------------------------------------------------------ scheduled_post
    op.create_table(
        "scheduled_post",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        _fk("post_set_id", "post_set.id"),
        _fk("post_variant_id", "post_variant.id"),
        _fk("post_version_id", "post_version.id", "RESTRICT"),
        _fk("social_account_id", "social_account.id"),
        _fk("account_group_id", "account_group.id", "SET NULL", nullable=True),
        _fk("approval_request_id", "approval_request.id", "SET NULL", nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stagger_offset_seconds", sa.Integer, nullable=False, server_default="0"),
        sa.Column("placement", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("state", sa.Text, nullable=False, server_default="queued"),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        _ts("next_attempt_at"),
        sa.Column("last_error", sa.Text),
        _ts("dead_lettered_at"),
        _ts("published_at"),
        _fk("created_by_user_id", "user.id", "SET NULL", nullable=True),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint(f"state IN {SCHEDULED_STATES}", name="ck_scheduled_post_state"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_scheduled_post_attempts"),
        sa.UniqueConstraint("idempotency_key", name="uq_scheduled_post_idempotency"),
    )
    op.create_index(
        "ix_scheduled_post_due",
        "scheduled_post",
        ["next_attempt_at"],
        postgresql_where=sa.text("state IN ('queued', 'failed')"),
    )
    op.create_index("ix_scheduled_post_set", "scheduled_post", ["post_set_id"])
    op.create_index(
        "ix_scheduled_post_account_time", "scheduled_post", ["social_account_id", "scheduled_for"]
    )
    _same_workspace(
        "scheduled_post",
        [
            ("post_set_id", "post_set"),
            ("post_variant_id", "post_variant"),
            ("post_version_id", "post_version"),
            ("social_account_id", "social_account"),
            ("account_group_id", "account_group"),
            ("approval_request_id", "approval_request"),
        ],
    )
    # What a scheduled post covers is fixed: only its time, state and attempt bookkeeping
    # move. A published or dead-lettered row is final apart from being resolved.
    op.execute(
        """
        CREATE FUNCTION mera_scheduled_post_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.post_variant_id IS DISTINCT FROM OLD.post_variant_id
             OR NEW.post_version_id IS DISTINCT FROM OLD.post_version_id
             OR NEW.social_account_id IS DISTINCT FROM OLD.social_account_id
             OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
             OR NEW.post_set_id IS DISTINCT FROM OLD.post_set_id THEN
            RAISE EXCEPTION 'scheduled_post anchor is immutable';
          END IF;
          IF OLD.state = 'published' AND NEW.state IS DISTINCT FROM OLD.state THEN
            RAISE EXCEPTION 'scheduled_post is published';
          END IF;
          NEW.updated_at := now();
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER scheduled_post_guard BEFORE UPDATE ON scheduled_post "
        "FOR EACH ROW EXECUTE FUNCTION mera_scheduled_post_guard()"
    )
    enable_rls("scheduled_post")

    # ------------------------------------------------------------ published_post
    op.create_table(
        "published_post",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        _fk("post_set_id", "post_set.id"),
        _fk("scheduled_post_id", "scheduled_post.id"),
        _fk("post_variant_id", "post_variant.id"),
        _fk("post_version_id", "post_version.id", "RESTRICT"),
        _fk("social_account_id", "social_account.id"),
        _fk("account_group_id", "account_group.id", "SET NULL", nullable=True),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("platform_post_id", sa.Text, nullable=False),
        sa.Column("permalink", sa.Text),
        sa.Column("extra_ids", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("content_hash", sa.LargeBinary, nullable=False),
        sa.Column("via_autopilot", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        _ts("edited_at"),
        _ts("deleted_at"),
        _ts("metrics_synced_at"),
        _ts("next_metrics_at"),
        _now("created_at"),
        sa.UniqueConstraint("scheduled_post_id", name="uq_published_post_scheduled"),
        sa.UniqueConstraint(
            "social_account_id", "platform_post_id", name="uq_published_post_platform_id"
        ),
    )
    op.create_index("ix_published_post_set", "published_post", ["post_set_id"])
    op.create_index(
        "ix_published_post_account_time", "published_post", ["social_account_id", "published_at"]
    )
    op.create_index(
        "ix_published_post_metrics_due",
        "published_post",
        ["next_metrics_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    _same_workspace(
        "published_post",
        [
            ("post_set_id", "post_set"),
            ("scheduled_post_id", "scheduled_post"),
            ("post_variant_id", "post_variant"),
            ("post_version_id", "post_version"),
            ("social_account_id", "social_account"),
        ],
    )
    enable_rls("published_post")

    # --------------------------------------------------------------- dead_letter
    op.create_table(
        "dead_letter",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        sa.Column("queue", sa.Text, nullable=False),
        sa.Column("task_name", sa.Text, nullable=False),
        sa.Column("subject_type", sa.Text, nullable=False),
        sa.Column("subject_id", _UUID, nullable=False),
        _fk("social_account_id", "social_account.id", "SET NULL", nullable=True),
        _fk("post_set_id", "post_set.id", "SET NULL", nullable=True),
        sa.Column("error", sa.Text, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        _now("failed_at"),
        _ts("resolved_at"),
        _fk("resolved_by_user_id", "user.id", "SET NULL", nullable=True),
        sa.Column("resolution", sa.Text),
    )
    op.create_index(
        "ix_dead_letter_open",
        "dead_letter",
        ["workspace_id", "failed_at"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    enable_rls("dead_letter")

    # --------------------------------------------------------------------- event
    op.create_table(
        "event",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        _ws(),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("subject_type", sa.Text),
        sa.Column("subject_id", _UUID),
        sa.Column("social_account_id", _UUID),
        sa.Column("account_group_id", _UUID),
        sa.Column("payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _now("created_at"),
    )
    op.create_index("ix_event_workspace_id", "event", ["workspace_id", "id"])
    op.create_index("ix_event_created_at", "event", ["created_at"])
    enable_rls("event")

    # ----------------------------------------------------------- metric_snapshot
    op.create_table(
        "metric_snapshot",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        _ws(),
        sa.Column("subject_type", sa.Text, nullable=False),
        _fk("published_post_id", "published_post.id", nullable=True),
        _fk("social_account_id", "social_account.id"),
        _fk("post_set_id", "post_set.id", "SET NULL", nullable=True),
        _fk("account_group_id", "account_group.id", "SET NULL", nullable=True),
        sa.Column("metric_key", sa.Text, nullable=False),
        sa.Column("value", sa.Numeric),
        sa.Column("platform_field", sa.Text, nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("subject_type IN ('post', 'account')", name="ck_metric_subject"),
        sa.CheckConstraint(
            "(subject_type = 'post') = (published_post_id IS NOT NULL)",
            name="ck_metric_subject_post",
        ),
        sa.CheckConstraint(
            "metric_key IN ('impressions', 'reach', 'likes', 'comments', 'shares', 'saves', "
            "'clicks', 'ctr', 'engagement_rate', 'followers', 'video_views')",
            name="ck_metric_key",
        ),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_metric_snapshot ON metric_snapshot (subject_type, "
        "COALESCE(published_post_id, '00000000-0000-0000-0000-000000000000'::uuid), "
        "social_account_id, metric_key, captured_at)"
    )
    op.execute(
        "CREATE INDEX brin_metric_snapshot_captured ON metric_snapshot USING brin (captured_at)"
    )
    op.create_index(
        "ix_metric_snapshot_post", "metric_snapshot", ["published_post_id", "metric_key"]
    )
    op.create_index(
        "ix_metric_snapshot_account", "metric_snapshot", ["social_account_id", "metric_key"]
    )
    enable_rls("metric_snapshot")

    # ---------------------------------------------------------- approval_channel
    op.create_table(
        "approval_channel",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        sa.Column("channel_type", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("config", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "constraints_snapshot", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("credentials_ct", sa.Text),
        _ts("last_test_at"),
        sa.Column("last_test_result", sa.Text),
        _fk("created_by_user_id", "user.id", "SET NULL", nullable=True),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint("channel_type IN ('telegram', 'whatsapp')", name="ck_channel_type"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_channel_status"),
        sa.UniqueConstraint("workspace_id", "channel_type", name="uq_approval_channel_type"),
    )
    enable_rls("approval_channel")

    # ---------------------------------------------------------- approver_binding
    op.create_table(
        "approver_binding",
        sa.Column("id", _UUID, primary_key=True),
        _fk("user_id", "user.id"),
        sa.Column("channel_type", sa.Text, nullable=False),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("external_label", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("preferences", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _now("verified_at"),
        _ts("revoked_at"),
        _now("created_at"),
        sa.CheckConstraint("channel_type IN ('telegram', 'whatsapp')", name="ck_binding_channel"),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_binding_status"),
        sa.CheckConstraint("char_length(external_id) <= 64", name="ck_binding_external_id"),
    )
    op.create_index(
        "uq_approver_binding_active_chat",
        "approver_binding",
        ["channel_type", "external_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_approver_binding_user", "approver_binding", ["user_id"])
    op.execute("ALTER TABLE approver_binding ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE approver_binding FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY binding_self ON approver_binding "
        f"USING (user_id = {USER_SETTING_SQL}) WITH CHECK (user_id = {USER_SETTING_SQL})"
    )
    # The bot worker resolving an inbound chat: exactly the rows for that chat.
    op.execute(
        "CREATE POLICY binding_chat ON approver_binding USING ("
        "channel_type = current_setting('mera.channel_type', true) AND "
        "external_id = current_setting('mera.channel_external_id', true)) WITH CHECK ("
        "channel_type = current_setting('mera.channel_type', true) AND "
        "external_id = current_setting('mera.channel_external_id', true))"
    )
    # A workspace sees (read-only) the bindings of its own members, for the Approval
    # Channels screen and for dispatching cards to them.
    op.execute(
        "CREATE POLICY binding_member ON approver_binding FOR SELECT USING ("
        "EXISTS (SELECT 1 FROM workspace_member m WHERE m.user_id = approver_binding.user_id "
        f"AND m.workspace_id = {_TENANT}))"
    )

    # -------------------------------------------------------------- pairing_code
    op.create_table(
        "pairing_code",
        sa.Column("id", _UUID, primary_key=True),
        _fk("user_id", "user.id"),
        sa.Column("channel_type", sa.Text, nullable=False),
        sa.Column("code_hash", sa.Text, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _ts("used_at"),
        _now("created_at"),
        sa.UniqueConstraint("code_hash", name="uq_pairing_code_hash"),
        sa.CheckConstraint("channel_type IN ('telegram', 'whatsapp')", name="ck_pairing_channel"),
    )
    op.execute("ALTER TABLE pairing_code ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE pairing_code FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY pairing_self ON pairing_code "
        f"USING (user_id = {USER_SETTING_SQL}) WITH CHECK (user_id = {USER_SETTING_SQL})"
    )
    op.execute(
        "CREATE POLICY pairing_lookup ON pairing_code USING ("
        "code_hash = current_setting('mera.pairing_code_hash', true))"
    )

    op.create_table(
        "channel_pairing_attempt",
        sa.Column("channel_type", sa.Text, primary_key=True),
        sa.Column("external_id", sa.Text, primary_key=True),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default="0"),
        _now("window_start"),
        _ts("locked_until"),
    )
    op.execute("ALTER TABLE channel_pairing_attempt ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE channel_pairing_attempt FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY attempt_chat ON channel_pairing_attempt USING ("
        "channel_type = current_setting('mera.channel_type', true) AND "
        "external_id = current_setting('mera.channel_external_id', true)) WITH CHECK ("
        "channel_type = current_setting('mera.channel_type', true) AND "
        "external_id = current_setting('mera.channel_external_id', true))"
    )

    # ----------------------------------------------------------- channel_message
    op.create_table(
        "channel_message",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        _fk("approval_request_id", "approval_request.id", nullable=True),
        _fk("approver_binding_id", "approver_binding.id", "SET NULL", nullable=True),
        _fk("user_id", "user.id", "SET NULL", nullable=True),
        sa.Column("channel_type", sa.Text, nullable=False),
        sa.Column("external_chat_id", sa.Text, nullable=False),
        sa.Column("external_message_id", sa.Text),
        sa.Column("message_kind", sa.Text, nullable=False),
        sa.Column("render_state", sa.Text, nullable=False, server_default="pending"),
        sa.Column("view", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("cost_estimate", sa.Numeric(10, 4)),
        _ts("sent_at"),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint(
            "message_kind IN ('card', 'reminder', 'escalation', 'expiry', 'notice', "
            "'publish_report', 'health_alert', 'edit_prompt', 'digest', 'command')",
            name="ck_channel_message_kind",
        ),
        sa.CheckConstraint(
            "render_state IN ('pending', 'delivered', 'updated', 'failed', 'closed')",
            name="ck_channel_message_state",
        ),
    )
    op.create_index("ix_channel_message_request", "channel_message", ["approval_request_id"])
    op.create_index(
        "ix_channel_message_chat",
        "channel_message",
        ["channel_type", "external_chat_id", "external_message_id"],
    )
    enable_rls("channel_message")

    op.create_table(
        "channel_update",
        sa.Column("channel_type", sa.Text, primary_key=True),
        sa.Column("update_id", sa.Text, primary_key=True),
        _now("received_at"),
    )
    op.create_index("ix_channel_update_received", "channel_update", ["received_at"])

    # ----------------------------------------------------------- sandbox_capture
    op.create_table(
        "sandbox_capture",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        _ws(),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("target", sa.Text, nullable=False),
        sa.Column("social_account_id", _UUID),
        sa.Column("payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _now("created_at"),
        sa.CheckConstraint(
            "kind IN ('platform_call', 'channel_message')", name="ck_sandbox_capture_kind"
        ),
    )
    op.create_index("ix_sandbox_capture_ws", "sandbox_capture", ["workspace_id", "id"])
    enable_rls("sandbox_capture")

    # ------------------------------------------------------------------- hashtag
    op.create_table(
        "hashtag",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        sa.Column("tag", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="allowed"),
        sa.Column("aliases", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        _ts("first_used_at"),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint(
            "status IN ('allowed', 'banned', 'preferred')", name="ck_hashtag_status"
        ),
        sa.CheckConstraint(
            "tag = lower(tag) AND tag !~ '^#' AND char_length(tag) BETWEEN 1 AND 100",
            name="ck_hashtag_tag",
        ),
        sa.UniqueConstraint("workspace_id", "tag", name="uq_hashtag_tag"),
    )
    enable_rls("hashtag")


def downgrade() -> None:
    op.drop_table("hashtag")
    op.drop_table("sandbox_capture")
    op.drop_table("channel_update")
    op.drop_table("channel_message")
    op.drop_table("channel_pairing_attempt")
    op.drop_table("pairing_code")
    op.drop_table("approver_binding")
    op.drop_table("approval_channel")
    op.drop_table("metric_snapshot")
    op.drop_table("event")
    op.drop_table("dead_letter")
    op.drop_table("published_post")
    op.execute("DROP FUNCTION IF EXISTS mera_published_post_same_workspace()")
    op.drop_table("scheduled_post")
    op.execute("DROP FUNCTION IF EXISTS mera_scheduled_post_guard()")
    op.execute("DROP FUNCTION IF EXISTS mera_scheduled_post_same_workspace()")
    op.drop_index("ix_approval_request_open", table_name="approval_request")
    # Before 0012 a set had at most one open request: keep the newest open one per set and
    # mark older ones superseded, or the unique index cannot be rebuilt.
    op.execute(
        """
        UPDATE approval_request r SET state = 'superseded'
        WHERE r.state IN ('pending', 'awaiting_edit') AND EXISTS (
          SELECT 1 FROM approval_request n
          WHERE n.post_set_id = r.post_set_id AND n.state IN ('pending', 'awaiting_edit')
            AND (n.created_at, n.id) > (r.created_at, r.id))
        """
    )
    op.create_index(
        "uq_approval_request_open",
        "approval_request",
        ["post_set_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('pending', 'awaiting_edit')"),
    )
