"""Cap request bodies before anything parses them (review S7-M5).

Starlette spools a whole multipart body to disk before a route runs, so a size check inside
the upload route comes too late. This middleware refuses an upload whose declared length is
over the cap, and stops reading any body (declared or streamed without a length, as the web
app's proxy sends it) once it passes the cap. Other routes keep a general ceiling: none of
them take large bodies.
"""

from __future__ import annotations

import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mera.api.errors import json_error_body
from mera.infrastructure.media.images import MAX_UPLOAD_BYTES

_UPLOAD = re.compile(r"^/api/v1/workspaces/[^/]+/media$")
UPLOAD_LIMIT = MAX_UPLOAD_BYTES + 256 * 1024  # the image plus multipart framing
DEFAULT_LIMIT = 2 * 1024 * 1024


class BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS", "DELETE"):
            await self.app(scope, receive, send)
            return
        upload = bool(_UPLOAD.match(scope["path"]))
        limit = UPLOAD_LIMIT if upload else DEFAULT_LIMIT
        declared: int | None = None
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = -1
        request_id = (scope.get("state") or {}).get("request_id")
        if declared is not None and (declared < 0 or declared > limit):
            text = "Images can be at most 20 MB." if upload else "The request body is too large."
            await _reply(send, 413, "file_too_large", text, request_id)
            return

        seen = 0
        started = False
        exceeded = False

        async def counted() -> Message:
            nonlocal seen, exceeded
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    exceeded = True
                    raise BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if exceeded:
                # The app turned our abort into its own error (Starlette's multipart parser
                # answers 400); say what actually happened instead.
                if message["type"] == "http.response.start" and not started:
                    started = True
                    await _reply(send, 413, "file_too_large", too_large, request_id)
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        too_large = "Images can be at most 20 MB." if upload else "The request body is too large."
        try:
            await self.app(scope, counted, tracking_send)
        except BodyTooLarge:
            if not started:
                await _reply(send, 413, "file_too_large", too_large, request_id)


async def _reply(send: Send, status: int, code: str, message: str, request_id: str | None) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": json_error_body(code, message, request_id=request_id),
        }
    )
