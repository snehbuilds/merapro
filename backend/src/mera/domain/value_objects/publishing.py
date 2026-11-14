"""Scheduling and publishing vocabulary (M8; docs/03 §8)."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class ScheduledState(StrEnum):
    QUEUED = "queued"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"  # a retry is scheduled
    DEAD_LETTERED = "dead_lettered"
    CANCELLED = "cancelled"


OPEN_SCHEDULED: Final = frozenset({ScheduledState.QUEUED, ScheduledState.FAILED})
TERMINAL_SCHEDULED: Final = frozenset(
    {ScheduledState.PUBLISHED, ScheduledState.DEAD_LETTERED, ScheduledState.CANCELLED}
)

# Retry policy (M8.9): exponential backoff from 1 minute, at most five attempts in total.
MAX_PUBLISH_ATTEMPTS: Final = 5
BACKOFF_BASE_SECONDS: Final = 60
BACKOFF_CAP_SECONDS: Final = 3600
# Two posts on one account are never closer than this (M8.6 minimum spacing).
MIN_ACCOUNT_SPACING_SECONDS: Final = 600
# A held post (kill switch) is looked at again after this long.
HOLD_RECHECK_SECONDS: Final = 300


def backoff_seconds(attempt: int, retry_after: int | None = None) -> int:
    """Delay before attempt ``attempt + 1``. A platform's own ``Retry-After`` wins when it is
    longer, so a 429 is honoured rather than hammered."""
    delay: int = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** max(0, attempt - 1)))
    if retry_after is not None:
        delay = max(delay, min(int(retry_after), 6 * BACKOFF_CAP_SECONDS))
    return delay
