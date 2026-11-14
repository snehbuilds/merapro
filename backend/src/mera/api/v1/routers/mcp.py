"""Mera's MCP server, the API side (P2-M8): OAuth 2.1 for MCP clients, introspection for the
mcp service, connected clients, and pending confirmations completed on the web."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from mera.api.deps import CurrentUser, HumanUser, Inbox, McpAuth, Meta, via_mcp
from mera.api.v1.routers.posts import WS
from mera.application.errors import ApplicationError, Forbidden, InvalidInput
from mera.application.services.mcp_auth_service import require_human_for_pending

router = APIRouter(tags=["mcp"])
well_known = APIRouter(include_in_schema=False)


class ClientInfoOut(BaseModel):
    client_id: str
    client_name: str
    redirect_uris: list[str]
    logo_uri: str | None


class AuthorizeIn(BaseModel):
    workspace_id: uuid.UUID
    client_id: str = Field(max_length=2048)
    redirect_uri: str = Field(max_length=2048)
    code_challenge: str = Field(min_length=43, max_length=128)
    code_challenge_method: str = "S256"
    scope: str = Field(max_length=2000)
    resource: str = Field(max_length=2048)
    state: str | None = Field(default=None, max_length=1024)


class AuthorizeOut(BaseModel):
    redirect_to: str


class GrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    client_id: str
    client_name: str | None
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime


class GrantList(BaseModel):
    data: list[GrantOut]


class PendingIn(BaseModel):
    action: str = Field(max_length=64)
    tool: str = Field(max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)
    summary: str = Field(
        default="",
        max_length=300,
        description="Ignored: the confirmation page shows Mera's own description",
    )


class PendingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action: str
    tool: str
    arguments: dict[str, Any]
    summary: str
    state: str
    result: dict[str, Any] | None
    expires_at: datetime
    confirm_url: str | None = None


@well_known.get("/.well-known/oauth-authorization-server")
async def as_metadata(request: Request) -> dict[str, Any]:
    """RFC 8414 metadata for Mera's authorization server (MCP clients only)."""
    out: dict[str, Any] = request.app.state.mcp_auth.metadata()
    return out


@router.get("/oauth/client-info", response_model=ClientInfoOut)
async def client_info(client_id: str, user: HumanUser, svc: McpAuth) -> ClientInfoOut:
    """For the consent page: who is asking, from their Client ID Metadata Document."""
    info = await svc.client_info(client_id)
    return ClientInfoOut(
        client_id=info.client_id,
        client_name=info.client_name,
        redirect_uris=list(info.redirect_uris),
        logo_uri=info.logo_uri,
    )


@router.post("/oauth/authorize", response_model=AuthorizeOut)
async def authorize(body: AuthorizeIn, user: HumanUser, svc: McpAuth, meta: Meta) -> AuthorizeOut:
    """The person consented on Mera's page: send the client back with a code."""
    url = await svc.authorize(
        user,
        body.workspace_id,
        meta,
        client_id=body.client_id,
        redirect_uri=body.redirect_uri,
        code_challenge=body.code_challenge,
        code_challenge_method=body.code_challenge_method,
        scope=body.scope,
        resource=body.resource,
        state=body.state,
    )
    return AuthorizeOut(redirect_to=url)


def _oauth_error(exc: ApplicationError) -> JSONResponse:
    return JSONResponse(
        {"error": exc.code or "invalid_request", "error_description": str(exc)},
        status_code=400,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/oauth/token", include_in_schema=False)
async def token(
    svc: McpAuth,
    grant_type: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    code: Annotated[str | None, Form()] = None,
    redirect_uri: Annotated[str | None, Form()] = None,
    code_verifier: Annotated[str | None, Form()] = None,
    refresh_token: Annotated[str | None, Form()] = None,
    resource: Annotated[str | None, Form()] = None,
    scope: Annotated[str | None, Form()] = None,
) -> Response:
    """OAuth 2.1 token endpoint (form-encoded, public clients, PKCE)."""
    form = {
        k: v
        for k, v in {
            "grant_type": grant_type,
            "client_id": client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "refresh_token": refresh_token,
            "resource": resource,
            "scope": scope,
        }.items()
        if v is not None
    }
    try:
        out = await svc.token(form)
    except ApplicationError as exc:
        return _oauth_error(exc)
    return JSONResponse(out, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.post("/oauth/revoke", include_in_schema=False)
async def revoke(svc: McpAuth, token: Annotated[str, Form()]) -> Response:
    await svc.revoke_token(token)
    return Response(status_code=200)


@router.post("/oauth/introspect", include_in_schema=False)
async def introspect(request: Request, svc: McpAuth, token: Annotated[str, Form()]) -> Response:
    """For the mcp service only: who a bearer is, its workspace and scopes."""
    if not via_mcp(request):
        raise Forbidden("Not available.", code="forbidden")
    i = await svc.introspect(token, request.app.state.auth_service)
    return JSONResponse(
        {
            "active": i.active,
            "kind": i.kind,
            "workspace_id": str(i.workspace_id) if i.workspace_id else None,
            "user_id": str(i.user_id) if i.user_id else None,
            "scope": " ".join(i.scopes),
            "client_id": i.client_id,
        }
    )


@router.get(f"{WS}/mcp/clients", response_model=GrantList)
async def list_clients(
    workspace_id: uuid.UUID, user: CurrentUser, svc: McpAuth, meta: Meta
) -> GrantList:
    """Connected MCP clients (yours; everyone's for Admins)."""
    rows = await svc.list_grants(user, workspace_id, meta)
    return GrantList(data=[GrantOut.model_validate(r) for r in rows])


@router.delete(f"{WS}/mcp/clients/{{grant_id}}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_client(
    workspace_id: uuid.UUID, grant_id: uuid.UUID, user: CurrentUser, svc: McpAuth, meta: Meta
) -> Response:
    await svc.revoke_grant(user, workspace_id, meta, grant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    f"{WS}/mcp/pending-actions", response_model=PendingOut, status_code=status.HTTP_201_CREATED
)
async def create_pending(
    workspace_id: uuid.UUID,
    body: PendingIn,
    user: CurrentUser,
    svc: McpAuth,
    inbox: Inbox,
    meta: Meta,
) -> PendingOut:
    """Park a high-risk call for confirmation on the web (the MCP server's URL elicitation).
    The caller must be allowed the action now; what the page shows is built here."""
    if body.action == "comment_delete":
        try:
            comment_id = uuid.UUID(str(body.arguments.get("comment_id")))
        except ValueError as exc:
            raise InvalidInput("comment_id is required.", code="invalid_arguments") from exc
        summary = await inbox.describe_delete(user, workspace_id, meta, comment_id)
        arguments: dict[str, Any] = {"comment_id": str(comment_id)}
    else:
        raise InvalidInput("That action cannot be confirmed this way.", code="invalid_action")
    row, url = await svc.create_pending(
        user,
        workspace_id,
        meta,
        action=body.action,
        tool=body.tool,
        arguments=arguments,
        summary=summary,
    )
    out = PendingOut.model_validate(row)
    out.confirm_url = url
    return out


@router.get(f"{WS}/mcp/pending-actions/{{pending_id}}", response_model=PendingOut)
async def get_pending(
    workspace_id: uuid.UUID, pending_id: uuid.UUID, user: CurrentUser, svc: McpAuth, meta: Meta
) -> PendingOut:
    return PendingOut.model_validate(await svc.get_pending(user, workspace_id, meta, pending_id))


@router.post(f"{WS}/mcp/pending-actions/{{pending_id}}/confirm", response_model=PendingOut)
async def confirm_pending(
    workspace_id: uuid.UUID,
    pending_id: uuid.UUID,
    user: HumanUser,
    svc: McpAuth,
    inbox: Inbox,
    meta: Meta,
) -> PendingOut:
    """Confirmed on the web with a second factor (``X-Mera-Second-Factor``); the action runs
    here, as the person, with every check it would have anywhere else."""
    require_human_for_pending(user)
    row = await svc.get_pending(user, workspace_id, meta, pending_id)
    if row.state != "pending":
        return PendingOut.model_validate(row)
    if row.action == "comment_delete":
        await inbox.delete_comment(
            user, workspace_id, meta, uuid.UUID(str(row.arguments["comment_id"]))
        )
        result: dict[str, Any] = {"deleted": str(row.arguments["comment_id"])}
    else:  # pragma: no cover - create_pending only accepts known actions
        raise Forbidden("Unknown action.", code="invalid_action")
    await svc.finish_pending(workspace_id, pending_id, user.user_id, state="done", result=result)
    return PendingOut.model_validate(await svc.get_pending(user, workspace_id, meta, pending_id))


@router.post(f"{WS}/mcp/pending-actions/{{pending_id}}/decline", response_model=PendingOut)
async def decline_pending(
    workspace_id: uuid.UUID, pending_id: uuid.UUID, user: HumanUser, svc: McpAuth, meta: Meta
) -> PendingOut:
    row = await svc.get_pending(user, workspace_id, meta, pending_id)
    if row.state == "pending":
        await svc.finish_pending(
            workspace_id, pending_id, user.user_id, state="declined", result={}
        )
    return PendingOut.model_validate(await svc.get_pending(user, workspace_id, meta, pending_id))
