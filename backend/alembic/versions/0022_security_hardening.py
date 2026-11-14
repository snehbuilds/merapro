"""Phase 2 security hardening (P2-M10, the release security review).

- **RLS on the second-factor tables** (migration 0016 left them to the repository filter):
  ``second_factor_grant`` is readable and writable only inside its own workspace;
  ``bot_second_factor`` only by the bot acting for its own chat (the ``channel_transaction``
  settings, like ``approver_binding``); ``security_event`` is append-only for the app: a
  transaction may insert events for no workspace or for its own, and read back only the
  rows it wrote itself (``INSERT … RETURNING``) or, in ``user_transaction``, the person's
  own.
- **Reply cards carry a nonce** (``reply.card_nonce`` / ``card_hash``): a tap is honoured
  only for the card last sent, and only while the reply's full content hash is still the
  one that card showed. A 40-bit prefix in the callback could be ground against.
- **Authenticator codes lock out** like the PIN: five wrong codes in a row (sign-in,
  re-authentication or a second factor) and codes are refused for 15 minutes
  (``user.totp_failed_count`` / ``totp_locked_until``).

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from mera.infrastructure.db.rls import USER_SETTING_SQL, disable_rls, enable_rls

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT = "NULLIF(current_setting('mera.workspace_id', true), '')::uuid"
_CHAT = (
    "channel_type = current_setting('mera.channel_type', true) AND "
    "chat_id = current_setting('mera.channel_external_id', true)"
)


def upgrade() -> None:
    # ---------------------------------------------------------------- RLS
    enable_rls("second_factor_grant")

    op.execute("ALTER TABLE bot_second_factor ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE bot_second_factor FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY bot_sf_chat ON bot_second_factor USING ({_CHAT}) WITH CHECK ({_CHAT})"
    )

    op.execute("ALTER TABLE security_event ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE security_event FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY security_event_append ON security_event FOR INSERT "
        f"WITH CHECK ((workspace_id IS NULL OR workspace_id = {_TENANT}) AND created_at = now())"
    )
    # created_at defaults to now(), the transaction's start: a transaction sees the events it
    # wrote (what INSERT … RETURNING needs) and none written by another.
    op.execute(
        "CREATE POLICY security_event_read ON security_event FOR SELECT "
        f"USING (created_at = now() OR user_id = {USER_SETTING_SQL})"
    )

    # ------------------------------------------------------- reply card nonce
    op.add_column("reply", sa.Column("card_nonce", sa.Text))
    op.add_column("reply", sa.Column("card_hash", sa.LargeBinary))

    # ------------------------------------------------- authenticator lockout
    op.add_column(
        "user",
        sa.Column("totp_failed_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column("user", sa.Column("totp_locked_until", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("user", "totp_locked_until")
    op.drop_column("user", "totp_failed_count")
    op.drop_column("reply", "card_hash")
    op.drop_column("reply", "card_nonce")
    op.execute("DROP POLICY IF EXISTS security_event_read ON security_event")
    op.execute("DROP POLICY IF EXISTS security_event_append ON security_event")
    op.execute("ALTER TABLE security_event NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE security_event DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS bot_sf_chat ON bot_second_factor")
    op.execute("ALTER TABLE bot_second_factor NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE bot_second_factor DISABLE ROW LEVEL SECURITY")
    disable_rls("second_factor_grant")
