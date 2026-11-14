"""M8.6 slot placement and M8.7 stagger guardrail (pure)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mera.domain.policies.scheduling import (
    StaggerItem,
    idempotency_key,
    place,
    quiet_end_after,
    stagger_violations,
    zone,
)
from mera.domain.value_objects.publishing import BACKOFF_CAP_SECONDS, backoff_seconds

T0 = datetime(2026, 11, 10, 3, 30, tzinfo=UTC)  # 09:00 in Asia/Kolkata


def _place(**kw: object):  # type: ignore[no-untyped-def]
    args: dict[str, object] = {
        "requested": T0,
        "offset_seconds": 0,
        "existing": [],
        "tz_name": "Asia/Kolkata",
        "quiet_hours": None,
        "daily_cap": None,
        "min_spacing_seconds": 600,
    }
    args.update(kw)
    return place(**args)  # type: ignore[arg-type]


def test_offset_is_added_and_nothing_moves_without_rules() -> None:
    p = _place(offset_seconds=480)
    assert p.at == T0 + timedelta(seconds=480)
    assert p.moved_by == []


def test_quiet_hours_move_to_window_end_wrapping_midnight() -> None:
    # 22:00 → 07:00 IST; 23:30 IST is inside, ends 07:00 IST next day.
    t = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)  # 23:30 IST
    p = _place(requested=t, quiet_hours={"start": "22:00", "end": "07:00"})
    assert p.at.astimezone(zone("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M") == "2026-11-11 07:00"
    assert p.moved_by == ["quiet hours"]


def test_quiet_window_before_midnight_part() -> None:
    t = datetime(2026, 11, 10, 22, 0, tzinfo=UTC)  # 03:30 IST on the 11th
    end = quiet_end_after(t, {"start": "22:00", "end": "07:00"}, zone("Asia/Kolkata"))
    assert end is not None
    assert end.astimezone(zone("Asia/Kolkata")).strftime("%d %H:%M") == "11 07:00"


def test_empty_quiet_window_is_no_window() -> None:
    assert quiet_end_after(T0, {"start": "09:00", "end": "09:00"}, zone("UTC")) is None


def test_spacing_pushes_after_a_close_post() -> None:
    p = _place(existing=[T0 + timedelta(minutes=3)])
    assert p.at == T0 + timedelta(minutes=13)
    assert "spacing" in p.moved_by


def test_daily_cap_moves_to_next_local_day() -> None:
    same_day = [T0 - timedelta(hours=2), T0 - timedelta(hours=1)]
    p = _place(existing=same_day, daily_cap=2)
    assert p.at.astimezone(zone("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M") == "2026-11-11 00:00"
    assert p.moved_by == ["daily cap"]


def test_cap_then_quiet_hours_compose() -> None:
    p = _place(existing=[T0], daily_cap=1, quiet_hours={"start": "23:00", "end": "08:00"})
    local = p.at.astimezone(zone("Asia/Kolkata"))
    assert local.strftime("%Y-%m-%d %H:%M") == "2026-11-11 08:00"
    assert set(p.moved_by) >= {"daily cap", "quiet hours"}


def test_not_before_and_naive_time() -> None:
    p = _place(not_before=T0 + timedelta(hours=1))
    assert p.at == T0 + timedelta(hours=1)
    with pytest.raises(ValueError, match="timezone-aware"):
        _place(requested=datetime(2026, 11, 10, 9, 0))


def test_unknown_timezone_falls_back_to_utc() -> None:
    assert zone("Not/AZone").key == "UTC"
    assert zone(None).key == "UTC"


@settings(max_examples=200, deadline=None)
@given(
    offset=st.integers(min_value=0, max_value=3600),
    others=st.lists(st.integers(min_value=-600, max_value=7200), max_size=8),
    cap=st.one_of(st.none(), st.integers(min_value=1, max_value=10)),
)
def test_placement_never_earlier_and_respects_spacing(
    offset: int, others: list[int], cap: int | None
) -> None:
    existing = [T0 + timedelta(seconds=o) for o in others]
    p = _place(
        offset_seconds=offset,
        existing=existing,
        daily_cap=cap,
        quiet_hours={"start": "22:00", "end": "07:00"},
    )
    assert p.at >= T0 + timedelta(seconds=offset)
    assert all(abs((p.at - e).total_seconds()) >= 600 for e in existing)
    assert quiet_end_after(p.at, {"start": "22:00", "end": "07:00"}, zone("Asia/Kolkata")) is None


def test_idempotency_key_is_per_account_and_version() -> None:
    a, b, v = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert idempotency_key(a, v) == idempotency_key(a, v)
    assert idempotency_key(a, v) != idempotency_key(b, v)
    assert len(idempotency_key(a, v)) == 64


def test_stagger_violation_only_same_platform_different_accounts() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    items = [
        StaggerItem(a, "x", T0, True),
        StaggerItem(b, "x", T0 + timedelta(seconds=20), True),
        StaggerItem(c, "linkedin_member", T0, True),
    ]
    assert stagger_violations(items) == [(a, b)]
    spaced = [StaggerItem(a, "x", T0, True), StaggerItem(b, "x", T0 + timedelta(minutes=4), True)]
    assert stagger_violations(spaced) == []


def test_backoff_grows_caps_and_honours_retry_after() -> None:
    assert backoff_seconds(1) == 60
    assert backoff_seconds(2) == 120
    assert backoff_seconds(30) == BACKOFF_CAP_SECONDS
    assert backoff_seconds(1, retry_after=900) == 900
    assert backoff_seconds(3, retry_after=5) == 240
