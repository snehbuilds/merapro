"""A/B tests (P3-M2, docs/04 §4.5 ``…/variants/{vid}/ab``, docs/14).

Version B of one account's post is its own post set, linked to A's (``ab_parent_id``), so
approval, placement, spacing and every guardrail apply to it exactly as to any post; a
post set keeps one variant per account. ``ab_label`` is A or B; ``ab_hypothesis`` is what
B is testing.

Revision ID: 0028
Revises: 0027
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "post_set",
        sa.Column(
            "ab_parent_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("post_set.id", ondelete="SET NULL"),
        ),
    )
    op.add_column("post_set", sa.Column("ab_label", sa.Text))
    op.add_column("post_set", sa.Column("ab_hypothesis", sa.Text))
    op.create_index("ix_post_set_ab_parent", "post_set", ["ab_parent_id"])


def downgrade() -> None:
    op.drop_index("ix_post_set_ab_parent", "post_set")
    op.drop_column("post_set", "ab_hypothesis")
    op.drop_column("post_set", "ab_label")
    op.drop_column("post_set", "ab_parent_id")
