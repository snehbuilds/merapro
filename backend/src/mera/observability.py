"""Structured logs and OpenTelemetry tracing.

Every log line carries the bound application context (``request_id``, ``workspace_id``,
``agent_run_id``, and ``actor``) plus the active OpenTelemetry trace and span IDs.  OTLP
is deliberately optional: development remains useful with JSON logs alone, while setting
``OTEL_EXPORTER_OTLP_ENDPOINT`` exports logs and traces to any OTLP/HTTP collector.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from urllib.parse import unquote, urlsplit, urlunsplit

import structlog
from fastapi import FastAPI
from sqlalchemy.engine import Engine

from mera.config import Settings

_tracing_configured = False
_httpx_instrumented = False


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
            body=str(event_dict.get("event", "log")),
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


def configure_logging(settings: Settings) -> None:
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


def _signal_endpoint(endpoint: str, signal: str) -> str:
    """Accept either an OTLP base URL or a signal-specific OTLP/HTTP endpoint."""
    parsed = urlsplit(endpoint.rstrip("/"))
    if parsed.path.endswith(f"/v1/{signal}"):
        return endpoint
    return urlunsplit((parsed.scheme, parsed.netloc, f"{parsed.path}/v1/{signal}", "", ""))


def _otlp_headers(settings: Settings) -> dict[str, str] | None:
    """Parse the standard OTLP comma-separated header environment variable.

    Grafana Cloud generates ``Authorization=Basic%20…`` for Python, hence URL-decoding
    values after splitting on the first equals sign (the base64 value can contain ``=``).
    """
    if settings.otel_exporter_otlp_headers is None:
        return None
    headers: dict[str, str] = {}
    for item in settings.otel_exporter_otlp_headers.get_secret_value().split(","):
        key, separator, value = item.partition("=")
        if not separator or not key.strip():
            raise ValueError("OTEL_EXPORTER_OTLP_HEADERS entries must use name=value format")
        headers[key.strip()] = unquote(value.strip())
    return headers


def configure_tracing(settings: Settings, service_name: str) -> None:
    """Configure OTLP tracing once per process and instrument outbound HTTP clients."""
    global _httpx_instrumented, _tracing_configured

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
