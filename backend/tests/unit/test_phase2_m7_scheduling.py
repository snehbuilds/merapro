"""P2-M7 acceptance: no agent proposal can place a post inside quiet hours, above the daily
cap or closer than the spacing rules. A property test over random proposals feeds them
to ``place`` (the only thing that writes a time) and checks every rule on the result."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mera.domain.policies.scheduling import PlacementImpossible, place, quiet_end_after, zone
from mera.domain.policies.slot_scores import propose, score_slots

T0 = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)  # a Monday
ZONES = ("UTC", "America/Los_Angeles", "Asia/Kolkata", "Europe/Berlin")


@st.composite
def scenario(draw: st.DrawFn) -> dict[str, object]:
    tz = draw(st.sampled_from(ZONES))
    proposal = T0 + timedelta(minutes=draw(st.integers(0, 14 * 24 * 60)))
    existing = [
        T0 + timedelta(minutes=draw(st.integers(0, 16 * 24 * 60)))
        for _ in range(draw(st.integers(0, 12)))
    ]
    quiet = None
    if draw(st.booleans()):
        start = draw(st.integers(0, 23))
        length = draw(st.integers(1, 12))
        quiet = {"start": f"{start:02d}:00", "end": f"{(start + length) % 24:02d}:00"}
    cap = draw(st.one_of(st.none(), st.integers(1, 6)))
    spacing = draw(st.sampled_from([0, 300, 1800, 3600, 4 * 3600]))
    offset = draw(st.integers(0, 3600))
    return {
        "tz": tz,
        "proposal": proposal,
        "existing": existing,
        "quiet": quiet,
        "cap": cap,
        "spacing": spacing,
        "offset": offset,
    }


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(scenario())
def test_no_proposal_escapes_the_rules(sc: dict[str, object]) -> None:
    tz_name = str(sc["tz"])
    existing = list(sc["existing"])  # type: ignore[call-overload]
    try:
        p = place(
            requested=sc["proposal"],  # type: ignore[arg-type]
            offset_seconds=int(sc["offset"]),  # type: ignore[call-overload]
            existing=existing,
            tz_name=tz_name,
            quiet_hours=sc["quiet"],  # type: ignore[arg-type]
            daily_cap=sc["cap"],  # type: ignore[arg-type]
            min_spacing_seconds=int(sc["spacing"]),  # type: ignore[call-overload]
        )
    except PlacementImpossible:
        return  # refusing is always allowed; placing badly is not
    at = p.at
    tz = zone(tz_name)
    assert at >= sc["proposal"]  # type: ignore[operator]
    assert quiet_end_after(at, sc["quiet"], tz) is None, "inside quiet hours"  # type: ignore[arg-type]
    spacing = int(sc["spacing"])  # type: ignore[call-overload]
    assert all(abs((at - e).total_seconds()) >= spacing for e in existing), "too close"
    cap = sc["cap"]
    if cap is not None:
        day = at.astimezone(tz).date()
        assert sum(1 for e in existing if e.astimezone(tz).date() == day) < cap, "above cap"  # type: ignore[operator]


def _history(hours: list[tuple[int, int, float]]) -> list[tuple[datetime, float]]:
    """(weekday, hour, engagement) → (published_at, engagement) in UTC."""
    out = []
    for i, (wd, hr, e) in enumerate(hours):
        week = i // 7
        out.append((T0 + timedelta(days=wd + 7 * week, hours=hr), e))
    return out


def test_scores_shrink_small_samples_and_need_history() -> None:
    assert score_slots(_history([(1, 9, 50)] * 4), "UTC") == []  # too little history
    typical = [(0, 7, 10), (3, 14, 12), (4, 8, 11), (5, 10, 9), (6, 11, 10), (0, 18, 11)]
    hist = _history([(1, 9, 30), (1, 9, 30), (1, 9, 30), *typical, (2, 20, 600)])
    scores = score_slots(hist, "UTC")
    best = scores[0]
    # One viral Wednesday post does not outrank three strong Tuesdays.
    assert (best.weekday, best.hour, best.samples) == (1, 9, 3)
    single = next(x for x in scores if (x.weekday, x.hour) == (2, 20))
    assert single.score < best.score


def test_proposals_are_upcoming_better_than_typical_and_explained() -> None:
    hist = _history(
        [(1, 9, 90), (1, 9, 80), (1, 9, 100), (3, 14, 10), (4, 8, 12), (0, 7, 11), (5, 10, 9)]
    )
    scores = score_slots(hist, "UTC")
    after = datetime(2026, 11, 10, 10, tzinfo=UTC)  # Tuesday 10:00, just past the best slot
    got = propose(scores, after=after, tz_name="UTC")
    assert got
    assert got[0].at == datetime(2026, 11, 17, 9, tzinfo=UTC)  # next Tuesday 09:00
    assert "Tuesdays 09:00" in got[0].reason
    assert all(p.score > 1.0 and p.at > after for p in got)
    # Outside the horizon: nothing.
    assert propose(scores, after=after, tz_name="UTC", horizon=timedelta(days=2)) == []
    # Time zones: a Kolkata account's 09:00 is 03:30 UTC.
    k = propose(score_slots(hist, "Asia/Kolkata"), after=after, tz_name="Asia/Kolkata")
    assert k
    assert k[0].at.astimezone(zone("Asia/Kolkata")).time() in {time(h, 0) for h in range(24)}


def test_placement_needs_enough_posts_in_a_slot_and_waits_at_most_a_day() -> None:
    """P3-M0.7: two strong Tuesdays are luck, not evidence; eight are evidence."""
    from mera.application.scheduling import MAX_SHIFT, MIN_SLOT_SAMPLES

    typical = [(0, 7, 10), (3, 14, 12), (4, 8, 11), (5, 10, 9), (6, 11, 10), (0, 18, 11)]
    after = datetime(2026, 11, 9, 12, tzinfo=UTC)  # Monday 12:00, Tuesday 09:00 is 21 h away
    few = score_slots(_history([(1, 9, 90)] * 2 + typical), "UTC")
    assert propose(few, after=after, tz_name="UTC", min_samples=1)  # the old behaviour
    assert propose(few, after=after, tz_name="UTC", min_samples=MIN_SLOT_SAMPLES) == []
    many = score_slots(_history([(1, 9, 90)] * MIN_SLOT_SAMPLES + typical * 3), "UTC")
    got = propose(many, after=after, tz_name="UTC", horizon=MAX_SHIFT, min_samples=MIN_SLOT_SAMPLES)
    assert got
    assert got[0].samples >= MIN_SLOT_SAMPLES
    assert got[0].at - after <= MAX_SHIFT
    # The same evidence two days away is not worth the wait.
    later = datetime(2026, 11, 8, 12, tzinfo=UTC)  # Sunday: Tuesday 09:00 is 45 h away
    assert propose(many, after=later, tz_name="UTC", horizon=MAX_SHIFT, min_samples=8) == []
