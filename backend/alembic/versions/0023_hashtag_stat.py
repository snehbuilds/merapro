"""Hashtag performance per account (P3-M1.1, docs/03 HASHTAG_STAT, docs/14).

One row per (account, tag): how many of the account's posts in the window used the tag,
their average engagement, and the lift against the account's median post. A daily job
recomputes the rows from published posts and their latest metrics. Below
``MIN_HASHTAG_SAMPLES`` posts a row exists but is reported as "not enough data".

The tag is stored as text, normalised the way drafting stores it, rather than as a key
into the registry: most tags a post uses are never registered, and a registry alias
counts toward its canonical tag when the row is computed.

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import enable_rls

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "hashtag_stat",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "social_account_id",
            _UUID,
            sa.ForeignKey("social_account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tag", sa.Text, nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("post_count", sa.Integer, nullable=False),
        sa.Column("avg_engagement", sa.Numeric(14, 4), nullable=False),
        sa.Column("lift_vs_account_median", sa.Numeric(8, 4), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("social_account_id", "tag", name="uq_hashtag_stat_account_tag"),
    )
    op.create_index("ix_hashtag_stat_tag", "hashtag_stat", ["workspace_id", "tag"])
    enable_rls("hashtag_stat")


def downgrade() -> None:
    op.drop_table("hashtag_stat")
