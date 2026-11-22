"""Scope Telegram chat pairing to a workspace bot."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pairing_code", sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_pairing_code_workspace_id",
        "pairing_code",
        "workspace",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_index("uq_approver_binding_active_chat", table_name="approver_binding")
    op.create_index(
        "uq_approver_binding_active_chat",
        "approver_binding",
        ["channel_type", "external_id", sa.text("coalesce(preferences->>'workspace_id', '')")],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_approver_binding_active_chat", table_name="approver_binding")
    op.create_index(
        "uq_approver_binding_active_chat",
        "approver_binding",
        ["channel_type", "external_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_constraint("fk_pairing_code_workspace_id", "pairing_code", type_="foreignkey")
    op.drop_column("pairing_code", "workspace_id")
