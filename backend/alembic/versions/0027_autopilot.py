"""Assisted and Full Autopilot (P3-M3, docs/14 §2.3, ADR-0017).

``autopilot_authorization``: the record that lets a post publish without a person, bound
exactly like a person's approval: to one variant, one version and its content hash, with
the mode it was given under, the Editor's confidence and the reasons. ``assert_publishable``
re-checks it before the platform call; withdrawing autopilot (or a kill switch) stops
queued posts that relied on it.

Revision ID: 0027
Revises: 0026
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import enable_rls

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "autopilot_authorization",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "approval_request_id",
            _UUID,
            sa.ForeignKey("approval_request.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "post_variant_id",
            _UUID,
            sa.ForeignKey("post_variant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "post_version_id",
            _UUID,
            sa.ForeignKey("post_version.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "social_account_id",
            _UUID,
            sa.ForeignKey("social_account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.LargeBinary, nullable=False),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("confidence", sa.Double),
        sa.Column("reasons", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_reason", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("mode IN ('assisted', 'full')", name="ck_autopilot_mode"),
        sa.UniqueConstraint(
            "approval_request_id", "post_variant_id", name="uq_autopilot_request_variant"
        ),
    )
    enable_rls("autopilot_authorization")


def downgrade() -> None:
    op.drop_table("autopilot_authorization")
