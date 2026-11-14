"""The ``PlatformAdapter`` port (M8.1, ADR-0008).

An adapter publishes **one account's** post and reads that account's metrics. It never
decides *whether* to publish: ``assert_publishable`` has already run. Everything platform
specific (endpoints, media upload dances, facets, first-comment links) lives behind it, and
every failure is classified so the worker knows whether retrying can help.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class PublishError(Exception):
    """Base: something went wrong talking to the platform."""

    retryable: bool = False

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class TransientError(PublishError):
    """5xx, timeouts, connection resets: retry with backoff."""

    retryable = True


class RateLimited(TransientError):
    """429 or an exhausted quota: retry after ``retry_after`` seconds (M8 acceptance: a 429
    backs off rather than dead-lettering)."""

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message, status=429)
        self.retry_after = retry_after


class PermanentError(PublishError):
    """The platform refused this content (validation, policy): retrying cannot help."""


class AuthExpired(PermanentError):
    """The grant is gone: the account moves to NEEDS_REAUTH and only it stops."""


@dataclass(frozen=True, slots=True)
class MediaFile:
    """One image, already rendered for this platform."""

    data: bytes
    mime_type: str
    alt_text: str | None
    width: int | None = None
    height: int | None = None
    public_url: str | None = None  # signed URL, for platforms that fetch media themselves


@dataclass(frozen=True, slots=True)
class PublishPayload:
    """Exactly the approved version's content for one account."""

    platform: str
    platform_account_id: str
    account_type: str
    body: str
    thread_parts: list[str] | None
    hashtags: list[str]
    link_url: str | None
    link_placement: str  # body | first_comment | reply | none
    media: list[MediaFile]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class PublishResult:
    platform_post_id: str
    permalink: str | None
    extra_ids: dict[str, str] = field(default_factory=dict)  # first comment, thread parts
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MetricReading:
    """One normalised metric (M10.3). ``value`` None means "the platform does not report
    this"; it is rendered "Not reported", never zero."""

    metric_key: str
    value: float | None
    platform_field: str


class PlatformAdapter(Protocol):
    platform: str

    async def publish(
        self, credentials: dict[str, Any], payload: PublishPayload
    ) -> PublishResult: ...

    async def find_existing(
        self, credentials: dict[str, Any], payload: PublishPayload, since: datetime
    ) -> PublishResult | None:
        """Reconciliation after a lost response (M8 acceptance: no double post). Returns the
        post that was already created for this payload, if the platform can tell."""
        ...

    async def post_metrics(
        self, credentials: dict[str, Any], platform_account_id: str, platform_post_id: str
    ) -> list[MetricReading]: ...

    async def account_metrics(
        self, credentials: dict[str, Any], platform_account_id: str
    ) -> list[MetricReading]: ...
