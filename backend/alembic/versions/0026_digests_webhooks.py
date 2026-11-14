"""Daily digests and outbound webhooks (P3-M4, docs/07 §3.4, docs/04 §4.14, docs/14).

- ``workspace_member.digest_mode`` (``immediate``, ``digest``, ``both``) and
  ``digest_time`` (local ``HH:MM``): a member on ``digest`` gets no cards for new approval
  requests, only one message a day listing what waits for them; ``both`` gets both.
- ``webhook_endpoint``: an https URL, the event types it wants, a signing secret
  (encrypted; shown once), and its position in the ``event`` outbox.
- ``webhook_delivery``: one attempt series per event and endpoint, with backoff.

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import enable_rls

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)


def _ws() -> sa.Column[object]:
    return sa.Column(
        "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )


def _ts(name: str, now: bool = False) -> sa.Column[object]:
    if now:
        return sa.Column(
            name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        )
    return sa.Column(name, sa.DateTime(timezone=True))


def upgrade() -> None:
    op.add_column(
        "workspace_member",
        sa.Column("digest_mode", sa.Text, nullable=False, server_default="immediate"),
    )
    op.add_column(
        "workspace_member",
        sa.Column("digest_time", sa.Text, nullable=False, server_default="08:30"),
    )
    op.add_column("workspace_member", sa.Column("digest_last_sent_on", sa.Date))
    op.create_check_constraint(
        "ck_member_digest_mode",
        "workspace_member",
        "digest_mode IN ('immediate', 'digest', 'both')",
    )

    op.create_table(
        "webhook_endpoint",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("events", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("secret_ct", sa.Text, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("last_event_id", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default="0"),
        sa.Column("disabled_reason", sa.Text),
        sa.Column("created_by_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        _ts("created_at", now=True),
        _ts("updated_at", now=True),
    )
    enable_rls("webhook_endpoint")

    op.create_table(
        "webhook_delivery",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        sa.Column(
            "endpoint_id",
            _UUID,
            sa.ForeignKey("webhook_endpoint.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_id", sa.BigInteger),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        _ts("next_attempt_at", now=True),
        sa.Column("last_status", sa.Integer),
        sa.Column("last_error", sa.Text),
        _ts("delivered_at"),
        _ts("created_at", now=True),
        sa.CheckConstraint("state IN ('pending', 'delivered', 'failed')", name="ck_delivery_state"),
    )
    op.create_index("ix_webhook_delivery_due", "webhook_delivery", ["state", "next_attempt_at"])
    enable_rls("webhook_delivery")


def downgrade() -> None:
    op.drop_table("webhook_delivery")
    op.drop_table("webhook_endpoint")
    op.drop_constraint("ck_member_digest_mode", "workspace_member", type_="check")
    op.drop_column("workspace_member", "digest_last_sent_on")
    op.drop_column("workspace_member", "digest_time")
    op.drop_column("workspace_member", "digest_mode")
