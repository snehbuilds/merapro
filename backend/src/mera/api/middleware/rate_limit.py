"""Rate limiting middleware (M2.12, docs/04 §3.5).

| Class | Limit | Keyed by |
| --- | --- | --- |
| auth (`/api/v1/auth/*` writes, and the OAuth GETs) | 10/min | client IP |
| write (POST, PUT, PATCH, DELETE) | 120/min | principal |
| read (everything else) | 600/min | principal |

Every read and write also counts against a per-IP ceiling (2x the per-principal limit).
Per-account login throttling lives in the auth service, because it needs the email.
Health endpoints are exempt. If the limiter itself fails, the request proceeds and the
failure is logged: an outage in rate limiting must not become an outage of the API.
"""

from __future__ import annotations

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mera.api.errors import json_error_body
from mera.api.middleware.principal_key import client_ip, principal_key
from mera.infrastructure.db.rate_limit import Decision, Limit, take

log = structlog.get_logger(__name__)

AUTH = Limit(capacity=10, per_seconds=60)
WRITE = Limit(capacity=120, per_seconds=60)
READ = Limit(capacity=600, per_seconds=60)
_EXEMPT = ("/healthz", "/readyz", "/metrics")
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


# Per-IP ceilings applied to every request as well as the principal's bucket, so rotating
# or invented credentials cannot buy a fresh bucket each (Sprint 3 review M2).
IP_CEILING = {
    "write": Limit(capacity=240, per_seconds=60),
    "read": Limit(capacity=1200, per_seconds=60),
}


def classify(method: str, path: str) -> tuple[str, Limit]:
    if path.startswith("/api/v1/auth/") and (method in _WRITE_METHODS or "/oauth/" in path):
        return "auth", AUTH
    if method in _WRITE_METHODS:
        return "write", WRITE
    return "read", READ


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, *, secret: str) -> None:
        self.app = app
        self.secret = secret

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].startswith(_EXEMPT):
            await self.app(scope, receive, send)
            return
        name, limit = classify(scope["method"], scope["path"])
        who = (
            f"ip:{client_ip(scope)}" if name == "auth" else principal_key(scope, secret=self.secret)
        )
        decision: Decision | None = None
        try:
            sessionmaker = scope["app"].state.sessionmaker
            decision = await take(sessionmaker, f"rl:{name}:{who}", limit)
            ceiling = IP_CEILING.get(name)
            if ceiling is not None and decision.allowed:
                by_ip = await take(sessionmaker, f"rl:ip:{name}:{client_ip(scope)}", ceiling)
                if not by_ip.allowed:
                    decision, limit = by_ip, ceiling
        except Exception as exc:  # fail open, loudly
            log.warning("rate_limit.unavailable", error_type=type(exc).__name__)

        if decision is not None and not decision.allowed:
            headers = [
                *_headers(limit, decision),
                (b"retry-after", str(decision.retry_after).encode()),
                (b"content-type", b"application/json"),
            ]
            body = json_error_body(
                "rate_limited",
                "Too many requests. Try again shortly.",
                request_id=(scope.get("state") or {}).get("request_id"),
            )
            await send({"type": "http.response.start", "status": 429, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start" and decision is not None:
                message["headers"] = list(message.get("headers", [])) + _headers(limit, decision)
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _headers(limit: Limit, d: Decision) -> list[tuple[bytes, bytes]]:
    return [
        (b"x-ratelimit-limit", str(limit.capacity).encode()),
        (b"x-ratelimit-remaining", str(d.remaining).encode()),
        (b"x-ratelimit-reset", str(d.reset_after).encode()),
    ]
