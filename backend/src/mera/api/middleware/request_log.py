"""Safe, structured HTTP access logs for every FastAPI service."""

from __future__ import annotations

from time import perf_counter

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mera.api.middleware.request_id import request_id_from_scope

log = structlog.get_logger(__name__)


class RequestLogMiddleware:
    """Emit one OTLP-exported event for every completed HTTP request.

    URLs often carry OAuth codes and other sensitive values, so this records only the
    route path—not query strings, headers, cookies, or request/response bodies.
    """

    def __init__(self, app: ASGIApp, *, service: str) -> None:
        self.app = app
        self.service = service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        status_code = 500
        response_bytes = 0

        async def send_with_metrics(message: Message) -> None:
            nonlocal response_bytes, status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, send_with_metrics)
        finally:
            log.info(
                "http.request.completed",
                message="HTTP request completed.",
                service=self.service,
                request_id=request_id_from_scope(scope),
                method=scope.get("method"),
                path=scope.get("path"),
                status_code=status_code,
                duration_ms=round((perf_counter() - started) * 1000, 2),
                response_bytes=response_bytes,
            )
