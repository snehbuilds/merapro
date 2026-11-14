"""Identity and tenancy: workspace, user, workspace_member, the app role, and RLS (M1.5, M1.6).

Revision ID: 0001
Revises:
Create Date: 2026-11-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import APP_ROLE, disable_rls, enable_rls

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # The application role: never a superuser, never BYPASSRLS. In Compose it is created
    # with LOGIN by infra/postgres/init; here it is ensured to exist so migrations are
    # self-sufficient (e.g. against a bare database in tests).
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS;
          END IF;
        END $$;
        """
    )

    op.create_table(
        "workspace",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("slug", sa.Text, nullable=False),
        sa.Column("workspace_type", sa.Text, nullable=False, server_default="team"),
        sa.Column("timezone", sa.Text, nullable=False, server_default="UTC"),
        sa.Column("settings", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "cloned_from_workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="SET NULL")
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("slug", name="uq_workspace_slug"),
        sa.CheckConstraint(
            "workspace_type IN ('personal', 'team', 'agency')", name="ck_workspace_workspace_type"
        ),
    )

    op.create_table(
        "user",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("email", pg.CITEXT, nullable=False),
        sa.Column("password_hash", sa.Text),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("avatar_url", sa.Text),
        sa.Column("totp_secret_ct", sa.Text),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("email", name="uq_user_email"),
    )

    op.create_table(
        "workspace_member",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("scope_restricted", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("invited_by", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("joined_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member_workspace_user"),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'editor', 'approver', 'viewer')",
            name="ck_workspace_member_role",
        ),
    )
    op.create_index("ix_workspace_member_workspace_id", "workspace_member", ["workspace_id"])
    op.create_index("ix_workspace_member_user_id", "workspace_member", ["user_id"])

    # Layer 2 tenancy. `workspace` is keyed on its own id.
    enable_rls("workspace", key="id")
    enable_rls("workspace_member")

    # Privileges for the app role. Default privileges cover tables later migrations create.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}"
    )
    # /readyz reads the applied revision as the app role, which must never be able to
    # change it (a reset revision would make the next deploy re-run migrations).
    op.execute(f"REVOKE ALL ON alembic_version FROM {APP_ROLE}")
    op.execute(f"GRANT SELECT ON alembic_version TO {APP_ROLE}")


def downgrade() -> None:
    disable_rls("workspace_member")
    disable_rls("workspace")
    op.drop_table("workspace_member")
    op.drop_table("user")
    op.drop_table("workspace")
