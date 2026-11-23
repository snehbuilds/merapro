"""The single error envelope (docs/04 §3.1).

``{"error": {code, message, detail, field_errors, request_id, docs_url}}`` for every
non-2xx response. ``code`` is the stable string clients branch on.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from mera.api.middleware.request_id import current_request_id, request_id_from_scope
from mera.application.errors import (
    ApplicationError,
    AuthenticationFailed,
    ConflictError,
    Gone,
    InvalidInput,
    NotFoundError,
    ServiceUnavailableError,
    TooManyAttempts,
)
from mera.application.errors import (
    Forbidden as AccessForbidden,
)
from mera.domain.errors import PermissionDenied

DOCS_BASE = "https://docs.mera.app/errors/"

log = structlog.get_logger(__name__)


class FieldError(BaseModel):
    field: str
    code: str
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    detail: str | None = None
    field_errors: list[FieldError] = []
    request_id: str | None = None
    docs_url: str | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class MeraError(Exception):
    """Raise from anywhere in the request path; rendered into the envelope."""

    status_code: int = 400
    code: str = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        detail: str | None = None,
        field_errors: list[FieldError] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.detail = detail
        self.field_errors = field_errors or []
        self.headers = headers


class NotFound(MeraError):
    """Also used for another workspace's resources: deliberately indistinguishable."""

    status_code = 404
    code = "not_found"


class Forbidden(MeraError):
    status_code = 403
    code = "forbidden"


class Conflict(MeraError):
    status_code = 409
    code = "conflict"


class ServiceUnavailable(MeraError):
    status_code = 503
    code = "service_unavailable"


_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    410: "gone",
    422: "unprocessable",
    429: "rate_limited",
    503: "service_unavailable",
}


def _envelope(
    status: int,
    code: str,
    message: str,
    *,
    detail: str | None = None,
    field_errors: list[FieldError] | None = None,
    headers: Mapping[str, str] | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    request_id = request_id or current_request_id()
    body = ErrorEnvelope(
        error=ErrorBody(
            code=code,
            message=message,
            detail=detail,
            field_errors=field_errors or [],
            request_id=request_id,
            docs_url=DOCS_BASE + code,
        )
    )
    return JSONResponse(status_code=status, content=body.model_dump(), headers=headers)


def json_error_body(code: str, message: str, *, request_id: str | None = None) -> bytes:
    """The envelope as raw bytes, for ASGI middleware that answers before routing."""
    body = ErrorEnvelope(
        error=ErrorBody(
            code=code, message=message, request_id=request_id, docs_url=DOCS_BASE + code
        )
    )
    return body.model_dump_json().encode()


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(MeraError)
    async def _mera(_: Request, exc: MeraError) -> JSONResponse:
        return _envelope(
            exc.status_code,
            exc.code,
            exc.message,
            detail=exc.detail,
            field_errors=exc.field_errors,
            headers=exc.headers,
        )

    @app.exception_handler(ApplicationError)
    async def _application(_: Request, exc: ApplicationError) -> JSONResponse:
        status, headers = 400, None
        if isinstance(exc, NotFoundError):
            status = 404
        elif isinstance(exc, ConflictError):
            status = 409
        elif isinstance(exc, InvalidInput):
            status = 422
        elif isinstance(exc, ServiceUnavailableError):
            status = 503
        elif isinstance(exc, Gone):
            status = 410
        elif isinstance(exc, TooManyAttempts):
            status, headers = 429, {"Retry-After": str(exc.retry_after)}
        elif isinstance(exc, AccessForbidden):
            status = 403
        elif isinstance(exc, AuthenticationFailed):
            status, headers = 401, {"WWW-Authenticate": "Bearer"}
        field_errors = (
            [FieldError(field=exc.field, code=exc.code, message=exc.message)] if exc.field else []
        )
        # ``mfa_required`` carries its challenge token here (P2-M2.3); nothing else does.
        detail = getattr(exc, "mfa_token", None)
        return _envelope(
            status,
            exc.code,
            exc.message,
            detail=detail,
            field_errors=field_errors,
            headers=headers,
        )

    @app.exception_handler(PermissionDenied)
    async def _denied(_: Request, exc: PermissionDenied) -> JSONResponse:
        # The internal reason stays in the audit log; the client gets the generic message.
        return _envelope(403, "forbidden", exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "error")
        message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ")
        return _envelope(exc.status_code, code, message, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors: list[FieldError] = []
        for err in exc.errors():
            loc: tuple[Any, ...] = tuple(err.get("loc", ()))
            # Drop the leading "body"/"query"/"path" segment for a client-facing path.
            path = loc[1:] if loc and loc[0] in {"body", "query", "path", "header"} else loc
            errors.append(
                FieldError(
                    field=".".join(str(p) for p in path) or "request",
                    code=str(err.get("type", "invalid")),
                    message=str(err.get("msg", "Invalid value")),
                )
            )
        return _envelope(400, "validation_error", "The request is invalid.", field_errors=errors)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Runs outside RequestIdMiddleware, so the id comes from the shared scope and the
        # header is set here rather than by the middleware.
        request_id = request_id_from_scope(request.scope)
        log.exception(
            "api.unhandled_exception",
            message="An unexpected API error reached the exception handler.",
            error_type=type(exc).__name__,
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        headers = {"X-Request-ID": request_id} if request_id else None
        return _envelope(
            500,
            "internal_error",
            "Something went wrong on our side.",
            headers=headers,
            request_id=request_id,
        )
