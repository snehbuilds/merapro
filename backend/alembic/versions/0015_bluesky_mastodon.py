"""Bluesky and Mastodon connections (P2-M3).

- ``account_connect_flow.context``: what a flow needs to finish besides the code, today the
  Mastodon instance the person chose.
- ``mastodon_app``: Mera's OAuth app on each Mastodon instance, registered the first time
  someone connects an account there. Instance-wide, not tenant data: one app per instance
  serves every workspace, like the Telegram bot. The client secret is envelope-encrypted.

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import APP_ROLE

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("account_connect_flow", sa.Column("context", pg.JSONB))
    op.create_table(
        "mastodon_app",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("instance", sa.Text, nullable=False, unique=True),
        sa.Column("client_id", sa.Text, nullable=False),
        sa.Column("client_secret_ct", sa.Text, nullable=False),
        sa.Column("redirect_uri", sa.Text, nullable=False),
        sa.Column("scopes", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("instance ~ '^[a-z0-9.-]+(:[0-9]+)?$'", name="ck_mastodon_app_instance"),
    )
    # Read and add only: an app registration is never rewritten in place.
    op.execute(f"REVOKE ALL ON mastodon_app FROM {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON mastodon_app TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("mastodon_app")
    op.drop_column("account_connect_flow", "context")
