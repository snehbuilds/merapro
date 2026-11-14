"""The MCP client registry (P2-M9, docs/05 §2): external servers, their tools, bindings,
approval-gated calls, and the OAuth 2.1 flow toward a server that needs one."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from mera.api.deps import CurrentUser, McpRegistry, Meta
from mera.api.v1.routers.posts import WS

router = APIRouter(tags=["mcp-registry"])
S = f"{WS}/mcp-servers"


class ServerIn(BaseModel):
    slug: str = Field(min_length=2, max_length=31)
    name: str = Field(min_length=1, max_length=120)
    transport: Literal["http", "stdio"]
    url: str | None = Field(default=None, max_length=2048)
    package: str | None = Field(default=None, max_length=300)
    args: list[str] = Field(default_factory=list, max_length=30)
    auth_method: Literal["none", "bearer", "headers", "env", "oauth"] = "none"
    credentials: dict[str, Any] | None = Field(
        default=None, description="token, headers or env: stored encrypted, never returned"
    )
    egress_allowlist: list[str] = Field(default_factory=list, max_length=50)
    scope_group_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class ProposeIn(BaseModel):
    slug: str = Field(min_length=2, max_length=31)
    name: str = Field(min_length=1, max_length=120)
    url: str | None = Field(default=None, max_length=2048)
    package: str | None = Field(default=None, max_length=300)


class ImportIn(BaseModel):
    config: dict[str, Any] = Field(description="An object with mcpServers, as in Claude Desktop")


class ToolPatch(BaseModel):
    enabled: bool | None = None
    risk_class: Literal["read", "write", "destructive"] | None = None
    approval_policy: Literal["always", "auto", "blocked"] | None = None
    acknowledge_flag: bool | None = None


class BindingsIn(BaseModel):
    agent_keys: list[str] = Field(max_length=20)
    max_calls_per_run: int | None = Field(
        default=None, ge=1, le=500, description="Per agent run; 20 when unset"
    )
    max_calls_per_day: int | None = Field(
        default=None, ge=1, le=10_000, description="Per agent per UTC day; 200 when unset"
    )


class InstallIn(BaseModel):
    slug: str | None = Field(default=None, min_length=2, max_length=31)
    credentials: dict[str, Any] | None = Field(
        default=None, description="Stored encrypted, never returned"
    )


class DecideIn(BaseModel):
    allow: bool
    content_hash: str = Field(min_length=64, max_length=64)


class CallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    server_id: uuid.UUID
    tool_name: str
    arguments: dict[str, Any] = Field(description="Exactly what will be sent")
    content_hash: str
    agent_key: str | None
    agent_run_id: uuid.UUID | None
    state: str
    result: dict[str, Any] | None = Field(description="Untrusted text from the server")
    error: str | None
    expires_at: datetime
    created_at: datetime


class OAuthStartOut(BaseModel):
    authorize_url: str


def _call(c: Any) -> CallOut:
    return CallOut(
        id=c.id,
        server_id=c.server_id,
        tool_name=c.tool_name,
        arguments=c.arguments,
        content_hash=c.content_hash.hex(),
        agent_key=c.agent_key,
        agent_run_id=c.agent_run_id,
        state=c.state,
        result=c.result,
        error=c.error,
        expires_at=c.expires_at,
        created_at=c.created_at,
    )


async def _one(
    svc: Any, user: Any, ws: uuid.UUID, meta: Any, server_id: uuid.UUID
) -> dict[str, Any]:
    for row in await svc.list_servers(user, ws, meta):
        if row["id"] == server_id:
            out: dict[str, Any] = row
            return out
    from mera.application.errors import NotFoundError

    raise NotFoundError("Server not found.")


@router.get(S)
async def list_servers(
    workspace_id: uuid.UUID, user: CurrentUser, svc: McpRegistry, meta: Meta
) -> dict[str, Any]:
    """Registrations with their tools (credentials are never returned)."""
    return {"data": await svc.list_servers(user, workspace_id, meta)}


@router.post(S, status_code=status.HTTP_201_CREATED)
async def register_server(
    workspace_id: uuid.UUID, body: ServerIn, user: CurrentUser, svc: McpRegistry, meta: Meta
) -> dict[str, Any]:
    """Lands in pending_review with every tool disabled."""
    srv = await svc.register(user, workspace_id, meta, **body.model_dump())
    return await _one(svc, user, workspace_id, meta, srv.id)


@router.post(f"{S}/import", status_code=status.HTTP_201_CREATED)
async def import_servers(
    workspace_id: uuid.UUID, body: ImportIn, user: CurrentUser, svc: McpRegistry, meta: Meta
) -> dict[str, Any]:
    rows = await svc.import_json(user, workspace_id, meta, body.config)
    return {"data": [r.id for r in rows]}


@router.post(f"{S}/propose", status_code=status.HTTP_201_CREATED)
async def propose_server(
    workspace_id: uuid.UUID, body: ProposeIn, user: CurrentUser, svc: McpRegistry, meta: Meta
) -> dict[str, Any]:
    """A suggestion (from Mera's MCP tool ``propose_mcp_server``): pending_review, always."""
    srv = await svc.register(
        user,
        workspace_id,
        meta,
        slug=body.slug,
        name=body.name,
        transport="http" if body.url else "stdio",
        url=body.url,
        package=body.package,
        added_via="mcp",
        need_admin=False,
    )
    return {"id": srv.id, "state": srv.state}


@router.post(f"{S}/{{server_id}}/discover")
async def discover_server(
    workspace_id: uuid.UUID, server_id: uuid.UUID, user: CurrentUser, svc: McpRegistry, meta: Meta
) -> dict[str, Any]:
    """Connection test, era probe and tool discovery (new tools arrive disabled)."""
    await svc.discover(user, workspace_id, meta, server_id)
    return await _one(svc, user, workspace_id, meta, server_id)


@router.post(f"{S}/{{server_id}}/approve")
async def approve_server(
    workspace_id: uuid.UUID, server_id: uuid.UUID, user: CurrentUser, svc: McpRegistry, meta: Meta
) -> dict[str, Any]:
    """Admins, with a second factor (``mcp_server_approve``)."""
    await svc.approve(user, workspace_id, meta, server_id)
    return await _one(svc, user, workspace_id, meta, server_id)


@router.post(f"{S}/{{server_id}}/reject")
async def reject_server(
    workspace_id: uuid.UUID, server_id: uuid.UUID, user: CurrentUser, svc: McpRegistry, meta: Meta
) -> dict[str, Any]:
    await svc.reject(user, workspace_id, meta, server_id)
    return await _one(svc, user, workspace_id, meta, server_id)


@router.post(f"{S}/{{server_id}}/disable")
async def disable_server(
    workspace_id: uuid.UUID, server_id: uuid.UUID, user: CurrentUser, svc: McpRegistry, meta: Meta
) -> dict[str, Any]:
    """Instant, no second factor: pending calls cancelled, the sandbox stopped."""
    await svc.disable(user, workspace_id, meta, server_id)
    return await _one(svc, user, workspace_id, meta, server_id)


@router.post(f"{S}/{{server_id}}/oauth/start", response_model=OAuthStartOut)
async def start_server_oauth(
    workspace_id: uuid.UUID, server_id: uuid.UUID, user: CurrentUser, svc: McpRegistry, meta: Meta
) -> OAuthStartOut:
    return OAuthStartOut(authorize_url=await svc.start_oauth(user, workspace_id, meta, server_id))


@router.patch(f"{WS}/mcp-tools/{{tool_id}}")
async def update_tool(
    workspace_id: uuid.UUID,
    tool_id: uuid.UUID,
    body: ToolPatch,
    user: CurrentUser,
    svc: McpRegistry,
    meta: Meta,
) -> dict[str, Any]:
    """Enable, classify, set the policy (``auto`` on a write tool needs a second factor)."""
    t = await svc.update_tool(user, workspace_id, meta, tool_id, **body.model_dump())
    return {
        "id": t.id,
        "enabled": t.enabled,
        "risk_class": t.risk_class,
        "approval_policy": t.approval_policy,
    }


@router.put(f"{WS}/mcp-tools/{{tool_id}}/bindings")
async def set_bindings(
    workspace_id: uuid.UUID,
    tool_id: uuid.UUID,
    body: BindingsIn,
    user: CurrentUser,
    svc: McpRegistry,
    meta: Meta,
) -> dict[str, Any]:
    """Which agents may call this tool. The Publisher and Approval agents never may."""
    keys = await svc.set_bindings(
        user,
        workspace_id,
        meta,
        tool_id,
        body.agent_keys,
        max_calls_per_run=body.max_calls_per_run,
        max_calls_per_day=body.max_calls_per_day,
    )
    return {
        "agent_keys": keys,
        "max_calls_per_run": body.max_calls_per_run,
        "max_calls_per_day": body.max_calls_per_day,
    }


@router.get(f"{WS}/mcp-catalog")
async def mcp_catalog(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: McpRegistry,
    meta: Meta,
    q: str | None = None,
) -> dict[str, Any]:
    """Curated servers, plus the public MCP Registry when it is configured (Admins)."""
    return {"data": await svc.catalog(user, workspace_id, meta, q)}


@router.post(f"{WS}/mcp-catalog/{{name:path}}/install", status_code=status.HTTP_201_CREATED)
async def install_from_catalog(
    workspace_id: uuid.UUID,
    name: str,
    body: InstallIn,
    user: CurrentUser,
    svc: McpRegistry,
    meta: Meta,
) -> dict[str, Any]:
    """Lands in pending_review with every tool disabled, like any other server."""
    srv = await svc.install(
        user, workspace_id, meta, name, slug=body.slug, credentials=body.credentials
    )
    return await _one(svc, user, workspace_id, meta, srv.id)


@router.get(f"{WS}/mcp-tool-calls")
async def list_calls(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: McpRegistry,
    meta: Meta,
    state: str | None = None,
) -> dict[str, Any]:
    return {"data": [_call(c) for c in await svc.list_calls(user, workspace_id, meta, state=state)]}


@router.post(f"{WS}/mcp-tool-calls/{{call_id}}/decide", response_model=CallOut)
async def decide_call(
    workspace_id: uuid.UUID,
    call_id: uuid.UUID,
    body: DecideIn,
    user: CurrentUser,
    svc: McpRegistry,
    meta: Meta,
) -> CallOut:
    """Allow or deny exactly the arguments shown (``content_hash``)."""
    return _call(
        await svc.decide_call(
            user, workspace_id, meta, call_id, allow=body.allow, content_hash=body.content_hash
        )
    )


@router.get("/mcp-client/metadata.json", include_in_schema=False)
async def client_metadata(request: Request) -> dict[str, Any]:
    """Mera's Client ID Metadata Document, as an MCP client."""
    out: dict[str, Any] = request.app.state.mcp_registry.client_metadata()
    return out


@router.get("/mcp-client/oauth/callback", include_in_schema=False)
async def oauth_callback(
    request: Request,
    state: str,
    code: str | None = None,
    iss: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    from mera.application.errors import InvalidInput

    if error or not code:
        return HTMLResponse(
            "<p>The server was not authorized. You can close this tab.</p>", status_code=400
        )
    try:
        await request.app.state.mcp_registry.finish_oauth(state=state, code=code, iss=iss)
    except InvalidInput as exc:
        return HTMLResponse(
            f"<p>Not authorized: {exc.code}. You can close this tab.</p>", status_code=400
        )
    return HTMLResponse("<p>Authorized. Go back to Mera.</p>")
