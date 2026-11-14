"""Slot placement for one account's post (M8.6) and the stagger guardrail (M8.7). Pure.

The stagger offset was fixed when the set was expanded (M5.7) and is stored on the
variant; placement adds it to the requested time and then moves the slot, never earlier,
until it satisfies every rule for that account:

1. not inside the account's effective quiet hours (strictest across levels, ADR-0014 §4);
2. at least ``min_spacing`` from any other post already placed on the same account;
3. no more than ``daily_cap`` posts on the account on that local calendar day.

The result is computed **once**, when a variant is approved, and stored. Retries honour
the stored time, so the calendar shows the time the approver saw (M8 acceptance).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_STEPS = 400


class PlacementImpossible(ValueError):
    """No slot within the search horizon (for example a 24-hour quiet window)."""


def idempotency_key(social_account_id: uuid.UUID, post_version_id: uuid.UUID) -> str:
    """``sha256(social_account_id || post_version_id)`` (docs/03 §8): unique per target by
    construction, and the key handed to adapters so a retried publish reconciles."""
    return hashlib.sha256(social_account_id.bytes + post_version_id.bytes).hexdigest()


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _hm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def quiet_end_after(t: datetime, quiet: dict[str, Any] | None, tz: ZoneInfo) -> datetime | None:
    """If ``t`` falls inside the quiet window, the instant the window ends; else None.
    Windows may wrap midnight (22:00 → 07:00). An empty window (start == end) is none."""
    if not quiet:
        return None
    start, end = _hm(quiet["start"]), _hm(quiet["end"])
    if start == end:
        return None
    local = t.astimezone(tz)
    now_t = local.time().replace(second=0, microsecond=0)
    if start < end:
        inside = start <= now_t < end
        end_day = local.date()
    else:  # wraps midnight
        inside = now_t >= start or now_t < end
        end_day = local.date() + timedelta(days=1) if now_t >= start else local.date()
    if not inside:
        return None
    return datetime.combine(end_day, end, tzinfo=tz).astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Placement:
    at: datetime
    moved_by: list[str]  # which rules moved the slot, for the UI ("quiet hours", ...)


def place(
    *,
    requested: datetime,
    offset_seconds: int,
    existing: Sequence[datetime],
    tz_name: str | None,
    quiet_hours: dict[str, Any] | None,
    daily_cap: int | None,
    min_spacing_seconds: int,
    not_before: datetime | None = None,
) -> Placement:
    if requested.tzinfo is None:
        raise ValueError("requested must be timezone-aware")
    tz = zone(tz_name)
    t = requested + timedelta(seconds=max(0, offset_seconds))
    if not_before is not None and t < not_before:
        t = not_before
    spacing = timedelta(seconds=max(0, min_spacing_seconds))
    others = sorted(e.astimezone(UTC) for e in existing)
    moved: list[str] = []
    for _ in range(MAX_STEPS):
        q = quiet_end_after(t, quiet_hours, tz)
        if q is not None:
            t = q
            _note(moved, "quiet hours")
            continue
        clash = next(
            (e for e in others if abs((t - e).total_seconds()) < spacing.total_seconds()), None
        )
        if clash is not None:
            t = clash + spacing
            _note(moved, "spacing")
            continue
        if daily_cap is not None:
            day = t.astimezone(tz).date()
            same_day = sum(1 for e in others if e.astimezone(tz).date() == day)
            if same_day >= daily_cap:
                t = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=tz).astimezone(UTC)
                _note(moved, "daily cap")
                continue
        return Placement(t, moved)
    raise PlacementImpossible("no slot satisfies quiet hours, spacing and the daily cap")


def _note(moved: list[str], reason: str) -> None:
    if reason not in moved:
        moved.append(reason)


@dataclass(frozen=True, slots=True)
class StaggerItem:
    account_id: uuid.UUID
    platform: str
    at: datetime
    requires_stagger: bool


def stagger_violations(
    items: Iterable[StaggerItem], *, min_gap_seconds: int = 60
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Pairs of **different accounts on one platform** whose posts would land within
    ``min_gap_seconds`` of each other (M8.7, docs/08 §3.3). Same-platform simultaneous
    posting is what gets accounts flagged as coordinated."""
    out: list[tuple[uuid.UUID, uuid.UUID]] = []
    seq = list(items)
    for i, a in enumerate(seq):
        for b in seq[i + 1 :]:
            if (
                a.platform == b.platform
                and a.account_id != b.account_id
                and (a.requires_stagger or b.requires_stagger)
                and abs((a.at - b.at).total_seconds()) < min_gap_seconds
            ):
                out.append((a.account_id, b.account_id))
    return out


# ---------------------------------------------------------------- posting slots (P2-M1.6)


def _hhmm(value: str) -> time:
    hours, minutes = value.split(":", 1)
    return time(int(hours), int(minutes))


def upcoming_posting_times(
    start: datetime, times: Sequence[dict[str, Any]] | None, tz_name: str | None, *, days: int
) -> list[datetime]:
    """Every posting time (``{"day": 0-6 Monday-first, "time": "HH:MM"}``, in the account's
    timezone) from ``start`` for ``days`` days, in UTC, ascending. Daylight-saving gaps are
    resolved by ``zoneinfo``: a 02:30 slot on a spring-forward night lands at 03:30."""
    if start.tzinfo is None:
        raise ValueError("start must be timezone-aware")
    if not times:
        return []
    tz = zone(tz_name)
    local_start = start.astimezone(tz)
    wanted: dict[int, list[time]] = {}
    for t in times:
        wanted.setdefault(int(t["day"]), []).append(_hhmm(str(t["time"])))
    out: list[datetime] = []
    for offset in range(days + 1):
        day = local_start.date() + timedelta(days=offset)
        for at in sorted(wanted.get(day.weekday(), [])):
            moment = datetime.combine(day, at, tzinfo=tz).astimezone(UTC)
            if moment >= start.astimezone(UTC) and moment < start + timedelta(days=days):
                out.append(moment)
    return sorted(set(out))


def next_posting_time(
    after: datetime, times: Sequence[dict[str, Any]] | None, tz_name: str | None
) -> datetime | None:
    """The first posting time at or after ``after``; None when no times are set."""
    upcoming = upcoming_posting_times(after, times, tz_name, days=8)
    return upcoming[0] if upcoming else None
