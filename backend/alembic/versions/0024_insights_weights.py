"""Insights and weight history (P3-M1.3 to 1.5, docs/03 INSIGHT, docs/14).

- ``insight``: one finding from the weekly run, per workspace, group or account. ``body``
  is written from numbers in code; ``evidence`` is the aggregate it came from, so every
  sentence can be traced. A ``summary`` row holds the Analytics agent's narrative, whose
  evidence is the list of insights it was given.
- ``weight_change``: every move of a source or pillar weight by the feedback loop, with
  the value before and after and the insight behind it. A revert writes the old value back
  exactly and marks the change reverted.

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import enable_rls

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)


def _ws() -> sa.Column[object]:
    return sa.Column(
        "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "insight",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        sa.Column("account_group_id", _UUID, sa.ForeignKey("account_group.id", ondelete="CASCADE")),
        sa.Column(
            "social_account_id", _UUID, sa.ForeignKey("social_account.id", ondelete="CASCADE")
        ),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("evidence", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("agent_run_id", _UUID, sa.ForeignKey("agent_run.id", ondelete="SET NULL")),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "kind IN ('topic', 'source', 'platform', 'account_comparison', "
            "'group_comparison', 'approval', 'summary')",
            name="ck_insight_kind",
        ),
    )
    op.create_index("ix_insight_period", "insight", ["workspace_id", "period_end"])
    enable_rls("insight")

    op.create_table(
        "weight_change",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        sa.Column("subject_type", sa.Text, nullable=False),
        sa.Column("subject_id", _UUID, nullable=False),
        sa.Column("before", sa.Double, nullable=False),
        sa.Column("after", sa.Double, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("insight_id", _UUID, sa.ForeignKey("insight.id", ondelete="SET NULL")),
        sa.Column("reverted_at", sa.DateTime(timezone=True)),
        sa.Column("reverted_by_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("subject_type IN ('source', 'pillar')", name="ck_weight_subject"),
    )
    op.create_index(
        "ix_weight_change_subject", "weight_change", ["subject_type", "subject_id", "created_at"]
    )
    enable_rls("weight_change")


def downgrade() -> None:
    op.drop_table("weight_change")
    op.drop_table("insight")
