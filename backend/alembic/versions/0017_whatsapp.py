"""WhatsApp approvals (P2-M4, docs/07 §4).

- ``approver_binding.last_inbound_at``: when the approver last messaged Mera from that
  chat. Inside 24 hours Mera may send free-form messages; outside, only an approved
  template (docs/07 §4.2).
- ``channel_send_log``: every message Mera sends on a paid channel, template or session,
  for the monthly cost ceiling and the cost view. Not tenant data (a chat serves every
  workspace its person belongs to); the app role may only read and append.

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from mera.infrastructure.db.rls import APP_ROLE

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("approver_binding", sa.Column("last_inbound_at", sa.DateTime(timezone=True)))
    op.create_table(
        "channel_send_log",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("channel_type", sa.Text, nullable=False),
        sa.Column("chat_id", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("template", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind IN ('template', 'session')", name="ck_channel_send_log_kind"),
    )
    op.create_index(
        "ix_channel_send_log_month", "channel_send_log", ["channel_type", "kind", "created_at"]
    )
    op.execute(f"REVOKE ALL ON channel_send_log FROM {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON channel_send_log TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("channel_send_log")
    op.drop_column("approver_binding", "last_inbound_at")
