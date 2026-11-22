"""The ``bots`` service (M7.1, docs/01 §2): terminates Telegram webhooks.

It verifies ``X-Telegram-Bot-Api-Secret-Token`` in constant time (401 and nothing else on
a mismatch), queues the update for the ``approvals`` worker and answers 200 at once:
Telegram retries anything slow, which would duplicate deliveries. It holds no state and
reads no tenant data, so it can restart or scale freely, and it stays up while the API is
redeployed so taps are not lost.
"""

from __future__ import annotations

import hmac
import json
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from mera.api.errors import install_error_handlers
from mera.api.middleware.request_id import RequestIdMiddleware
from mera.config import Settings, get_settings
from mera.infrastructure.queue.deferrer import Deferrer, ProcrastinateDeferrer
from mera.observability import configure_logging, configure_tracing, instrument_fastapi

TASK = "approvals:telegram_update"
WA_TASK = "approvals:whatsapp_update"
MAX_BODY = 1_000_000


async def bounded_body(request: Request, limit: int = MAX_BODY) -> bytes | None:
    """The raw body, or None past ``limit``: a declared length is refused before reading,
    and an undeclared (chunked) one is cut off while streaming, never buffered whole first
    (release review #14)."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                return None
        except ValueError:
            return None
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > limit:
            return None
    return bytes(raw)


def _too_large() -> JSONResponse:
    return JSONResponse({"error": {"code": "too_large", "message": "Too large."}}, status_code=413)


def create_bots_app(settings: Settings | None = None, deferrer: Deferrer | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    configure_tracing(settings, "mera-bots")
    app = FastAPI(title="Mera bots", docs_url=None, redoc_url=None, openapi_url=None)
    instrument_fastapi(app)
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    app.state.deferrer = deferrer or ProcrastinateDeferrer(settings.libpq_dsn)
    secret = (
        settings.telegram_webhook_secret.get_secret_value()
        if settings.telegram_webhook_secret
        else None
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/v1/webhooks/telegram")
    async def telegram(request: Request) -> Response:
        given = request.headers.get("x-telegram-bot-api-secret-token", "")
        if secret is None or not hmac.compare_digest(given.encode(), secret.encode()):
            return JSONResponse(
                {"error": {"code": "unauthorized", "message": "Bad secret."}}, status_code=401
            )
        raw = await bounded_body(request)
        if raw is None:
            return _too_large()
        try:
            update: Any = json.loads(raw)
        except ValueError:
            return Response(status_code=200)  # malformed: acknowledge, never retried
        if not isinstance(update, dict) or "update_id" not in update:
            return Response(status_code=200)
        await app.state.deferrer.defer(
            TASK,
            queue="approvals",
            queueing_lock=f"tg:{update['update_id']}",
            update=update,
        )
        return Response(status_code=200)

    wa_secret = (
        settings.whatsapp_app_secret.get_secret_value() if settings.whatsapp_app_secret else None
    )
    wa_verify = (
        settings.whatsapp_verify_token.get_secret_value()
        if settings.whatsapp_verify_token
        else None
    )

    @app.get("/api/v1/webhooks/whatsapp")
    async def whatsapp_verify(request: Request) -> Response:
        """Meta's subscription handshake: echo ``hub.challenge`` for the right token."""
        q = request.query_params
        token = q.get("hub.verify_token", "")
        if (
            q.get("hub.mode") != "subscribe"
            or wa_verify is None
            or not hmac.compare_digest(token.encode(), wa_verify.encode())
        ):
            return JSONResponse(
                {"error": {"code": "forbidden", "message": "Bad token."}}, status_code=403
            )
        return Response(content=q.get("hub.challenge", ""), media_type="text/plain")

    @app.post("/api/v1/webhooks/whatsapp")
    async def whatsapp(request: Request) -> Response:
        """Verified against X-Hub-Signature-256 over the raw body before anything is
        parsed (docs/07 §9); then queued, and 200 at once."""
        from mera.infrastructure.channels.whatsapp import valid_signature

        raw = await bounded_body(request)
        if raw is None:
            return _too_large()
        if wa_secret is None or not valid_signature(
            wa_secret, raw, request.headers.get("x-hub-signature-256")
        ):
            return JSONResponse(
                {"error": {"code": "unauthorized", "message": "Bad signature."}}, status_code=401
            )
        try:
            payload: Any = json.loads(raw)
        except ValueError:
            return Response(status_code=200)
        if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
            return Response(status_code=200)
        ids = [
            str(m.get("id"))
            for e in payload.get("entry") or []
            for c in e.get("changes") or []
            for m in (c.get("value") or {}).get("messages") or []
        ]
        if not ids:
            return Response(status_code=200)  # delivery statuses: nothing to do
        await app.state.deferrer.defer(
            WA_TASK, queue="approvals", queueing_lock=f"wa:{ids[0]}", payload=payload
        )
        return Response(status_code=200)

    return app


def app_factory() -> FastAPI:
    return create_bots_app()
