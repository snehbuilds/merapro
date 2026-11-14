"""Mera's MCP server (P2-M8, docs/05 §1): OAuth 2.1 grants, codes, pending confirmations.

- ``mcp_oauth_code``: one authorization code (PKCE S256, one use, five minutes), bound to
  the client, the redirect URI, the resource and the consented scopes.
- ``mcp_grant``: an access token and its refresh token, both stored as hashes, scoped to
  one workspace and to the MCP resource only. The REST API accepts the access token only
  when the MCP service presents it (docs/05 §1.2: a token for the MCP server cannot be
  replayed against the REST API).
- ``mcp_pending_action``: a high-risk call waiting for a second factor on a Mera web page,
  so a PIN or code never passes through the model.

Codes and tokens are found by their hash, under RLS lookup policies like API keys.

Revision ID: 0020
Revises: 0019
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import enable_rls

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)


def _setting(name: str) -> str:
    return f"NULLIF(current_setting('{name}', true), '')"


def _common() -> list[sa.Column[object]]:
    return [
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "mcp_oauth_code",
        *_common(),
        sa.Column("client_id", sa.Text, nullable=False),
        sa.Column("client_name", sa.Text),
        sa.Column("redirect_uri", sa.Text, nullable=False),
        sa.Column("code_hash", sa.Text, nullable=False, unique=True),
        sa.Column("code_challenge", sa.Text, nullable=False),
        sa.Column("scopes", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("resource", sa.Text, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    enable_rls("mcp_oauth_code")
    op.execute(
        "CREATE POLICY mcp_code_lookup ON mcp_oauth_code FOR SELECT "
        f"USING (code_hash = {_setting('mera.mcp_code_hash')})"
    )

    op.create_table(
        "mcp_grant",
        *_common(),
        sa.Column("client_id", sa.Text, nullable=False),
        sa.Column("client_name", sa.Text),
        sa.Column("scopes", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("resource", sa.Text, nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("refresh_hash", sa.Text, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_mcp_grant_workspace", "mcp_grant", ["workspace_id", "created_at"])
    enable_rls("mcp_grant")
    op.execute(
        "CREATE POLICY mcp_token_lookup ON mcp_grant FOR SELECT "
        f"USING (token_hash = {_setting('mera.mcp_token_hash')} "
        f"OR refresh_hash = {_setting('mera.mcp_refresh_hash')})"
    )

    op.create_table(
        "mcp_pending_action",
        *_common(),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("tool", sa.Text, nullable=False),
        sa.Column("arguments", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default="pending"),
        sa.Column("result", pg.JSONB),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'done', 'failed', 'expired', 'declined')",
            name="ck_mcp_pending_action_state",
        ),
    )
    enable_rls("mcp_pending_action")


def downgrade() -> None:
    op.drop_table("mcp_pending_action")
    op.drop_table("mcp_grant")
    op.drop_table("mcp_oauth_code")
