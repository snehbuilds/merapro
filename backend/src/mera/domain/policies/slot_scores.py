"""Slot scores and time proposals (P2-M7.1, docs/06 §4.8).

The Scheduler agent's judgement is statistics, not a model call: for one account, how did
posts published in each local hour of the week do, relative to that account's own median?
Buckets with few posts are shrunk toward the median (a prior of ``PRIOR_POSTS`` median
posts), and one post counts as at most three times typical, so one lucky post does not
move the schedule.

A proposal is only an input. ``place`` (``scheduling.place``) still applies quiet hours, the
daily cap and spacing to whatever is proposed, and it is the only thing that writes a time.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Final

from mera.domain.policies.scheduling import zone

PRIOR_POSTS: Final = 3.0
MAX_RATIO: Final = 3.0  # one viral post counts as 3x typical, no more
MIN_HISTORY: Final = 5  # fewer published posts than this: no proposal
DAYS: Final = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")


@dataclass(frozen=True, slots=True)
class SlotScore:
    weekday: int  # 0 = Monday, local time
    hour: int
    score: float  # smoothed engagement relative to the account median (1.0 = typical)
    samples: int


@dataclass(frozen=True, slots=True)
class Proposal:
    at: datetime
    score: float
    samples: int
    reason: str


def score_slots(history: Iterable[tuple[datetime, float]], tz_name: str | None) -> list[SlotScore]:
    """``history``: (published_at, engagement) per post. Highest score first."""
    tz = zone(tz_name)
    rows = [(t.astimezone(tz), max(0.0, e)) for t, e in history]
    if len(rows) < MIN_HISTORY:
        return []
    base = median(e for _, e in rows) or 1.0
    buckets: dict[tuple[int, int], list[float]] = defaultdict(list)
    for t, e in rows:
        buckets[(t.weekday(), t.hour)].append(min(MAX_RATIO, e / base))
    out = []
    for (wd, hr), vals in buckets.items():
        n = len(vals)
        smoothed = (sum(vals) + PRIOR_POSTS * 1.0) / (n + PRIOR_POSTS)
        out.append(SlotScore(wd, hr, round(smoothed, 4), n))
    return sorted(out, key=lambda s: (-s.score, -s.samples, s.weekday, s.hour))


def propose(
    scores: Sequence[SlotScore],
    *,
    after: datetime,
    tz_name: str | None,
    horizon: timedelta = timedelta(days=7),
    count: int = 3,
    min_samples: int = 1,
) -> list[Proposal]:
    """The next occurrence of each of the best-scoring slots (better than typical only,
    and measured on at least ``min_samples`` posts), within ``horizon`` of ``after``,
    soonest first among equals."""
    if after.tzinfo is None:
        raise ValueError("after must be timezone-aware")
    tz = zone(tz_name)
    local = after.astimezone(tz)
    found: list[Proposal] = []
    for sc in scores:
        if sc.score <= 1.0 or len(found) >= count:
            break
        if sc.samples < min_samples:
            continue  # promising, but not yet evidence (P3-M0.7)
        days = (sc.weekday - local.weekday()) % 7
        at = (local + timedelta(days=days)).replace(hour=sc.hour, minute=0, second=0, microsecond=0)
        if at <= local:
            at += timedelta(days=7)
        if at - local > horizon:
            continue
        found.append(
            Proposal(
                at=at.astimezone(UTC),
                score=sc.score,
                samples=sc.samples,
                reason=(
                    f"{DAYS[sc.weekday]} {sc.hour:02d}:00 averaged {sc.score:.1f} times this "
                    f"account's typical engagement over {sc.samples} post"
                    f"{'s' if sc.samples != 1 else ''}"
                ),
            )
        )
    return found
