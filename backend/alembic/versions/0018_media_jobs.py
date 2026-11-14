"""Media generation jobs (P2-M5, docs/03 §6, docs/04 §4.7).

``media_job`` is a tracked generation: queued, running, then succeeded, failed, cancelled
or timed out. It keeps the estimate it was checked against and the cost the provider
reported, whether or not the result was usable, so the month's spend is the sum of
``actual_cost_usd``. ``batch_id`` and ``slot`` order the images of one carousel, which
attach together once every one has succeeded.

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import enable_rls

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "media_job",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("post_set_id", _UUID, sa.ForeignKey("post_set.id", ondelete="CASCADE")),
        sa.Column("post_variant_id", _UUID, sa.ForeignKey("post_variant.id", ondelete="CASCADE")),
        sa.Column("batch_id", _UUID),
        sa.Column("slot", sa.Integer, nullable=False, server_default="0"),
        sa.Column("kind", sa.Text, nullable=False, server_default="image"),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("model", sa.Text),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("alt_text", sa.Text),
        sa.Column("parameters", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("state", sa.Text, nullable=False, server_default="queued"),
        sa.Column("result_asset_id", _UUID, sa.ForeignKey("media_asset.id", ondelete="SET NULL")),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 4), nullable=False),
        sa.Column("actual_cost_usd", sa.Numeric(10, 4)),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column("requested_by_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("agent_run_id", _UUID, sa.ForeignKey("agent_run.id", ondelete="SET NULL")),
        sa.Column("surface", sa.Text, nullable=False, server_default="web"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind IN ('image', 'video')", name="ck_media_job_kind"),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'timed_out')",
            name="ck_media_job_state",
        ),
        sa.CheckConstraint("char_length(prompt) BETWEEN 1 AND 4000", name="ck_media_job_prompt"),
        sa.CheckConstraint("slot BETWEEN 0 AND 19", name="ck_media_job_slot"),
        sa.CheckConstraint(
            "estimated_cost_usd >= 0 AND (actual_cost_usd IS NULL OR actual_cost_usd >= 0)",
            name="ck_media_job_cost",
        ),
    )
    op.create_index(
        "ix_media_job_live",
        "media_job",
        ["state", "started_at"],
        postgresql_where=sa.text("state IN ('queued', 'running')"),
    )
    op.create_index("ix_media_job_month", "media_job", ["workspace_id", "created_at"])
    op.create_index("ix_media_job_batch", "media_job", ["batch_id"])
    enable_rls("media_job")


def downgrade() -> None:
    op.drop_table("media_job")
