"""Idempotency keys (M2.13, docs/04 §3.4).

For POST, PUT, PATCH and DELETE carrying ``Idempotency-Key``, from an authenticated caller:

- first sight: a row is claimed ``in_progress`` *before* the handler runs, so a concurrent
  duplicate cannot also execute; the response is stored when it completes;
- same principal, same key, same request: the stored response is replayed with
  ``Idempotency-Replayed: true`` — after re-authenticating the caller, so a revoked key or a
  killed session cannot read a cached response;
- same key, different request: 409 ``idempotency_key_reuse``;
- same key while the first is still running: 409 ``idempotency_in_progress`` (a claim
  abandoned for over ``STALE_CLAIM`` by a crashed worker is taken over).

What is never cached (Sprint 3 security review H3, M1):
- ``/api/v1/auth/*``: those responses are session tokens;
- unauthenticated callers: there is no principal to bind a cached response to;
- 5xx (so the client can retry) and 401/403/429 (so a stale credential cannot poison a key
  its owner will later use).

Cached bodies may hold one-time secrets (a new API key, an invitation token), so they are
encrypted at rest with the KeyProvider, bound to the principal.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mera.api.errors import json_error_body
from mera.api.middleware.principal_key import bearer, principal_key
from mera.domain.ports.key_provider import DecryptionError
from mera.infrastructure.db.models import IdempotencyKey

log = structlog.get_logger(__name__)

HEADER = b"idempotency-key"
TTL = timedelta(hours=24)
STALE_CLAIM = timedelta(minutes=2)
MAX_BODY = 1024 * 1024  # requests above this are not eligible for idempotent replay
_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,255}$")
_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_NEVER_CACHED_PREFIXES = ("/api/v1/auth/",)
_UNCACHEABLE_STATUS = {401, 403, 429}
_REPLAY_HEADERS = {"content-type", "location", "etag"}
_PURPOSE = "idempotency"


class IdempotencyMiddleware:
    def __init__(self, app: ASGIApp, *, secret: str) -> None:
        self.app = app
        self.secret = secret

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        raw_key = (
            dict(scope.get("headers") or []).get(HEADER)
            if scope["type"] == "http" and scope["method"] in _METHODS
            else None
        )
        if raw_key is None or scope["path"].startswith(_NEVER_CACHED_PREFIXES):
            await self.app(scope, receive, send)
            return
        who = principal_key(scope, secret=self.secret)
        if who.startswith("ip:"):
            await self.app(scope, receive, send)  # unauthenticated: the route will 401
            return

        request_id = (scope.get("state") or {}).get("request_id")
        key = raw_key.decode("latin-1")
        if not _KEY.match(key):
            await _error(
                send,
                400,
                "invalid_idempotency_key",
                "Idempotency-Key must be 8-255 characters of [A-Za-z0-9._:-].",
                request_id,
            )
            return
        body = await _read_body(receive)
        if body is None:
            await _error(
                send,
                413,
                "request_too_large",
                "Request body too large for an idempotent request.",
                request_id,
            )
            return
        target = scope["path"] + (
            "?" + scope["query_string"].decode("latin-1") if scope.get("query_string") else ""
        )
        request_hash = hashlib.sha256(
            b"\x00".join([scope["method"].encode(), target.encode(), body])
        ).hexdigest()
        state = scope["app"].state
        sm = state.sessionmaker
        now = datetime.now(UTC)

        async with sm() as s, s.begin():
            # Expired keys, and claims abandoned by a crashed request, are released.
            await s.execute(
                delete(IdempotencyKey).where(
                    IdempotencyKey.principal_key == who,
                    IdempotencyKey.key == key,
                    or_(
                        IdempotencyKey.expires_at <= now,
                        (IdempotencyKey.state == "in_progress")
                        & (IdempotencyKey.created_at <= now - STALE_CLAIM),
                    ),
                )
            )
            claimed = (
                await s.execute(
                    insert(IdempotencyKey)
                    .values(
                        principal_key=who,
                        key=key,
                        method=scope["method"],
                        path=scope["path"],
                        request_hash=request_hash,
                        state="in_progress",
                        expires_at=now + TTL,
                    )
                    .on_conflict_do_nothing(index_elements=["principal_key", "key"])
                    .returning(IdempotencyKey.id)
                )
            ).scalar_one_or_none()
            existing = None
            if claimed is None:
                existing = (
                    await s.execute(
                        select(IdempotencyKey).where(
                            IdempotencyKey.principal_key == who, IdempotencyKey.key == key
                        )
                    )
                ).scalar_one()

        if existing is not None:
            if existing.request_hash != request_hash:
                await _error(
                    send,
                    409,
                    "idempotency_key_reuse",
                    "This Idempotency-Key was used for a different request.",
                    request_id,
                )
            elif existing.state != "completed":
                await _error(
                    send,
                    409,
                    "idempotency_in_progress",
                    "A request with this Idempotency-Key is still running.",
                    request_id,
                )
            elif not await _still_authenticated(state, scope):
                # Let the route produce its own 401; never replay to a dead credential.
                await self.app(scope, _replaying(body), send)
            else:
                await _replay(send, existing, state.key_provider, who)
            return

        captured: dict[str, Any] = {"status": 500, "headers": [], "body": b""}

        async def capture_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                captured["status"] = message["status"]
                captured["headers"] = list(message.get("headers", []))
            elif message["type"] == "http.response.body":
                captured["body"] += message.get("body", b"")
            await send(message)

        try:
            await self.app(scope, _replaying(body), capture_send)
        finally:
            await _finish(sm, state.key_provider, who, claimed, captured)


def _replaying(body: bytes) -> Receive:
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return receive


async def _still_authenticated(state: Any, scope: Scope) -> bool:
    credential = bearer(scope)
    if credential is None:
        return False
    try:
        await state.auth_service.authenticate(credential)
    except Exception:
        return False
    return True


async def _finish(sm: Any, kp: Any, who: str, row_id: int, captured: dict[str, Any]) -> None:
    try:
        async with sm() as s, s.begin():
            row = await s.get(IdempotencyKey, row_id)
            if row is None:
                return
            status = captured["status"]
            if status >= 500 or status in _UNCACHEABLE_STATUS:
                await s.delete(row)  # let the client (or the right credential) retry
                return
            row.state = "completed"
            row.response_status = status
            row.response_headers = {
                k.decode("latin-1"): v.decode("latin-1")
                for k, v in captured["headers"]
                if k.decode("latin-1").lower() in _REPLAY_HEADERS
            }
            row.response_body = kp.encrypt(
                captured["body"], workspace_id=None, purpose=_purpose(who)
            ).encode()
    except Exception as exc:
        log.error("idempotency.store_failed", error_type=type(exc).__name__)


def _purpose(who: str) -> str:
    # Bind the ciphertext to its principal: a row copied to another principal won't decrypt.
    return f"{_PURPOSE}.{hashlib.sha256(who.encode()).hexdigest()[:32]}"


async def _read_body(receive: Receive) -> bytes | None:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > MAX_BODY:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


async def _replay(send: Send, row: IdempotencyKey, kp: Any, who: str) -> None:
    try:
        body = kp.decrypt(
            (row.response_body or b"").decode(), workspace_id=None, purpose=_purpose(who)
        )
    except DecryptionError:
        await _error(
            send,
            409,
            "idempotency_unavailable",
            "The stored response for this key cannot be replayed.",
            None,
        )
        return
    headers = [(k.encode(), v.encode()) for k, v in (row.response_headers or {}).items()]
    headers.append((b"idempotency-replayed", b"true"))
    await send({"type": "http.response.start", "status": row.response_status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _error(send: Send, status: int, code: str, message: str, request_id: str | None) -> None:
    body = json_error_body(code, message, request_id=request_id)
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})
