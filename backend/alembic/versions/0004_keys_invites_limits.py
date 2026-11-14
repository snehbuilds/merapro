"""API keys (M2.6), invitations (M2.8), rate limits (M2.12), idempotency (M2.13), OAuth (M2.7).

API keys and invitations are tenant tables, but are presented *before* the workspace is
known. Following the ADR-0006 addendum, each gets one extra SELECT-only policy keyed on a
transaction-local hash of the presented secret: the holder of a secret can read exactly the
one row it names, and nothing else.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import enable_rls

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)


def _setting(name: str) -> str:
    return f"NULLIF(current_setting('{name}', true), '')"


def _ts() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    # ---- API keys ----
    op.create_table(
        "api_key",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("key_prefix", sa.Text, nullable=False),  # shown in lists; not secret
        sa.Column("key_hash", sa.Text, nullable=False),
        sa.Column("scopes", pg.ARRAY(sa.Text), nullable=False),
        sa.Column(
            "created_by", _UUID, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        *_ts(),
        sa.UniqueConstraint("key_hash", name="uq_api_key_key_hash"),
    )
    op.create_index("ix_api_key_workspace_id", "api_key", ["workspace_id"])
    enable_rls("api_key")
    op.execute(
        f"CREATE POLICY api_key_lookup ON api_key FOR SELECT "
        f"USING (key_hash = {_setting('mera.api_key_hash')})"
    )

    # ---- invitations ----
    op.create_table(
        "workspace_invitation",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("email", pg.CITEXT, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False),
        sa.Column("invited_by", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_by", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        *_ts(),
        sa.UniqueConstraint("token_hash", name="uq_workspace_invitation_token_hash"),
        sa.CheckConstraint(
            "role IN ('admin', 'editor', 'approver', 'viewer')",
            name="ck_workspace_invitation_role",
        ),
    )
    op.create_index(
        "uq_workspace_invitation_pending",
        "workspace_invitation",
        ["workspace_id", "email"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )
    enable_rls("workspace_invitation")
    op.execute(
        f"CREATE POLICY invitation_lookup ON workspace_invitation FOR SELECT "
        f"USING (token_hash = {_setting('mera.invitation_token_hash')})"
    )

    # ---- rate limiting: token buckets (global, no tenant data) ----
    op.create_table(
        "rate_limit_bucket",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("tokens", sa.Float, nullable=False),
        sa.Column("refilled_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ---- idempotency: cached responses keyed per principal ----
    op.create_table(
        "idempotency_key",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("principal_key", sa.Text, nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("method", sa.Text, nullable=False),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("request_hash", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("response_status", sa.Integer),
        sa.Column("response_headers", pg.JSONB),
        sa.Column("response_body", sa.LargeBinary),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("principal_key", "key", name="uq_idempotency_key_principal_key"),
        sa.CheckConstraint(
            "state IN ('in_progress', 'completed')", name="ck_idempotency_key_state"
        ),
    )
    op.create_index("ix_idempotency_key_expires_at", "idempotency_key", ["expires_at"])

    # ---- OAuth login flows (short-lived, single use) ----
    op.create_table(
        "oauth_flow",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("state_hash", sa.Text, nullable=False),
        sa.Column("provider_code_verifier", sa.Text, nullable=False),
        sa.Column("nonce", sa.Text, nullable=False),
        sa.Column("client_code_challenge", sa.Text, nullable=False),
        sa.Column("client_redirect_uri", sa.Text, nullable=False),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id", ondelete="CASCADE")),
        sa.Column("login_code_hash", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("callback_at", sa.DateTime(timezone=True)),
        sa.Column("exchanged_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("state_hash", name="uq_oauth_flow_state_hash"),
        sa.UniqueConstraint("login_code_hash", name="uq_oauth_flow_login_code_hash"),
        sa.CheckConstraint("provider IN ('google', 'microsoft')", name="ck_oauth_flow_provider"),
    )


def downgrade() -> None:
    op.drop_table("oauth_flow")
    op.drop_table("idempotency_key")
    op.drop_table("rate_limit_bucket")
    op.drop_table("workspace_invitation")
    op.drop_table("api_key")
