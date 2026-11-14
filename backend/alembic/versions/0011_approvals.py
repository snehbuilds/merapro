"""Approval core (Sprint 8, M6; docs/03 §7, docs/07 §5 and §7, ADR-0014).

- ``approval_policy``: who approves and how many, at workspace, group and account scope.
  Resolution is strictest-wins across the three.
- ``approval_request``: one per submission of a post set. Its ``policy_snapshot`` freezes the
  rules in force at creation. Its state is derived from its variants (ADR-0014).
- ``approval_variant_state``: one per enabled variant. This is the approval anchor: it names
  the exact ``post_version_id`` and ``content_hash`` an approval covers, and carries its own
  quorum counts.
- ``approval_decision``: append-only. Every approve and reject, with its scope.
- ``approval_token``: the single-use ledger for bot buttons. ``used_at``/``revoked_at`` under
  ``FOR UPDATE`` is the burn.
- ``automation_state``: the kill switch and autopilot mode at three scopes.

Channels, bindings and channel messages arrive with the Telegram bot (M7).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import enable_rls

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)
_ZERO = "'00000000-0000-0000-0000-000000000000'::uuid"


def _ws() -> sa.Column:
    return sa.Column(
        "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )


def _now(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=None if nullable else sa.func.now(),
        nullable=nullable,
    )


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


SCOPES = "scope_type IN ('workspace', 'account_group', 'social_account')"
SCOPE_ID = "(scope_type = 'workspace') = (scope_id IS NULL)"


def upgrade() -> None:
    # A set whose accounts are all approved, before M8 schedules it.
    op.drop_constraint("ck_post_set_state", "post_set", type_="check")
    op.create_check_constraint(
        "ck_post_set_state",
        "post_set",
        "state IN ('drafting', 'needs_approval', 'approved', 'partially_approved', 'scheduled', "
        "'published', 'partially_published', 'rejected', 'blocked', 'archived')",
    )

    # ------------------------------------------------------------------ policy
    op.create_table(
        "approval_policy",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        sa.Column("scope_type", sa.Text, nullable=False),
        sa.Column("scope_id", _UUID),
        sa.Column(
            "subject_types",
            pg.ARRAY(sa.Text),
            nullable=False,
            server_default=sa.text("'{post_set}'"),
        ),
        sa.Column("required_approver_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("quorum_mode", sa.Text, nullable=False, server_default="any_of"),
        # Empty means: any member with the Approve permission whose scope covers the account.
        sa.Column(
            "approver_user_ids", pg.ARRAY(_UUID), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "escalation_chain", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("remind_after", sa.Interval, nullable=False, server_default="2 hours"),
        sa.Column("escalate_after", sa.Interval, nullable=False, server_default="4 hours"),
        sa.Column("expires_after", sa.Interval, nullable=False, server_default="12 hours"),
        sa.Column(
            "approval_validity_window", sa.Interval, nullable=False, server_default="24 hours"
        ),
        sa.Column("quiet_hours", pg.JSONB),
        # Phase 2 (ADR-0014 §5): kept for forward-compatible snapshots, always empty now.
        sa.Column("second_factor_actions", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("digest_mode", sa.Text, nullable=False, server_default="immediate"),
        sa.Column("updated_by_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint(SCOPES, name="ck_approval_policy_scope"),
        sa.CheckConstraint(SCOPE_ID, name="ck_approval_policy_scope_id"),
        sa.CheckConstraint(
            "required_approver_count BETWEEN 1 AND 5", name="ck_approval_policy_count"
        ),
        sa.CheckConstraint("quorum_mode IN ('any_of', 'all_of')", name="ck_approval_policy_quorum"),
        sa.CheckConstraint(
            "digest_mode IN ('immediate', 'digest', 'both')", name="ck_approval_policy_digest"
        ),
        sa.CheckConstraint(
            "cardinality(second_factor_actions) = 0", name="ck_approval_policy_no_second_factor"
        ),
        sa.CheckConstraint(
            "remind_after > interval '0' AND escalate_after >= remind_after "
            "AND expires_after >= escalate_after AND expires_after <= interval '7 days' "
            "AND approval_validity_window BETWEEN interval '5 minutes' AND interval '7 days'",
            name="ck_approval_policy_timers",
        ),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_approval_policy_scope ON approval_policy "
        f"(workspace_id, scope_type, COALESCE(scope_id, {_ZERO}))"
    )
    enable_rls("approval_policy")

    # ----------------------------------------------------------------- request
    op.create_table(
        "approval_request",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        sa.Column("subject_type", sa.Text, nullable=False, server_default="post_set"),
        sa.Column(
            "post_set_id", _UUID, sa.ForeignKey("post_set.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "account_group_id", _UUID, sa.ForeignKey("account_group.id", ondelete="SET NULL")
        ),
        sa.Column("content_hash", sa.LargeBinary, nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default="pending"),
        sa.Column("policy_snapshot", pg.JSONB, nullable=False),
        _ts("remind_at"),
        _ts("escalate_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _ts("reminded_at"),
        _ts("escalated_at"),
        sa.Column("escalation_level", sa.Integer, nullable=False, server_default="0"),
        _ts("approved_at"),
        _ts("decided_at"),
        sa.Column(
            "superseded_by", _UUID, sa.ForeignKey("approval_request.id", ondelete="SET NULL")
        ),
        _ts("scheduled_for"),
        sa.Column("requested_by_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        _now("created_at"),
        sa.CheckConstraint("subject_type = 'post_set'", name="ck_approval_request_subject"),
        sa.CheckConstraint(
            "state IN ('pending', 'awaiting_edit', 'approved', 'partially_approved', "
            "'rejected', 'expired', 'superseded', 'cancelled')",
            name="ck_approval_request_state",
        ),
        sa.CheckConstraint("octet_length(content_hash) = 32", name="ck_approval_request_hash"),
    )
    # One open request per post set: a second submission while one is pending is refused.
    op.create_index(
        "uq_approval_request_open",
        "approval_request",
        ["post_set_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('pending', 'awaiting_edit')"),
    )
    op.create_index(
        "ix_approval_request_ws_state", "approval_request", ["workspace_id", "state", "created_at"]
    )
    op.create_index(
        "ix_approval_request_expiry",
        "approval_request",
        ["expires_at"],
        postgresql_where=sa.text("state IN ('pending', 'awaiting_edit')"),
    )
    _same_workspace(
        "approval_request", [("post_set_id", "post_set"), ("account_group_id", "account_group")]
    )
    enable_rls("approval_request")

    # ------------------------------------------------------- per-variant anchor
    op.create_table(
        "approval_variant_state",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        sa.Column(
            "approval_request_id",
            _UUID,
            sa.ForeignKey("approval_request.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "post_variant_id",
            _UUID,
            sa.ForeignKey("post_variant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "post_version_id",
            _UUID,
            sa.ForeignKey("post_version.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "social_account_id",
            _UUID,
            sa.ForeignKey("social_account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.LargeBinary, nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default="pending"),
        sa.Column("required_count", sa.Integer, nullable=False),
        sa.Column("quorum_mode", sa.Text, nullable=False),
        sa.Column("approve_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reject_count", sa.Integer, nullable=False, server_default="0"),
        _ts("decided_at"),
        sa.CheckConstraint(
            "state IN ('pending', 'approved', 'rejected', 'superseded', 'expired', 'cancelled')",
            name="ck_approval_variant_state",
        ),
        sa.CheckConstraint("octet_length(content_hash) = 32", name="ck_approval_variant_hash"),
        sa.CheckConstraint(
            "quorum_mode IN ('any_of', 'all_of')", name="ck_approval_variant_quorum"
        ),
        sa.CheckConstraint(
            "required_count BETWEEN 1 AND 5 AND approve_count >= 0 AND reject_count >= 0",
            name="ck_approval_variant_counts",
        ),
        sa.UniqueConstraint(
            "approval_request_id", "post_variant_id", name="uq_approval_variant_state"
        ),
    )
    op.create_index(
        "ix_approval_variant_state_variant",
        "approval_variant_state",
        ["post_variant_id", "state"],
    )
    _same_workspace(
        "approval_variant_state",
        [
            ("approval_request_id", "approval_request"),
            ("post_variant_id", "post_variant"),
            ("post_version_id", "post_version"),
            ("social_account_id", "social_account"),
        ],
    )
    # The anchor cannot be re-pointed: what an approval covers is fixed at creation, and a
    # terminal per-variant state never changes again (ADR-0014: no "un-approve").
    op.execute(
        """
        CREATE FUNCTION mera_approval_variant_state_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.post_version_id IS DISTINCT FROM OLD.post_version_id
             OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
             OR NEW.post_variant_id IS DISTINCT FROM OLD.post_variant_id
             OR NEW.approval_request_id IS DISTINCT FROM OLD.approval_request_id
             OR NEW.social_account_id IS DISTINCT FROM OLD.social_account_id
             OR NEW.required_count IS DISTINCT FROM OLD.required_count
             OR NEW.quorum_mode IS DISTINCT FROM OLD.quorum_mode THEN
            RAISE EXCEPTION 'approval_variant_state anchor is immutable';
          END IF;
          IF OLD.state <> 'pending' AND NEW.state IS DISTINCT FROM OLD.state THEN
            RAISE EXCEPTION 'approval_variant_state % is terminal', OLD.state;
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER approval_variant_state_guard BEFORE UPDATE ON approval_variant_state "
        "FOR EACH ROW EXECUTE FUNCTION mera_approval_variant_state_guard()"
    )
    enable_rls("approval_variant_state")

    # ---------------------------------------------------------------- decisions
    op.create_table(
        "approval_decision",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        sa.Column(
            "approval_request_id",
            _UUID,
            sa.ForeignKey("approval_request.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("approver_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("channel_type", sa.Text, nullable=False),
        sa.Column("external_id", sa.Text),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("instruction_text", sa.Text),
        sa.Column("variant_scope", pg.ARRAY(_UUID), nullable=False),
        sa.Column("token_jti", sa.Text),
        sa.Column("ip_address", pg.INET),
        _now("decided_at"),
        sa.CheckConstraint(
            "channel_type IN ('web', 'api', 'mcp', 'telegram', 'whatsapp', 'system', 'test')",
            name="ck_approval_decision_channel",
        ),
        sa.CheckConstraint(
            "action IN ('approve', 'reject', 'edit_requested', 'regenerate', 'reschedule', "
            "'cancel')",
            name="ck_approval_decision_action",
        ),
        sa.CheckConstraint(
            "reason IS NULL OR char_length(reason) <= 1000", name="ck_approval_decision_reason"
        ),
    )
    op.create_index(
        "ix_approval_decision_request", "approval_decision", ["approval_request_id", "decided_at"]
    )
    _same_workspace("approval_decision", [("approval_request_id", "approval_request")])
    op.execute(
        """
        CREATE FUNCTION mera_approval_decision_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          -- Rows go only with their request (a cascade), and the one change ever allowed is
          -- the approver reference nulling when a user is deleted (review S8-L15).
          IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
            RETURN OLD;
          END IF;
          IF TG_OP = 'UPDATE' AND NEW.approver_user_id IS NULL
             AND OLD.approver_user_id IS NOT NULL
             AND (NEW.id, NEW.workspace_id, NEW.approval_request_id, NEW.channel_type,
                  NEW.external_id, NEW.action, NEW.reason, NEW.instruction_text,
                  NEW.variant_scope, NEW.token_jti, NEW.ip_address, NEW.decided_at)
                 IS NOT DISTINCT FROM
                 (OLD.id, OLD.workspace_id, OLD.approval_request_id, OLD.channel_type,
                  OLD.external_id, OLD.action, OLD.reason, OLD.instruction_text,
                  OLD.variant_scope, OLD.token_jti, OLD.ip_address, OLD.decided_at) THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'approval_decision is append-only';
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER approval_decision_append_only BEFORE UPDATE OR DELETE ON "
        "approval_decision FOR EACH ROW EXECUTE FUNCTION mera_approval_decision_append_only()"
    )
    enable_rls("approval_decision")

    # ------------------------------------------------------------------- tokens
    op.create_table(
        "approval_token",
        sa.Column("jti", sa.Text, primary_key=True),
        _ws(),
        sa.Column(
            "approval_request_id",
            _UUID,
            sa.ForeignKey("approval_request.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "approver_user_id",
            _UUID,
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.Text, nullable=False),
        # NULL: every enabled variant (the "Approve all" button, bound to the composite hash).
        sa.Column("variant_scope", pg.ARRAY(_UUID)),
        sa.Column("content_hash", sa.LargeBinary, nullable=False),
        sa.Column("channel", sa.Text, nullable=False),
        _now("issued_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _ts("used_at"),
        _ts("revoked_at"),
        sa.CheckConstraint("char_length(jti) BETWEEN 20 AND 40", name="ck_approval_token_jti"),
        sa.CheckConstraint("action IN ('approve', 'reject')", name="ck_approval_token_action"),
        sa.CheckConstraint(
            "channel IN ('telegram', 'whatsapp', 'test')", name="ck_approval_token_channel"
        ),
        sa.CheckConstraint("expires_at > issued_at", name="ck_approval_token_expiry"),
        # NULL is "every account" (the composite hash); a scoped token names exactly one.
        sa.CheckConstraint(
            "variant_scope IS NULL OR cardinality(variant_scope) = 1",
            name="ck_approval_token_scope",
        ),
    )
    op.create_index(
        "ix_approval_token_request_live",
        "approval_token",
        ["approval_request_id"],
        postgresql_where=sa.text("used_at IS NULL AND revoked_at IS NULL"),
    )
    _same_workspace("approval_token", [("approval_request_id", "approval_request")])
    # Once burned or revoked a token stays that way; nothing else about it ever changes.
    op.execute(
        """
        CREATE FUNCTION mera_approval_token_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.jti IS DISTINCT FROM OLD.jti
             OR NEW.approval_request_id IS DISTINCT FROM OLD.approval_request_id
             OR NEW.approver_user_id IS DISTINCT FROM OLD.approver_user_id
             OR NEW.action IS DISTINCT FROM OLD.action
             OR NEW.variant_scope IS DISTINCT FROM OLD.variant_scope
             OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
             OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
             OR (OLD.used_at IS NOT NULL AND NEW.used_at IS DISTINCT FROM OLD.used_at)
             OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at)
          THEN
            RAISE EXCEPTION 'approval_token is immutable once issued';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER approval_token_guard BEFORE UPDATE ON approval_token "
        "FOR EACH ROW EXECUTE FUNCTION mera_approval_token_guard()"
    )
    enable_rls("approval_token")

    # --------------------------------------------------------------- automation
    op.create_table(
        "automation_state",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        sa.Column("scope_type", sa.Text, nullable=False),
        sa.Column("scope_id", _UUID),
        sa.Column("autopilot_mode", sa.Text, nullable=False, server_default="manual"),
        sa.Column("assisted_confidence_threshold", sa.Numeric(3, 2)),
        sa.Column("kill_switch_engaged", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("kill_switch_by_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("kill_switch_surface", sa.Text),
        _ts("kill_switch_at"),
        sa.Column("kill_switch_reason", sa.Text),
        _now("updated_at"),
        sa.CheckConstraint(SCOPES, name="ck_automation_state_scope"),
        sa.CheckConstraint(SCOPE_ID, name="ck_automation_state_scope_id"),
        sa.CheckConstraint(
            "autopilot_mode IN ('manual', 'assisted', 'full')", name="ck_automation_state_mode"
        ),
        sa.CheckConstraint(
            "kill_switch_reason IS NULL OR char_length(kill_switch_reason) <= 500",
            name="ck_automation_state_reason",
        ),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_automation_state_scope ON automation_state "
        f"(workspace_id, scope_type, COALESCE(scope_id, {_ZERO}))"
    )
    enable_rls("automation_state")


def downgrade() -> None:
    op.drop_table("automation_state")
    op.drop_table("approval_token")
    op.execute("DROP FUNCTION IF EXISTS mera_approval_token_guard()")
    op.execute("DROP FUNCTION IF EXISTS mera_approval_token_same_workspace()")
    op.drop_table("approval_decision")
    op.execute("DROP FUNCTION IF EXISTS mera_approval_decision_append_only()")
    op.execute("DROP FUNCTION IF EXISTS mera_approval_decision_same_workspace()")
    op.drop_table("approval_variant_state")
    op.execute("DROP FUNCTION IF EXISTS mera_approval_variant_state_guard()")
    op.execute("DROP FUNCTION IF EXISTS mera_approval_variant_state_same_workspace()")
    op.drop_table("approval_request")
    op.execute("DROP FUNCTION IF EXISTS mera_approval_request_same_workspace()")
    op.drop_table("approval_policy")
    op.execute("UPDATE post_set SET state = 'needs_approval' WHERE state = 'approved'")
    op.drop_constraint("ck_post_set_state", "post_set", type_="check")
    op.create_check_constraint(
        "ck_post_set_state",
        "post_set",
        "state IN ('drafting', 'needs_approval', 'partially_approved', 'scheduled', "
        "'published', 'partially_published', 'rejected', 'blocked', 'archived')",
    )
