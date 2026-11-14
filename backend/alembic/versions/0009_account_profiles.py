"""Account profiles for the platform skills (Sprint 7).

Brand voice becomes a slim per-account profile that feeds the vendored platform writing
skills their "voice profile" and "story bank": who is posting, and true facts and stories
the Writer may use (the skills forbid inventing figures).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("brand_voice", sa.Column("about", sa.Text))
    op.add_column(
        "brand_voice",
        sa.Column("story_bank", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("brand_voice", "story_bank")
    op.drop_column("brand_voice", "about")
