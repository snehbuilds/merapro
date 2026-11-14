"""Post templates (P2-M1.4, docs/04 §4.5).

A template is a reusable starting point for the Composer: a brief, an optional outline the
Writer follows for structure, default hashtags and default target groups. It never holds
an account's final text: every account's post is still written and approved per account.

Revision ID: 0014
Revises: 0013
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import enable_rls

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "post_template",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("topic", sa.Text, nullable=False),
        sa.Column("outline", sa.Text),
        sa.Column("hashtags", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("target_group_ids", pg.ARRAY(_UUID), nullable=False, server_default="{}"),
        sa.Column("created_by", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("use_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="ck_post_template_name"),
        sa.CheckConstraint("char_length(topic) BETWEEN 1 AND 2000", name="ck_post_template_topic"),
        sa.CheckConstraint(
            "outline IS NULL OR char_length(outline) <= 4000", name="ck_post_template_outline"
        ),
        sa.UniqueConstraint("workspace_id", "name", name="uq_post_template_name"),
    )
    enable_rls("post_template")


def downgrade() -> None:
    op.drop_table("post_template")
