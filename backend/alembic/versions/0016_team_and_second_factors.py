"""Team, MFA and second factors (P2-M2, docs/09 §5).

- ``user``: TOTP enrolment (the secret column exists since 0003), replay guard, recovery
  code hashes, the approval PIN (Argon2id) with its lockout.
- ``mfa_challenge``: the short-lived, single-use proof that a password (or OAuth) sign-in
  passed, while the TOTP code is still owed. Hashed, attempts counted.
- ``second_factor_grant``: a single-use proof, bound to one user, one workspace and one
  action, that a second factor was given in the last few minutes (docs/09 §5.3).
- ``bot_second_factor``: an action waiting in a chat for ``/pin`` (company Page approvals
  from Telegram or WhatsApp).
- ``security_event``: MFA, PIN and second-factor events for review and, later, alerting.
- ``workspace_member``: out-of-office (``away_until``) and a delegate for approvals.
- ``approval_policy.escalate_to_user_ids``: named people to escalate to.

User-level tables follow ``refresh_token``: no tenant column, reached through global
transactions; the app role may append to ``security_event`` but never rewrite it.

Revision ID: 0016
Revises: 0015
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import APP_ROLE

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.add_column("user", sa.Column("totp_pending_ct", sa.Text))
    op.add_column("user", sa.Column("mfa_enabled_at", sa.DateTime(timezone=True)))
    op.add_column("user", sa.Column("totp_last_counter", sa.BigInteger))
    op.add_column(
        "user",
        sa.Column("recovery_code_hashes", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
    )
    op.add_column("user", sa.Column("approval_pin_hash", sa.Text))
    op.add_column(
        "user", sa.Column("pin_failed_count", sa.Integer, nullable=False, server_default="0")
    )
    op.add_column("user", sa.Column("pin_locked_until", sa.DateTime(timezone=True)))

    op.create_table(
        "mfa_challenge",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("method", sa.Text, nullable=False),
        sa.Column("device_label", sa.Text),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        _now("created_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "second_factor_grant",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("method", sa.Text, nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        _now("created_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("method IN ('totp', 'pin', 'recovery')", name="ck_sf_grant_method"),
    )
    op.create_table(
        "bot_second_factor",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("channel_type", sa.Text, nullable=False),
        sa.Column("chat_id", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False, server_default="{}"),
        _now("created_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_bot_second_factor_chat", "bot_second_factor", ["channel_type", "chat_id"])
    op.create_table(
        "security_event",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("workspace_id", _UUID),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("detail", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("ip_address", pg.INET),
        _now("created_at"),
    )
    op.create_index("ix_security_event_user", "security_event", ["user_id", "created_at"])
    op.execute(f"REVOKE ALL ON security_event FROM {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON security_event TO {APP_ROLE}")

    op.add_column("workspace_member", sa.Column("away_until", sa.DateTime(timezone=True)))
    op.add_column(
        "workspace_member",
        sa.Column("delegate_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
    )
    op.add_column(
        "approval_policy",
        sa.Column("escalate_to_user_ids", pg.ARRAY(_UUID), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("approval_policy", "escalate_to_user_ids")
    op.drop_column("workspace_member", "delegate_user_id")
    op.drop_column("workspace_member", "away_until")
    op.drop_table("security_event")
    op.drop_table("bot_second_factor")
    op.drop_table("second_factor_grant")
    op.drop_table("mfa_challenge")
    for col in (
        "pin_locked_until",
        "pin_failed_count",
        "approval_pin_hash",
        "recovery_code_hashes",
        "totp_last_counter",
        "mfa_enabled_at",
        "totp_pending_ct",
    ):
        op.drop_column("user", col)
