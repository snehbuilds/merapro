"""Correlation ID per request: accepted from ``X-Request-ID`` or minted, echoed back,
bound into structlog's contextvars, and placed in every error envelope."""

from __future__ import annotations

import re
from contextvars import ContextVar

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mera.domain.value_objects.ids import uuid7

HEADER = "x-request-id"
_SAFE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def current_request_id() -> str | None:
    return _request_id.get()


def request_id_from_scope(scope: Scope) -> str | None:
    state = scope.get("state") or {}
    value = state.get("request_id")
    return value if isinstance(value, str) else None


class RequestIdMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so contextvars propagate into handlers and SSE."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or []).get(HEADER.encode())
        candidate = incoming.decode("latin-1") if incoming else ""
        request_id = candidate if _SAFE.match(candidate) else str(uuid7())

        # Also kept on the shared scope: Starlette runs the catch-all 500 handler in the
        # outermost ServerErrorMiddleware, after this middleware's contextvar is reset.
        scope.setdefault("state", {})["request_id"] = request_id
        token = _request_id.set(request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((HEADER.encode(), request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
            _request_id.reset(token)
