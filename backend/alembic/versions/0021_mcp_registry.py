"""MCP client registry (P2-M9, docs/05 §2): external servers Mera's agents may use.

- ``mcp_server``: one registration per workspace, ``pending_review`` until an Admin
  approves it with a second factor. Credentials are envelope-encrypted, keyed by issuer
  for OAuth, and never returned by any read. ``scope_group_ids`` empty means
  workspace-wide.
- ``mcp_tool``: discovered tools, all disabled at first, each with a schema hash, a risk
  class and an approval policy. A changed schema disables the tool.
- ``mcp_agent_binding``: the third level of the allowlist (default deny).
- ``mcp_tool_call``: every call; a write waits for a person on a card, bound by a hash over
  the registration, the tool, its schema hash and the canonical arguments.
- ``mcp_oauth_flow``: one OAuth 2.1 authorization in progress toward an external server.

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import enable_rls

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)


def _base() -> list[sa.Column[object]]:
    return [
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
        ),
    ]


def _ts(name: str) -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "mcp_server",
        *_base(),
        sa.Column("slug", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("transport", sa.Text, nullable=False),
        sa.Column("url", sa.Text),
        sa.Column("package", sa.Text),
        sa.Column("args", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("protocol_era", sa.Text),
        sa.Column("protocol_version", sa.Text),
        sa.Column("state", sa.Text, nullable=False, server_default="pending_review"),
        sa.Column("auth_method", sa.Text, nullable=False, server_default="none"),
        sa.Column("credentials_ct", sa.Text),
        sa.Column("oauth_issuer", sa.Text),
        sa.Column("egress_allowlist", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("scope_group_ids", pg.ARRAY(_UUID), nullable=False, server_default="{}"),
        sa.Column("curated", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("added_via", sa.Text, nullable=False, server_default="form"),
        sa.Column("created_by_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("approved_by_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.Column("last_discovered_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint("transport IN ('http', 'stdio')", name="ck_mcp_server_transport"),
        sa.CheckConstraint(
            "state IN ('pending_review', 'active', 'degraded', 'auth_required', 'disabled', "
            "'rejected')",
            name="ck_mcp_server_state",
        ),
        sa.CheckConstraint(
            "auth_method IN ('none', 'bearer', 'headers', 'env', 'oauth')",
            name="ck_mcp_server_auth",
        ),
        sa.CheckConstraint(
            "added_via IN ('form', 'json', 'catalog', 'mcp')", name="ck_mcp_server_added_via"
        ),
        sa.CheckConstraint("slug ~ '^[a-z][a-z0-9_]{1,30}$'", name="ck_mcp_server_slug"),
        sa.CheckConstraint(
            "(transport = 'http') = (url IS NOT NULL) AND (transport = 'stdio') = (package IS NOT NULL)",
            name="ck_mcp_server_target",
        ),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_mcp_server_slug"),
    )
    enable_rls("mcp_server")

    op.create_table(
        "mcp_tool",
        *_base(),
        sa.Column(
            "server_id", _UUID, sa.ForeignKey("mcp_server.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("title", sa.Text),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("input_schema", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("output_schema", pg.JSONB),
        sa.Column("annotations", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("schema_hash", sa.LargeBinary, nullable=False),
        sa.Column("risk_class", sa.Text, nullable=False, server_default="write"),
        sa.Column("approval_policy", sa.Text, nullable=False, server_default="always"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("flag", sa.Text),
        sa.Column("flag_acknowledged", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("presence", sa.Text, nullable=False, server_default="present"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint(
            "risk_class IN ('read', 'write', 'destructive')", name="ck_mcp_tool_risk"
        ),
        sa.CheckConstraint(
            "approval_policy IN ('always', 'auto', 'blocked')", name="ck_mcp_tool_policy"
        ),
        sa.CheckConstraint(
            "presence IN ('present', 'changed', 'gone')", name="ck_mcp_tool_presence"
        ),
        sa.UniqueConstraint("server_id", "name", name="uq_mcp_tool_name"),
    )
    enable_rls("mcp_tool")

    op.create_table(
        "mcp_agent_binding",
        *_base(),
        sa.Column(
            "tool_id", _UUID, sa.ForeignKey("mcp_tool.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("agent_key", sa.Text, nullable=False),
        _ts("created_at"),
        sa.CheckConstraint(
            "agent_key NOT IN ('publisher', 'approval')", name="ck_mcp_agent_binding_agent"
        ),
        sa.UniqueConstraint("tool_id", "agent_key", name="uq_mcp_agent_binding"),
    )
    enable_rls("mcp_agent_binding")

    op.create_table(
        "mcp_tool_call",
        *_base(),
        sa.Column(
            "server_id", _UUID, sa.ForeignKey("mcp_server.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "tool_id", _UUID, sa.ForeignKey("mcp_tool.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("tool_name", sa.Text, nullable=False),
        sa.Column("arguments", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.LargeBinary, nullable=False),
        sa.Column("agent_key", sa.Text),
        sa.Column("agent_run_id", _UUID, sa.ForeignKey("agent_run.id", ondelete="SET NULL")),
        sa.Column("state", sa.Text, nullable=False, server_default="pending_approval"),
        sa.Column("result", pg.JSONB),
        sa.Column("error", sa.Text),
        sa.Column("decided_by_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        _ts("created_at"),
        sa.CheckConstraint(
            "state IN ('pending_approval', 'approved', 'rejected', 'expired', 'running', "
            "'succeeded', 'failed', 'cancelled')",
            name="ck_mcp_tool_call_state",
        ),
    )
    op.create_index("ix_mcp_tool_call_state", "mcp_tool_call", ["workspace_id", "state"])
    enable_rls("mcp_tool_call")

    op.create_table(
        "mcp_oauth_flow",
        *_base(),
        sa.Column(
            "server_id", _UUID, sa.ForeignKey("mcp_server.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("state_hash", sa.Text, nullable=False, unique=True),
        sa.Column("code_verifier_ct", sa.Text, nullable=False),
        sa.Column("issuer", sa.Text, nullable=False),
        sa.Column("token_endpoint", sa.Text, nullable=False),
        sa.Column("resource", sa.Text, nullable=False),
        sa.Column("redirect_uri", sa.Text, nullable=False),
        sa.Column("client_id", sa.Text, nullable=False),
        sa.Column("created_by_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        _ts("created_at"),
    )
    enable_rls("mcp_oauth_flow")
    op.execute(
        "CREATE POLICY mcp_flow_lookup ON mcp_oauth_flow FOR SELECT "
        "USING (state_hash = NULLIF(current_setting('mera.mcp_flow_state_hash', true), ''))"
    )


def downgrade() -> None:
    op.drop_table("mcp_oauth_flow")
    op.drop_table("mcp_tool_call")
    op.drop_table("mcp_agent_binding")
    op.drop_table("mcp_tool")
    op.drop_table("mcp_server")
