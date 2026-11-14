"""Identity (M2.1), membership discovery across RLS (C8), and the append-only audit log (M2.10).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import APP_ROLE, USER_SETTING_SQL, enable_rls

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)
_TENANT = "NULLIF(current_setting('mera.workspace_id', true), '')::uuid"


def _ts() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.add_column(
        "user", sa.Column("token_version", sa.Integer, nullable=False, server_default="0")
    )

    # ---- C8 (ADR-0006 addendum): a user may READ their own memberships and workspaces. ----
    op.execute(
        f"CREATE POLICY member_self ON workspace_member FOR SELECT "
        f"USING (user_id = {USER_SETTING_SQL})"
    )
    op.execute(
        f"CREATE POLICY workspace_via_membership ON workspace FOR SELECT "
        f"USING (id IN (SELECT workspace_id FROM workspace_member WHERE user_id = {USER_SETTING_SQL}))"
    )

    op.create_table(
        "member_scope",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "workspace_member_id",
            _UUID,
            sa.ForeignKey("workspace_member.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope_type", sa.Text, nullable=False),
        sa.Column("scope_id", _UUID, nullable=False),
        *_ts(),
        sa.CheckConstraint(
            "scope_type IN ('account_group', 'social_account')", name="ck_member_scope_scope_type"
        ),
        sa.UniqueConstraint(
            "workspace_member_id", "scope_type", "scope_id", name="uq_member_scope_member_subject"
        ),
    )
    op.create_index("ix_member_scope_workspace_id", "member_scope", ["workspace_id"])
    enable_rls("member_scope")
    # A child's workspace_id must match its parent's (docs/03 §11).
    op.execute(
        """
        CREATE FUNCTION mera_member_scope_same_workspace() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM workspace_member m
                         WHERE m.id = NEW.workspace_member_id AND m.workspace_id = NEW.workspace_id) THEN
            RAISE EXCEPTION 'member_scope.workspace_id does not match its membership';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER member_scope_same_workspace BEFORE INSERT OR UPDATE ON member_scope "
        "FOR EACH ROW EXECUTE FUNCTION mera_member_scope_same_workspace()"
    )

    op.create_table(
        "oauth_identity",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("provider_subject", sa.Text, nullable=False),
        *_ts(),
        sa.CheckConstraint(
            "provider IN ('google', 'microsoft')", name="ck_oauth_identity_provider"
        ),
        sa.UniqueConstraint(
            "provider", "provider_subject", name="uq_oauth_identity_provider_subject"
        ),
    )
    op.create_index("ix_oauth_identity_user_id", "oauth_identity", ["user_id"])

    op.create_table(
        "refresh_token",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False),
        sa.Column("family_id", _UUID, nullable=False),
        sa.Column("rotated_from", _UUID, sa.ForeignKey("refresh_token.id", ondelete="SET NULL")),
        sa.Column("device_label", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # Absolute end of the login session: rotation never extends past this.
        sa.Column("family_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_reason", sa.Text),
        sa.UniqueConstraint("token_hash", name="uq_refresh_token_token_hash"),
    )
    op.create_index("ix_refresh_token_user_id", "refresh_token", ["user_id"])
    op.create_index("ix_refresh_token_family_id", "refresh_token", ["family_id"])

    # ---- audit_log: partitioned by month, append-only (docs/09 §10) ----
    op.execute("CREATE SEQUENCE audit_log_id_seq AS bigint")
    op.execute(
        """
        CREATE TABLE audit_log (
          id bigint NOT NULL DEFAULT nextval('audit_log_id_seq'),
          created_at timestamptz NOT NULL DEFAULT now(),
          -- No FK: a cascade or SET NULL would be an UPDATE/DELETE, which the append-only
          -- trigger rejects, blocking workspace erasure. Erasure pseudonymises instead.
          workspace_id uuid,
          actor_type text NOT NULL CHECK (actor_type IN ('user', 'agent', 'bot', 'system', 'api_key')),
          actor_id uuid,
          actor_label text,
          surface text NOT NULL CHECK (surface IN ('web', 'api', 'mcp', 'telegram', 'whatsapp', 'worker')),
          action text NOT NULL,
          subject_type text,
          subject_id uuid,
          social_account_id uuid,
          account_group_id uuid,
          subject_content_hash bytea,
          before jsonb,
          after jsonb,
          ip_address inet,
          request_id text,
          CONSTRAINT pk_audit_log PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute("ALTER SEQUENCE audit_log_id_seq OWNED BY audit_log.id")
    # Monthly partitions are created ahead of time by a periodic task (later milestone);
    # the default partition guarantees no write is ever lost before that exists.
    op.execute("CREATE TABLE audit_log_default PARTITION OF audit_log DEFAULT")
    op.execute(
        "CREATE INDEX ix_audit_log_workspace_created ON audit_log (workspace_id, created_at DESC)"
    )
    op.execute("CREATE INDEX ix_audit_log_subject ON audit_log (subject_type, subject_id)")
    op.execute(
        "CREATE INDEX ix_audit_log_account_created ON audit_log (social_account_id, created_at DESC)"
    )

    # RLS on the parent AND on each partition: a partition queried directly does not
    # inherit its parent's policies. The monthly-partition task must repeat this.
    for table in ("audit_log", "audit_log_default"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_read ON {table} FOR SELECT USING (workspace_id = {_TENANT})"
        )
        # User-level events (login) carry no workspace; anything else must match.
        op.execute(
            f"CREATE POLICY tenant_append ON {table} FOR INSERT "
            f"WITH CHECK (workspace_id IS NULL OR workspace_id = {_TENANT})"
        )
    op.execute(
        """
        CREATE FUNCTION mera_audit_log_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit_log is append-only';
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION mera_audit_log_immutable()"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION mera_audit_log_immutable()"
    )

    # Privileges. Default privileges already granted DML on the new tables; tighten:
    # the app may only read and append to the audit log.
    op.execute(f"REVOKE ALL ON audit_log, audit_log_default FROM {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON audit_log TO {APP_ROLE}")
    op.execute(f"GRANT USAGE ON SEQUENCE audit_log_id_seq TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_log CASCADE")
    op.execute("DROP FUNCTION IF EXISTS mera_audit_log_immutable()")
    op.drop_table("refresh_token")
    op.drop_table("oauth_identity")
    op.drop_table("member_scope")
    op.execute("DROP FUNCTION IF EXISTS mera_member_scope_same_workspace()")
    op.execute("DROP POLICY IF EXISTS workspace_via_membership ON workspace")
    op.execute("DROP POLICY IF EXISTS member_self ON workspace_member")
    op.drop_column("user", "token_version")
