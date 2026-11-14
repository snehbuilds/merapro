"""Structured JSON logging with correlation IDs, and OpenTelemetry scaffolding (M1.9).

Every log line carries whatever is bound in the contextvars: ``request_id`` on the API
path, and later ``workspace_id``, ``agent_run_id`` and ``actor`` (docs/01 §7.7).
"""

from __future__ import annotations

import logging
import sys

import structlog

from mera.config import Settings


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
            structlog.processors.add_log_level,
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


def configure_tracing(settings: Settings, service_name: str) -> None:
    """Install a tracer provider. Exporters are wired when an OTLP endpoint is configured;
    the exporter package is added at that point (not a Phase 1 M1 requirement)."""
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    trace.set_tracer_provider(provider)
    if settings.otel_exporter_otlp_endpoint:
        structlog.get_logger(__name__).warning(
            "otel.exporter_not_installed",
            hint="add opentelemetry-exporter-otlp to export spans",
        )
