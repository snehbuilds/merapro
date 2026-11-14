"""External MCP controls (P3-M6, docs/05 §2 and §3, docs/14).

- ``mcp_server.consecutive_failures`` and ``circuit_open_until``: five failures in a row
  open the circuit for sixty seconds (calls fail fast, the server shows ``degraded``); the
  next call after that is the probe.
- ``mcp_agent_binding.max_calls_per_run`` and ``max_calls_per_day``: an agent's budget for
  one tool; refused beyond it. Defaults apply when unset.

Revision ID: 0029
Revises: 0028
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mcp_server",
        sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column("mcp_server", sa.Column("circuit_open_until", sa.DateTime(timezone=True)))
    op.add_column("mcp_agent_binding", sa.Column("max_calls_per_run", sa.Integer))
    op.add_column("mcp_agent_binding", sa.Column("max_calls_per_day", sa.Integer))


def downgrade() -> None:
    op.drop_column("mcp_agent_binding", "max_calls_per_day")
    op.drop_column("mcp_agent_binding", "max_calls_per_run")
    op.drop_column("mcp_server", "circuit_open_until")
    op.drop_column("mcp_server", "consecutive_failures")
