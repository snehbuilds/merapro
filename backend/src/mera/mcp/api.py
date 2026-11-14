"""The MCP server's only way in: the REST API, as the caller (P2-M8.1)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field


@dataclass(frozen=True, slots=True)
class Caller:
    """Who the bearer is, from introspection (or from the workspace list for a local key)."""

    workspace_id: str
    workspaces: tuple[tuple[str, str], ...]  # (id, name) the bearer can reach
    scopes: frozenset[str] | None  # None: unknown here; the REST API still enforces
    kind: str = "api_key"


class MeraApi:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        service_secret: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._secret = service_secret
        self._transport = transport
        self._timeout = timeout

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self._token}", "User-Agent": "mera-mcp/1.0"}
        if self._secret:
            h["X-Mera-Service"] = self._secret
        return {**h, **(extra or {})}

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        async with httpx.AsyncClient(
            base_url=self._base, transport=self._transport, timeout=self._timeout
        ) as c:
            r = await c.request(
                method,
                f"/api/v1{path}",
                json=json_body,
                params=clean,
                headers=self._headers(headers),
            )
        if r.status_code == 204 or not r.content:
            if r.status_code >= 400:
                raise ApiError(r.status_code, "error", "The request failed.")
            return None
        try:
            body = r.json()
        except ValueError as exc:
            raise ApiError(
                r.status_code, "bad_response", "Mera returned something unexpected."
            ) from exc
        if r.status_code >= 400:
            err = (body or {}).get("error") or {}
            raise ApiError(
                r.status_code,
                str(err.get("code") or "error"),
                str(err.get("message") or "The request failed."),
                err.get("field"),
            )
        return body

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params=params)

    async def post(
        self, path: str, body: Any = None, *, headers: dict[str, str] | None = None
    ) -> Any:
        return await self.request(
            "POST", path, json_body=body if body is not None else {}, headers=headers
        )

    async def delete(self, path: str) -> Any:
        return await self.request("DELETE", path)

    async def caller(self, default_workspace: str | None = None) -> Caller:
        """Introspect when the service secret is configured; else ask which workspaces the
        bearer reaches (an API key reaches exactly one)."""
        scopes: frozenset[str] | None = None
        kind = "api_key"
        ws_from_token: str | None = None
        if self._secret:
            async with httpx.AsyncClient(
                base_url=self._base, transport=self._transport, timeout=self._timeout
            ) as c:
                r = await c.post(
                    "/api/v1/oauth/introspect",
                    data={"token": self._token},
                    headers={"X-Mera-Service": self._secret},
                )
            info = r.json() if r.status_code == 200 else {"active": False}
            if not info.get("active"):
                raise ApiError(401, "invalid_token", "The credential is not valid.")
            scopes = frozenset(str(info.get("scope") or "").split())
            kind = str(info.get("kind") or "api_key")
            ws_from_token = str(info["workspace_id"]) if info.get("workspace_id") else None
        listed = await self.get("/workspaces")
        rows = tuple((str(w["id"]), str(w["name"])) for w in (listed or {}).get("data", []))
        default = ws_from_token or default_workspace or (rows[0][0] if len(rows) == 1 else "")
        return Caller(workspace_id=default, workspaces=rows, scopes=scopes, kind=kind)


class Sealer:
    """``requestState`` for the modern revision's multi round-trip: an HMAC-sealed blob, so
    any replica can resume a call and a client cannot forge one."""

    def __init__(self, key: bytes) -> None:
        self._key = key

    def seal(self, data: dict[str, Any], ttl: int = 900) -> str:
        raw = json.dumps({**data, "exp": int(time.time()) + ttl}, separators=(",", ":")).encode()
        mac = hmac.new(self._key, raw, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw + mac).decode().rstrip("=")

    def unseal(self, token: str) -> dict[str, Any] | None:
        try:
            blob = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        except (ValueError, TypeError):
            return None
        raw, mac = blob[:-32], blob[-32:]
        if not hmac.compare_digest(mac, hmac.new(self._key, raw, hashlib.sha256).digest()):
            return None
        try:
            data: dict[str, Any] = json.loads(raw)
        except ValueError:
            return None
        if int(data.get("exp", 0)) < time.time():
            return None
        return data
