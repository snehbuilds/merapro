"""Governance (P3-M7, docs/09 §9 and §13, docs/14).

- **Security alerts.** ``security_event`` gains a read policy for the alert scanner only
  (``mera.security_scan``, set by ``security_scan_transaction`` in workers). Alerts sent are
  kept in ``security_alert``; the scanner's position in ``security_scan_cursor``.
- **Step-up.** ``reauth_proof``: a few-minutes, single-use proof that a person who signs in
  only with a provider just did so again; it stands in for the password they do not have.
- **Data rights.** ``data_export`` tracks a workspace export (JSON and media in a zip in the
  object store, downloaded by a time-limited link). ``user.erased_at`` and
  ``workspace.erased_at`` mark the end of the 30-day grace after deletion, when personal
  data is removed and audit entries stay with the actor pseudonymised.
  ``comment.author_erased_at`` records a commenter's erasure request.

Revision ID: 0025
Revises: 0024
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import enable_rls

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)
_SCAN = "current_setting('mera.security_scan', true) = 'on'"
_TENANT = "NULLIF(current_setting('mera.workspace_id', true), '')::uuid"


def _now(name: str = "created_at") -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.execute(f"CREATE POLICY security_event_scan ON security_event FOR SELECT USING ({_SCAN})")

    op.create_table(
        "security_alert",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("workspace_id", _UUID),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("event_id", sa.BigInteger),
        sa.Column("detail", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("recipients", sa.Integer, nullable=False, server_default="0"),
        _now(),
    )
    op.create_index("ix_security_alert_lookup", "security_alert", ["kind", "user_id", "created_at"])
    op.execute("ALTER TABLE security_alert ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE security_alert FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY security_alert_scan ON security_alert USING ({_SCAN}) WITH CHECK ({_SCAN})"
    )
    op.execute(
        f"CREATE POLICY security_alert_read ON security_alert FOR SELECT USING (workspace_id = {_TENANT})"
    )

    op.create_table(
        "security_scan_cursor",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("last_id", sa.BigInteger, nullable=False, server_default="0"),
    )

    op.create_table(
        "reauth_proof",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        _now(),
    )

    op.create_table(
        "data_export",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("requested_by_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("state", sa.Text, nullable=False, server_default="pending"),
        sa.Column("object_key", sa.Text),
        sa.Column("byte_size", sa.BigInteger),
        sa.Column("error", sa.Text),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        _now(),
        sa.CheckConstraint(
            "state IN ('pending', 'ready', 'failed', 'expired')", name="ck_export_state"
        ),
    )
    enable_rls("data_export")

    op.add_column("user", sa.Column("erased_at", sa.DateTime(timezone=True)))
    op.add_column("workspace", sa.Column("erased_at", sa.DateTime(timezone=True)))
    op.add_column("comment", sa.Column("author_erased_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("comment", "author_erased_at")
    op.drop_column("workspace", "erased_at")
    op.drop_column("user", "erased_at")
    op.drop_table("data_export")
    op.drop_table("reauth_proof")
    op.drop_table("security_scan_cursor")
    op.drop_table("security_alert")
    op.execute("DROP POLICY IF EXISTS security_event_scan ON security_event")
