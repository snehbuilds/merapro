"""Streamable HTTP transport for Mera's MCP server (P2-M8.2, docs/05 §1.1).

- ``POST /mcp``: one JSON-RPC message (or a batch) per request; any replica answers.
- A bearer is required: an MCP OAuth token or a workspace API key. Without one, 401 with
  ``WWW-Authenticate`` pointing at the Protected Resource Metadata (RFC 9728).
- A tool whose scope the token lacks gets 403 ``insufficient_scope`` naming the scope, so
  the client can ask the user for just that (incremental consent).
- ``Origin`` is checked on every request; an unknown origin gets 403.
- No server-to-client stream: ``GET /mcp`` is 405.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from mera.config import Settings
from mera.domain.policies.rbac import KNOWN_SCOPES
from mera.mcp.api import ApiError, Caller, MeraApi, Sealer
from mera.mcp.server import Context, InsufficientScope, McpServer
from mera.mcp.settings import McpServiceSettings

CACHE_SECONDS = 60


def create_mcp_app(
    settings: Settings | McpServiceSettings | None = None,
    *,
    api_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or McpServiceSettings()
    app = FastAPI(title="Mera MCP", docs_url=None, redoc_url=None, openapi_url=None)
    server = McpServer()
    secret = settings.mcp_service_secret.get_secret_value() if settings.mcp_service_secret else None
    # The sealed requestState is keyed by the service secret, which every replica holds;
    # without one the service cannot call the API anyway, so a per-process key will do.
    sealer = Sealer(
        hashlib.sha256(b"mcp-state|" + secret.encode()).digest()
        if secret
        else secrets.token_bytes(32)
    )
    resource = settings.mcp_resource_url.rstrip("/")
    internal_api = getattr(settings, "mcp_api_url", None) or settings.mera_api_url
    prm = resource.rsplit("/", 1)[0] + "/.well-known/oauth-protected-resource"
    allowed = {o.rstrip("/") for o in settings.mcp_allowed_origins} | {
        settings.mera_public_url.rstrip("/")
    }
    callers: dict[str, tuple[float, Caller]] = {}

    def challenge(status: int, error: str | None = None, scope: str | None = None) -> Response:
        parts = [f'resource_metadata="{prm}"']
        if error:
            parts.insert(0, f'error="{error}"')
        if scope:
            parts.append(f'scope="{scope}"')
        return JSONResponse(
            {"error": error or "unauthorized"},
            status_code=status,
            headers={"WWW-Authenticate": "Bearer " + ", ".join(parts)},
        )

    @app.get("/.well-known/oauth-protected-resource")
    async def protected_resource() -> dict[str, Any]:
        return {
            "resource": resource,
            "authorization_servers": [settings.mera_api_url.rstrip("/")],
            "scopes_supported": sorted(KNOWN_SCOPES - {"admin", "accounts:write"}),
            "bearer_methods_supported": ["header"],
            "resource_name": "Mera",
        }

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/mcp")
    async def no_stream() -> Response:
        return Response(status_code=405, headers={"Allow": "POST"})

    @app.post("/mcp")
    async def mcp(request: Request) -> Response:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in allowed:
            return JSONResponse({"error": "forbidden_origin"}, status_code=403)
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer ") or len(auth) < 16:
            return challenge(401)
        token = auth[7:].strip()
        api = MeraApi(internal_api, token, service_secret=secret, transport=api_transport)
        key = hashlib.sha256(token.encode()).hexdigest()
        cached = callers.get(key)
        if cached is None or cached[0] < time.monotonic():
            try:
                who = await api.caller()
            except ApiError:
                return challenge(401, "invalid_token")
            callers[key] = (time.monotonic() + CACHE_SECONDS, who)
            if len(callers) > 5000:
                callers.clear()
        caller = callers[key][1]

        async def ctx_factory(era: str, url_ok: bool, form_ok: bool) -> Context:
            return Context(api, caller, sealer, era, url_ok, form_ok)

        try:
            body = json.loads(await request.body())
        except ValueError:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error."},
                },
                status_code=400,
            )
        negotiated = request.headers.get("mcp-protocol-version")
        try:
            if isinstance(body, list):
                out = [
                    r
                    for m in body
                    if (r := await server.handle(m, ctx_factory, negotiated=negotiated))
                ]
                return JSONResponse(out) if out else Response(status_code=202)
            result = await server.handle(body, ctx_factory, negotiated=negotiated)
        except InsufficientScope as exc:
            return challenge(403, "insufficient_scope", exc.scope)
        if result is None:
            return Response(status_code=202)
        headers = {}
        if isinstance(body, dict) and body.get("method") == "initialize":
            headers["MCP-Protocol-Version"] = str(
                result.get("result", {}).get("protocolVersion", "")
            )
        return JSONResponse(result, headers=headers)

    return app


def app_factory() -> FastAPI:
    return create_mcp_app()
