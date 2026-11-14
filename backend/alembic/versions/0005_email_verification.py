"""Email ownership (Sprint 3 security review H1).

``user.email_verified_at`` records *proof* that the account holder controls the address: a
verification link, or a provider that asserts it (Google ``email_verified``). Linking a
sign-in method and accepting an invitation both require it.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user", sa.Column("email_verified_at", sa.DateTime(timezone=True)))
    op.create_table(
        "email_verification",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The address the link proves; if the user's email changes, old links die.
        sa.Column("email", pg.CITEXT, nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("token_hash", name="uq_email_verification_token_hash"),
    )
    op.create_index("ix_email_verification_user_id", "email_verification", ["user_id"])


def downgrade() -> None:
    op.drop_table("email_verification")
    op.drop_column("user", "email_verified_at")
