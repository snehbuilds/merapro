"""Accounts and account groups (M3), notifications, and system workspace enumeration.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import enable_rls

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)
_PLATFORMS = (
    "'bluesky', 'mastodon', 'linkedin_member', 'linkedin_organization', "
    "'x', 'threads', 'instagram', 'facebook'"
)


def _ts() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _ws_fk() -> sa.Column:
    return sa.Column(
        "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )


def _jsonb(name: str, default: str = "'{}'::jsonb") -> sa.Column:
    return sa.Column(name, pg.JSONB, nullable=False, server_default=sa.text(default))


def upgrade() -> None:
    op.create_table(
        "social_account",
        sa.Column("id", _UUID, primary_key=True),
        _ws_fk(),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("platform_account_id", sa.Text, nullable=False),
        sa.Column("handle", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text),
        sa.Column("nickname", sa.Text, nullable=False),
        sa.Column("avatar_url", sa.Text),
        sa.Column("account_type", sa.Text, nullable=False),
        sa.Column("credentials_ct", sa.Text),  # envelope-encrypted; NULL once disconnected
        sa.Column("granted_scopes", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("token_issued_at", sa.DateTime(timezone=True)),
        sa.Column("token_expires_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("status_reason", sa.Text),
        _jsonb("capabilities_snapshot"),
        sa.Column("default_brand_voice_id", _UUID),  # FK arrives with brand_voice (M5)
        sa.Column("default_posting_times", pg.JSONB),
        _jsonb("settings"),
        _jsonb("rate_budget"),
        sa.Column("last_health_check_at", sa.DateTime(timezone=True)),
        _jsonb("health"),
        sa.Column("connected_by", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("connected_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("disconnected_at", sa.DateTime(timezone=True)),
        *_ts(),
        sa.UniqueConstraint(
            "workspace_id", "platform", "platform_account_id", name="uq_social_account_identity"
        ),
        sa.CheckConstraint(f"platform IN ({_PLATFORMS})", name="ck_social_account_platform"),
        sa.CheckConstraint(
            "account_type IN ('personal', 'page', 'organization', 'business')",
            name="ck_social_account_account_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'needs_reauth', 'suspended', 'disconnected')",
            name="ck_social_account_status",
        ),
    )
    op.create_index("ix_social_account_workspace_id", "social_account", ["workspace_id"])
    op.create_index(
        "ix_social_account_token_expiry",
        "social_account",
        ["token_expires_at"],
        postgresql_where=sa.text("status = 'active'"),
    )
    enable_rls("social_account")

    op.create_table(
        "account_group",
        sa.Column("id", _UUID, primary_key=True),
        _ws_fk(),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("color", sa.Text),
        sa.Column("default_brand_voice_id", _UUID),
        sa.Column("posting_schedule", pg.JSONB),
        sa.Column("content_pillar_ids", pg.ARRAY(_UUID), nullable=False, server_default="{}"),
        sa.Column("stagger_mode", sa.Text, nullable=False, server_default="random_spread"),
        sa.Column("stagger_min_seconds", sa.Integer, nullable=False, server_default="180"),
        sa.Column("stagger_max_seconds", sa.Integer, nullable=False, server_default="900"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        _jsonb("settings"),
        *_ts(),
        sa.UniqueConstraint("workspace_id", "name", name="uq_account_group_name"),
        sa.CheckConstraint(
            "stagger_mode IN ('none', 'fixed', 'random_spread')",
            name="ck_account_group_stagger_mode",
        ),
        sa.CheckConstraint(
            "stagger_min_seconds >= 0 AND stagger_max_seconds >= stagger_min_seconds",
            name="ck_account_group_stagger_bounds",
        ),
    )
    op.create_index("ix_account_group_workspace_id", "account_group", ["workspace_id"])
    enable_rls("account_group")

    op.create_table(
        "account_group_member",
        sa.Column("id", _UUID, primary_key=True),
        _ws_fk(),
        sa.Column(
            "account_group_id",
            _UUID,
            sa.ForeignKey("account_group.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "social_account_id",
            _UUID,
            sa.ForeignKey("social_account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("enabled_by_default", sa.Boolean, nullable=False, server_default=sa.text("true")),
        *_ts(),
        sa.UniqueConstraint(
            "account_group_id", "social_account_id", name="uq_account_group_member_pair"
        ),
    )
    op.create_index(
        "ix_account_group_member_account", "account_group_member", ["social_account_id"]
    )
    enable_rls("account_group_member")
    # Both parents must belong to the member row's workspace (docs/03 §11).
    op.execute(
        """
        CREATE FUNCTION mera_group_member_same_workspace() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM account_group g
                         WHERE g.id = NEW.account_group_id AND g.workspace_id = NEW.workspace_id)
             OR NOT EXISTS (SELECT 1 FROM social_account a
                            WHERE a.id = NEW.social_account_id
                              AND a.workspace_id = NEW.workspace_id) THEN
            RAISE EXCEPTION 'account_group_member crosses workspaces';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER account_group_member_same_workspace "
        "BEFORE INSERT OR UPDATE ON account_group_member "
        "FOR EACH ROW EXECUTE FUNCTION mera_group_member_same_workspace()"
    )

    # Connect flows are tenant rows under RLS. The platform's redirect presents only the
    # state, so (like API keys) one SELECT-only policy lets the holder of that state read
    # exactly its own row; claiming it happens inside the workspace's transaction.
    op.create_table(
        "account_connect_flow",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("state_hash", sa.Text, nullable=False),
        sa.Column("code_verifier", sa.Text, nullable=False),
        sa.Column(
            "initiated_by", _UUID, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "reconnect_account_id", _UUID, sa.ForeignKey("social_account.id", ondelete="CASCADE")
        ),
        sa.Column("return_path", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("connect_code_hash", sa.Text),
        sa.Column("pending_identity_ct", sa.Text),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("state_hash", name="uq_account_connect_flow_state_hash"),
        sa.UniqueConstraint("connect_code_hash", name="uq_account_connect_flow_code_hash"),
    )
    enable_rls("account_connect_flow")
    op.execute(
        "CREATE POLICY connect_flow_lookup ON account_connect_flow FOR SELECT "
        "USING (state_hash = NULLIF(current_setting('mera.connect_state_hash', true), ''))"
    )
    op.execute(
        "CREATE POLICY connect_code_lookup ON account_connect_flow FOR SELECT "
        "USING (connect_code_hash = NULLIF(current_setting('mera.connect_code_hash', true), ''))"
    )

    op.create_table(
        "notification",
        sa.Column("id", _UUID, primary_key=True),
        _ws_fk(),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("body", sa.Text),
        sa.Column("subject_type", sa.Text),
        sa.Column("subject_id", _UUID),
        sa.Column("social_account_id", _UUID),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("read_by", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'critical')", name="ck_notification_severity"
        ),
    )
    op.create_index(
        "ix_notification_workspace_created", "notification", ["workspace_id", "created_at"]
    )
    enable_rls("notification")

    # ADR-0006 addendum 2: periodic jobs must find the workspaces to iterate. With
    # ``mera.system_scan`` set, workspace *rows* (not their contents) become readable; the
    # job then opens a normal workspace_transaction per workspace.
    op.execute(
        "CREATE POLICY workspace_system_scan ON workspace FOR SELECT "
        "USING (current_setting('mera.system_scan', true) = 'on')"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS workspace_system_scan ON workspace")
    op.drop_table("notification")
    op.drop_table("account_connect_flow")
    op.drop_table("account_group_member")
    op.execute("DROP FUNCTION IF EXISTS mera_group_member_same_workspace()")
    op.drop_table("account_group")
    op.drop_table("social_account")
