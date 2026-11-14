"""MCP client registry (P2-M9, docs/05 §2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mera.infrastructure.db.base import Base, TenantScoped, UUIDPrimaryKey


def _fk(target: str, ondelete: str = "SET NULL", nullable: bool = True) -> Any:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable
    )


def _ts() -> Any:
    return mapped_column(DateTime(timezone=True))


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class McpServer(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "mcp_server"

    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    transport: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    package: Mapped[str | None] = mapped_column(Text)
    args: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    protocol_era: Mapped[str | None] = mapped_column(Text)
    protocol_version: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending_review")
    auth_method: Mapped[str] = mapped_column(Text, nullable=False, server_default="none")
    credentials_ct: Mapped[str | None] = mapped_column(Text)
    oauth_issuer: Mapped[str | None] = mapped_column(Text)
    egress_allowlist: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    scope_group_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    curated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    added_via: Mapped[str] = mapped_column(Text, nullable=False, server_default="form")
    created_by_user_id: Mapped[uuid.UUID | None] = _fk("user.id")
    approved_by_user_id: Mapped[uuid.UUID | None] = _fk("user.id")
    approved_at: Mapped[datetime | None] = _ts()
    disabled_at: Mapped[datetime | None] = _ts()
    last_discovered_at: Mapped[datetime | None] = _ts()
    last_error: Mapped[str | None] = mapped_column(Text)
    # Circuit breaker (P3-M6.3, migration 0029).
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    circuit_open_until: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class McpTool(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "mcp_tool"

    server_id: Mapped[uuid.UUID] = _fk("mcp_server.id", "CASCADE", False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    annotations: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    schema_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    risk_class: Mapped[str] = mapped_column(Text, nullable=False, server_default="write")
    approval_policy: Mapped[str] = mapped_column(Text, nullable=False, server_default="always")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    flag: Mapped[str | None] = mapped_column(Text)
    flag_acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    presence: Mapped[str] = mapped_column(Text, nullable=False, server_default="present")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class McpAgentBinding(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "mcp_agent_binding"

    tool_id: Mapped[uuid.UUID] = _fk("mcp_tool.id", "CASCADE", False)
    agent_key: Mapped[str] = mapped_column(Text, nullable=False)
    # Budgets (P3-M6.2, migration 0029); unset uses the defaults.
    max_calls_per_run: Mapped[int | None] = mapped_column(Integer)
    max_calls_per_day: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _now()


class McpToolCall(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "mcp_tool_call"

    server_id: Mapped[uuid.UUID] = _fk("mcp_server.id", "CASCADE", False)
    tool_id: Mapped[uuid.UUID] = _fk("mcp_tool.id", "CASCADE", False)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    content_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    agent_key: Mapped[str | None] = mapped_column(Text)
    agent_run_id: Mapped[uuid.UUID | None] = _fk("agent_run.id")
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending_approval")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    decided_by_user_id: Mapped[uuid.UUID | None] = _fk("user.id")
    decided_at: Mapped[datetime | None] = _ts()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _now()


class McpOAuthFlow(UUIDPrimaryKey, TenantScoped, Base):
    __tablename__ = "mcp_oauth_flow"

    server_id: Mapped[uuid.UUID] = _fk("mcp_server.id", "CASCADE", False)
    state_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    code_verifier_ct: Mapped[str] = mapped_column(Text, nullable=False)
    issuer: Mapped[str] = mapped_column(Text, nullable=False)
    token_endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = _fk("user.id")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _now()
