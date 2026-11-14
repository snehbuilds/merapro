"""Mera as an MCP client (P2-M9.2, docs/05 §2.1): dual-era, Streamable HTTP or the sandbox.

- **Detection.** A modern ``server/discover`` first. A JSON-RPC result means a modern
  server; a 4xx without a modern error body, or a method-not-found, means legacy, and the
  client falls back to ``initialize`` and a session (``Mcp-Session-Id``).
- **Declares no Sampling and no Roots**, and declines elicitation: an unattended agent run
  has nobody to answer, so an ``input_required`` result is an error recorded on the call.
- **No token passthrough.** The only credential sent is the one stored for this server.
- **Egress.** A remote URL must resolve to public addresses only (the SSRF guard from
  docs/09), checked before every connection; stdio servers run in ``mcp-sandbox``,
  reached over its relay.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

import httpx

from mera.infrastructure.http.safe_fetch import FetchError, guarded_transport, validate_public_url

MODERN: Final = "2026-07-28"
LEGACY: Final = "2025-11-25"
CLIENT_INFO: Final = {"name": "mera", "version": "2.0.0"}


class McpClientError(Exception):
    pass


MAX_RESPONSE: Final = 4 * 1024 * 1024  # a tools/list or a result: bounded, never trusted


def public_url(url: str, *, allow_private: bool = False) -> str:
    """The URL, if it is https and names no obviously internal host. This is the friendly
    save-time check; the real control is the guarded transport, which resolves, checks
    every address and connects to the one it checked (no DNS rebinding window)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme not in ("https", "http") or not host:
        raise McpClientError("The server URL must be an https URL.")
    if allow_private:
        return url
    if parts.scheme != "https":
        raise McpClientError("The server URL must use https.")
    problem = validate_public_url(url)
    if problem:
        raise McpClientError(f"The server URL is not allowed: {problem}.")
    return url


@dataclass
class ToolInfo:
    name: str
    title: str | None
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    annotations: dict[str, Any]


@dataclass
class Probe:
    era: str
    version: str
    server_name: str | None = None
    tools: list[ToolInfo] = field(default_factory=list)


class Transport(Protocol):
    async def send(
        self, message: dict[str, Any], session: str | None
    ) -> tuple[int, Any, str | None]:
        """(http status, json body or None, session id from the response)."""
        ...


class HttpTransport:
    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        allow_private: bool = False,
    ) -> None:
        self._url = public_url(url, allow_private=allow_private)
        self._headers = headers or {}
        # Tests inject a transport; otherwise every connection goes through the guard.
        self._t = transport if transport is not None or allow_private else guarded_transport()

    async def send(
        self, message: dict[str, Any], session: str | None
    ) -> tuple[int, Any, str | None]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._headers,
        }
        if session:
            headers["Mcp-Session-Id"] = session
        try:
            async with (
                httpx.AsyncClient(
                    timeout=60, transport=self._t, follow_redirects=False, trust_env=False
                ) as c,
                c.stream("POST", self._url, json=message, headers=headers) as r,
            ):
                raw = bytearray()
                async for chunk in r.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE:
                        raise McpClientError("the server's response is too large")
                status, ctype, sid = (
                    r.status_code,
                    r.headers.get("content-type", ""),
                    r.headers.get("mcp-session-id"),
                )
        except FetchError as exc:
            raise McpClientError(f"the server cannot be reached ({exc.kind})") from exc
        except httpx.HTTPError as exc:
            cause = exc.__context__ or exc.__cause__
            if isinstance(cause, FetchError):
                raise McpClientError(f"the server cannot be reached ({cause.kind})") from exc
            raise McpClientError(f"could not reach the server ({type(exc).__name__})") from exc
        body: Any = None
        if raw:
            text = raw.decode("utf-8", errors="replace")
            if ctype.startswith("text/event-stream"):
                # A single-response SSE stream: take the last data line.
                data = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]
                text = data[-1] if data else ""
            try:
                body = json.loads(text) if text else None
            except ValueError:
                body = None
        return status, body, sid


class SandboxTransport:
    """JSON-RPC frames relayed to a stdio server inside ``mcp-sandbox``."""

    def __init__(
        self,
        relay_url: str,
        server_id: str,
        *,
        token: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = f"{relay_url.rstrip('/')}/servers/{server_id}/rpc"
        self._token = token
        self._t = transport

    async def send(
        self, message: dict[str, Any], session: str | None
    ) -> tuple[int, Any, str | None]:
        if not self._token:
            raise McpClientError("the sandbox is not configured (MCP_SANDBOX_TOKEN)")
        try:
            async with httpx.AsyncClient(timeout=60, transport=self._t, trust_env=False) as c:
                r = await c.post(self._url, json=message, headers={"X-Sandbox-Token": self._token})
        except httpx.HTTPError as exc:
            raise McpClientError(f"the sandbox is unavailable ({type(exc).__name__})") from exc
        try:
            body = r.json() if r.content else None
        except ValueError:
            body = None
        return r.status_code, body, None


class McpClient:
    def __init__(self, transport: Transport, *, era: str | None = None) -> None:
        self._t = transport
        self._era = era
        self._session: str | None = None
        self._ids = 0
        self._ready = False

    def _msg(
        self, method: str, params: dict[str, Any] | None = None, *, notify: bool = False
    ) -> dict[str, Any]:
        p = dict(params or {})
        if self._era == "modern":
            p["_meta"] = {
                "protocolVersion": MODERN,
                "clientInfo": CLIENT_INFO,
                "clientCapabilities": {},  # no sampling, no roots, no elicitation
            }
        m: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": p}
        if not notify:
            self._ids += 1
            m["id"] = self._ids
        return m

    async def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        status, body, session = await self._t.send(self._msg(method, params), self._session)
        if session:
            self._session = session
        if status >= 400 or not isinstance(body, dict):
            raise McpClientError(f"{method} failed (HTTP {status})")
        if "error" in body:
            err = body["error"] or {}
            raise McpClientError(f"{method}: {err.get('message', 'error')} ({err.get('code')})")
        result = body.get("result")
        if not isinstance(result, dict):
            raise McpClientError(f"{method} returned no result")
        return result

    async def probe(self) -> Probe:
        """Detect the era (docs/05 §2.1), then list the tools."""
        self._era = "modern"
        status, body, _ = await self._t.send(self._msg("server/discover"), None)
        modern = (
            status < 400
            and isinstance(body, dict)
            and isinstance(body.get("result"), dict)
            and MODERN in (body["result"].get("protocolVersions") or [])
        )
        name = None
        if modern:
            name = ((body or {}).get("result", {}).get("serverInfo") or {}).get("name")
            version = MODERN
        else:
            self._era = "legacy"
            init = await self._rpc(
                "initialize",
                {"protocolVersion": LEGACY, "capabilities": {}, "clientInfo": CLIENT_INFO},
            )
            version = str(init.get("protocolVersion") or LEGACY)
            name = (init.get("serverInfo") or {}).get("name")
            await self._t.send(self._msg("notifications/initialized", notify=True), self._session)
            self._ready = True
        tools = await self.list_tools()
        return Probe(era=self._era or "legacy", version=version, server_name=name, tools=tools)

    async def _ensure(self) -> None:
        if self._era is None:
            await self.probe()
        elif self._era == "legacy" and not self._ready:
            await self._rpc(
                "initialize",
                {"protocolVersion": LEGACY, "capabilities": {}, "clientInfo": CLIENT_INFO},
            )
            await self._t.send(self._msg("notifications/initialized", notify=True), self._session)
            self._ready = True

    async def list_tools(self) -> list[ToolInfo]:
        out: list[ToolInfo] = []
        cursor = None
        for _ in range(20):
            result = await self._rpc("tools/list", {"cursor": cursor} if cursor else {})
            for t in result.get("tools") or []:
                if not isinstance(t, dict) or not t.get("name"):
                    continue
                out.append(
                    ToolInfo(
                        name=str(t["name"])[:128],
                        title=t.get("title") or (t.get("annotations") or {}).get("title"),
                        description=str(t.get("description") or "")[:8000],
                        input_schema=t.get("inputSchema") or {},
                        output_schema=t.get("outputSchema"),
                        annotations=t.get("annotations") or {},
                    )
                )
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return out

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await self._ensure()
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("resultType") == "input_required":
            # Nobody is present to answer during an agent run: decline (docs/05 §2.1).
            raise McpClientError("the server asked for input; declined in an unattended run")
        return result
