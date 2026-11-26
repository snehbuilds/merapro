"""Structured logs and OpenTelemetry tracing.

Every log line carries the bound application context (``request_id``, ``workspace_id``,
``agent_run_id``, and ``actor``) plus the active OpenTelemetry trace and span IDs.  OTLP
is deliberately optional: development remains useful with JSON logs alone, while setting
``OTEL_EXPORTER_OTLP_ENDPOINT`` exports logs and traces to any OTLP/HTTP collector.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import MutableMapping
from types import TracebackType
from typing import Protocol
from urllib.parse import unquote, urlsplit, urlunsplit

import structlog
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.engine import Engine

from mera.config import Env

_tracing_configured = False
_httpx_instrumented = False
_exception_hooks_installed = False
_stdlib_otel_handler_installed = False


class LoggingSettings(Protocol):
    """The small configuration surface every runnable service needs for JSON logging."""

    log_level: str
    log_format: str


class TracingSettings(LoggingSettings, Protocol):
    """Configuration needed by a process that exports OTLP traces and logs."""

    mera_env: Env
    otel_exporter_otlp_endpoint: str | None
    @property
    def otel_exporter_otlp_headers(self) -> SecretStr | str | None: ...


class EnvironmentObservabilitySettings:
    """Minimal settings for small services that must not load API/database secrets."""

    def __init__(self) -> None:
        self.log_level = os.environ.get("LOG_LEVEL", "INFO")
        self.log_format = os.environ.get("LOG_FORMAT", "json")
        self.mera_env = Env(os.environ.get("MERA_ENV", "development"))
        self.otel_exporter_otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or None
        self.otel_exporter_otlp_headers = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS") or None


def _otel_attributes(
    event_dict: MutableMapping[str, object],
) -> dict[str, str | bool | int | float]:
    """Keep OTLP log attributes within the OpenTelemetry attribute value types."""
    attributes: dict[str, str | bool | int | float] = {}
    for key, value in event_dict.items():
        if key in {"event", "exc_info", "exception"}:
            continue
        if isinstance(value, (str, bool, int, float)):
            attributes[key] = value
        elif value is not None:
            attributes[key] = str(value)
    return attributes


def _otel_body(event_dict: MutableMapping[str, object]) -> str:
    """Format a concise, searchable OTLP log line while keeping full attributes."""
    event = str(event_dict.get("event", "log"))
    parts: list[str] = [event]
    method = event_dict.get("method")
    path = event_dict.get("path")
    if method and path:
        parts.append(f"{method} {path}")
    for key in ("status_code", "duration_ms", "service", "request_id", "error_type"):
        val = event_dict.get(key)
        if val is not None:
            parts.append(f"{key}={val}")
    return " ".join(parts)


def export_otel_log(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, object]
) -> MutableMapping[str, object]:
    """Mirror each structured application log to OTLP without changing stdout logs."""
    from opentelemetry._logs import LogRecord, get_logger
    from opentelemetry._logs.severity import SeverityNumber

    level = str(event_dict.get("level", "info")).upper()
    severity = {
        "DEBUG": SeverityNumber.DEBUG,
        "INFO": SeverityNumber.INFO,
        "WARNING": SeverityNumber.WARN,
        "WARN": SeverityNumber.WARN,
        "ERROR": SeverityNumber.ERROR,
        "EXCEPTION": SeverityNumber.ERROR,
        "CRITICAL": SeverityNumber.FATAL,
    }.get(level, SeverityNumber.UNSPECIFIED)
    get_logger("mera").emit(
        LogRecord(
            severity_text=level,
            severity_number=severity,
            body=_otel_body(event_dict),
            attributes=_otel_attributes(event_dict),
        )
    )
    return event_dict


def add_trace_context(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, object]
) -> MutableMapping[str, object]:
    """Attach the current W3C trace identifiers to each structlog event.

    IDs are strings rather than numeric fields so log backends preserve leading zeros and
    can use them directly as trace links.
    """
    from opentelemetry import trace

    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        event_dict["trace_id"] = format(context.trace_id, "032x")
        event_dict["span_id"] = format(context.span_id, "016x")
    return event_dict


def configure_logging(settings: LoggingSettings) -> None:
    level = logging.getLevelName(settings.log_level.upper())
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_trace_context,
            structlog.processors.add_log_level,
            export_otel_log,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s")
    # HTTPX's INFO messages include the full request URL, including OAuth query secrets.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    install_exception_hooks()


def install_exception_hooks() -> None:
    """Log uncaught main-thread and background-thread exceptions with their traceback.

    Expected operational failures should be logged at the call site. These hooks are the
    last line of defence so a crashed worker task or service thread never disappears into
    a container log without its exception and stack trace.
    """
    global _exception_hooks_installed
    if _exception_hooks_installed:
        return
    _exception_hooks_installed = True

    def report(
        exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None, *, source: str
    ) -> None:
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            return
        structlog.get_logger("mera.runtime").error(
            "runtime.unhandled_exception",
            message="An unhandled exception reached the process boundary.",
            source=source,
            error_type=exc_type.__name__,
            exc_info=(exc_type, exc, tb),
        )

    def excepthook(
        exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
    ) -> None:
        report(exc_type, exc, tb, source="main_thread")

    def thread_excepthook(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is not None:
            report(args.exc_type, args.exc_value, args.exc_traceback, source="thread")

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook


def install_asyncio_exception_handler(loop: object) -> None:
    """Record unobserved task failures with a traceback when an event loop reports them."""
    import asyncio

    if not isinstance(loop, asyncio.AbstractEventLoop):
        raise TypeError("loop must be an asyncio event loop")

    def handler(active_loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
        exc = context.get("exception")
        if isinstance(exc, BaseException):
            structlog.get_logger("mera.runtime").error(
                "runtime.unobserved_async_exception",
                message="An asynchronous task failed without being awaited.",
                error_type=type(exc).__name__,
                task=str(context.get("task") or context.get("future") or "unknown"),
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            return
        structlog.get_logger("mera.runtime").error(
            "runtime.unobserved_async_error",
            message=str(context.get("message") or "The event loop reported an unknown error."),
        )
        active_loop.default_exception_handler(context)

    loop.set_exception_handler(handler)


def _signal_endpoint(endpoint: str, signal: str) -> str:
    """Accept either an OTLP base URL or a signal-specific OTLP/HTTP endpoint."""
    parsed = urlsplit(endpoint.rstrip("/"))
    if parsed.path.endswith(f"/v1/{signal}"):
        return endpoint
    return urlunsplit((parsed.scheme, parsed.netloc, f"{parsed.path}/v1/{signal}", "", ""))


def _otlp_headers(settings: TracingSettings) -> dict[str, str] | None:
    """Parse the standard OTLP comma-separated header environment variable.

    Grafana Cloud generates ``Authorization=Basic%20…`` for Python, hence URL-decoding
    values after splitting on the first equals sign (the base64 value can contain ``=``).
    """
    raw_headers = settings.otel_exporter_otlp_headers
    if raw_headers is None:
        return None
    value = (
        raw_headers.get_secret_value()
        if hasattr(raw_headers, "get_secret_value")
        else str(raw_headers)
    )
    headers: dict[str, str] = {}
    for item in value.split(","):
        key, separator, value = item.partition("=")
        if not separator or not key.strip():
            raise ValueError("OTEL_EXPORTER_OTLP_HEADERS entries must use name=value format")
        headers[key.strip()] = unquote(value.strip())
    return headers


def configure_tracing(settings: TracingSettings, service_name: str) -> None:
    """Configure OTLP tracing once per process and instrument outbound HTTP clients."""
    global _httpx_instrumented, _stdlib_otel_handler_installed, _tracing_configured

    if _tracing_configured:
        return

    from opentelemetry import trace
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "mera",
            "deployment.environment.name": settings.mera_env.value,
        }
    )
    provider = TracerProvider(resource=resource)
    log_provider = LoggerProvider(resource=resource)
    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=_signal_endpoint(settings.otel_exporter_otlp_endpoint, "traces"),
                    headers=_otlp_headers(settings),
                )
            )
        )
        log_provider.add_log_record_processor(
            BatchLogRecordProcessor(
                OTLPLogExporter(
                    endpoint=_signal_endpoint(settings.otel_exporter_otlp_endpoint, "logs"),
                    headers=_otlp_headers(settings),
                )
            )
        )
    trace.set_tracer_provider(provider)
    set_logger_provider(log_provider)
    if settings.otel_exporter_otlp_endpoint and not _stdlib_otel_handler_installed:
        from opentelemetry.sdk._logs import LoggingHandler

        # structlog events export through ``export_otel_log`` above. This handler covers
        # library/runtime output too, notably Uvicorn access and error logs.
        otel_handler = LoggingHandler(level=logging.NOTSET, logger_provider=log_provider)
        logging.getLogger().addHandler(otel_handler)
        for name in ("uvicorn", "uvicorn.access"):
            logging.getLogger(name).addHandler(otel_handler)
        _stdlib_otel_handler_installed = True
    _tracing_configured = True

    # Instrumenting HTTPX once propagates traceparent to LLM, platform, OAuth, and fetch
    # calls without individual providers having to know about tracing.
    if not _httpx_instrumented:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        _httpx_instrumented = True


def instrument_fastapi(app: FastAPI) -> None:
    """Create server spans for every API request, continuing an incoming traceparent."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz")


def instrument_sqlalchemy(engine: Engine) -> None:
    """Create child spans for SQLAlchemy execution on an application's engine."""
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=engine)
