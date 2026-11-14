"""P2-M1 pure parts: posting times, hashtag matching, template briefs."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mera.application.errors import InvalidInput
from mera.application.hashtags import banned_tags_in, normalize_tag
from mera.application.services.template_service import brief_with_outline
from mera.domain.policies.scheduling import next_posting_time, upcoming_posting_times

LA = "America/Los_Angeles"


def test_next_posting_time_wraps_to_next_week() -> None:
    times = [{"day": 0, "time": "09:00"}]  # Mondays
    tuesday = datetime(2026, 11, 10, 18, tzinfo=UTC)
    nxt = next_posting_time(tuesday, times, LA)
    assert nxt == datetime(2026, 11, 16, 17, tzinfo=UTC)  # Monday 09:00 PST


def test_upcoming_times_cross_daylight_saving() -> None:
    # 2026-03-08: US clocks spring forward at 02:00. A 02:30 slot does not exist that day.
    times = [{"day": d, "time": "02:30"} for d in range(7)]
    start = datetime(2026, 3, 7, 0, tzinfo=UTC)
    got = upcoming_posting_times(start, times, LA, days=3)
    assert len(got) == len(set(got))
    assert all(g >= start for g in got)
    offsets = {(g - datetime(2026, 3, 7, tzinfo=UTC)).total_seconds() // 3600 for g in got}
    assert len(offsets) == len(got)  # one slot per day, the nonexistent one resolved


def test_no_times_means_no_slots() -> None:
    assert upcoming_posting_times(datetime(2026, 1, 1, tzinfo=UTC), None, LA, days=7) == []
    assert next_posting_time(datetime(2026, 1, 1, tzinfo=UTC), [], LA) is None
    with pytest.raises(ValueError, match="timezone-aware"):
        upcoming_posting_times(datetime(2026, 1, 1), [{"day": 0, "time": "09:00"}], LA, days=1)


def test_normalize_tag() -> None:
    assert normalize_tag("  #MachineLearning ") == "machinelearning"
    for bad in ("", "#", "two words", "a,b", "x" * 101):
        with pytest.raises(InvalidInput):
            normalize_tag(bad)


def test_banned_tags_found_as_hashtags_and_inline() -> None:
    banned = {"crypto", "ml"}
    assert banned_tags_in("Plain text", ["#Crypto"], banned) == ["crypto"]
    assert banned_tags_in("Our #ML stack and more", [], banned) == ["ml"]
    # Not a hashtag: an anchor in a URL or an HTML entity.
    assert banned_tags_in("See https://x.io/page#ml and &#ml;", [], banned) == []
    assert banned_tags_in("anything #crypto", ["#crypto"], set()) == []


def test_template_brief() -> None:
    assert brief_with_outline("Topic", None) == "Topic"
    out = brief_with_outline("Topic", "Hook, then data.", ["a", "b"])
    assert "Structure to follow" in out
    assert out.endswith("#a #b")
