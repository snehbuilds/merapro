"""The browser's address for requests the web app forwards (P2-M0.9).

Every browser request reaches the API through the web app's server-side proxy, so the
socket peer is the web server for everybody. Left alone, per-IP limits (login lockout,
anonymous rate limits) would be shared by every user, and the audit log would record one
address for all of them.

The web app already proves itself with ``X-Mera-Proxy-Secret``. Only when that secret
matches is its ``X-Mera-Client-IP`` believed, and only if it parses as an IP address; the
scope's ``client`` is then rewritten before anything else reads it. A request without the
secret keeps its socket peer, so a script cannot claim another address by sending the
header. Deployments with a reverse proxy directly in front of the API (the public API,
the bots webhook) use uvicorn's own ``FORWARDED_ALLOW_IPS`` for the same purpose.
"""

from __future__ import annotations

import hmac
import ipaddress

from starlette.types import ASGIApp, Receive, Scope, Send

PROXY_SECRET_HEADER = b"x-mera-proxy-secret"
CLIENT_IP_HEADER = b"x-mera-client-ip"


def _ip(raw: bytes | None) -> str | None:
    if not raw or len(raw) > 64:
        return None
    try:
        return str(ipaddress.ip_address(raw.decode("ascii").strip()))
    except (UnicodeDecodeError, ValueError):
        return None


class TrustedClientIPMiddleware:
    def __init__(self, app: ASGIApp, *, proxy_secret: str) -> None:
        self.app = app
        self._secret = proxy_secret.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            claimed = headers.get(PROXY_SECRET_HEADER)
            if claimed is not None and hmac.compare_digest(claimed, self._secret):
                ip = _ip(headers.get(CLIENT_IP_HEADER))
                if ip is not None:
                    scope = {**scope, "client": (ip, 0)}
        await self.app(scope, receive, send)
