"""settings_resolution is a 100% area (docs/10 §6.2), property-tested over every combination
of levels (M3 acceptance)."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mera.domain.policies.settings_resolution import (
    ORDER,
    SPECS,
    Level,
    Rule,
    resolve,
    resolve_all,
)

LEVELS = [lv for lv in ORDER if lv is not Level.SYSTEM]
MODES = ["manual", "assisted", "full"]
STRICTNESS = {"manual": 0, "assisted": 1, "full": 2}


def test_unset_everywhere_falls_back_to_system_default() -> None:
    r = resolve("daily_post_cap", {})
    assert (r.value, r.source_level, r.rule) == (5, Level.SYSTEM, Rule.STRICTEST)
    assert resolve("autopilot_mode", {Level.ACCOUNT: None}).value == "manual"


def test_most_specific_wins_and_reports_its_level() -> None:
    r = resolve(
        "brand_voice_id",
        {Level.WORKSPACE: {"brand_voice_id": "ws"}, Level.GROUP: {"brand_voice_id": "grp"}},
    )
    assert (r.value, r.source_level) == ("grp", Level.GROUP)


def test_none_is_unset_not_a_value() -> None:
    r = resolve(
        "daily_post_cap",
        {Level.ACCOUNT: {"daily_post_cap": None}, Level.WORKSPACE: {"daily_post_cap": 3}},
    )
    assert (r.value, r.source_level) == (3, Level.WORKSPACE)


def test_adr_0012_table() -> None:
    # Account Manual inside a Full group stays Manual, and vice versa.
    r = resolve(
        "autopilot_mode",
        {Level.ACCOUNT: {"autopilot_mode": "manual"}, Level.GROUP: {"autopilot_mode": "full"}},
    )
    assert (r.value, r.source_level) == ("manual", Level.ACCOUNT)
    r = resolve(
        "autopilot_mode",
        {Level.ACCOUNT: {"autopilot_mode": "full"}, Level.GROUP: {"autopilot_mode": "manual"}},
    )
    assert (r.value, r.source_level) == ("manual", Level.GROUP)
    r = resolve(
        "required_approvers",
        {Level.ACCOUNT: {"required_approvers": 2}, Level.GROUP: {"required_approvers": 1}},
    )
    assert r.value == 2
    r = resolve("autopilot_mode", {Level.GROUP: {"autopilot_mode": "assisted"}})
    assert (r.value, r.source_level, r.rule) == ("assisted", Level.GROUP, Rule.STRICTEST)


def test_unknown_key() -> None:
    with pytest.raises(KeyError):
        resolve("nope", {})


def test_resolve_all_covers_every_spec() -> None:
    assert [r.key for r in resolve_all({})] == list(SPECS)


level_values = st.dictionaries(
    st.sampled_from(LEVELS),
    st.one_of(
        st.none(),
        st.fixed_dictionaries(
            {},
            optional={
                "autopilot_mode": st.none() | st.sampled_from(MODES),
                "daily_post_cap": st.none() | st.integers(1, 50),
                "required_approvers": st.none() | st.integers(1, 5),
            },
        ),
    ),
)


def _set_values(levels: dict[Level, Any], key: str) -> list[tuple[Level, Any]]:
    return [
        (lv, levels[lv][key])
        for lv in LEVELS
        if levels.get(lv) is not None and levels[lv].get(key) is not None
    ]


@given(level_values)
def test_daily_cap_is_the_lowest_set_anywhere(levels: dict[Level, Any]) -> None:
    # Sprint 8 decision: a group can tighten the workspace cap, never loosen it.
    r = resolve("daily_post_cap", levels)
    present = _set_values(levels, "daily_post_cap")
    if present:
        assert r.value == min(v for _, v in present)
        assert (r.source_level, r.value) in present
    else:
        assert r.source_level is Level.SYSTEM


def test_quiet_hours_cover_every_level() -> None:
    r = resolve(
        "quiet_hours",
        {
            Level.ACCOUNT: {"quiet_hours": {"start": "23:00", "end": "06:00"}},  # inside
            Level.WORKSPACE: {"quiet_hours": {"start": "21:00", "end": "08:00"}},
        },
    )
    assert ((r.value["start"], r.value["end"]), r.source_level) == (
        ("21:00", "08:00"),
        Level.WORKSPACE,
    )
    # A longer daytime window at a group must not open the workspace's night (S8-M3).
    r = resolve(
        "quiet_hours",
        {
            Level.GROUP: {"quiet_hours": {"start": "08:00", "end": "20:00"}},
            Level.WORKSPACE: {"quiet_hours": {"start": "22:00", "end": "07:00"}},
        },
    )
    # Only 07:00-08:00 and 20:00-22:00 are free; the covering window skips the longer gap.
    assert (r.value["start"], r.value["end"]) == ("22:00", "20:00")


@given(st.lists(st.tuples(st.integers(0, 1439), st.integers(1, 1439)), min_size=1, max_size=4))
def test_quiet_hull_covers_every_window(windows: list[tuple[int, int]]) -> None:
    from mera.domain.policies.settings_resolution import quiet_hull

    fmt = lambda m: f"{m // 60:02d}:{m % 60:02d}"  # noqa: E731
    ws = [{"start": fmt(s), "end": fmt((s + n) % 1440)} for s, n in windows]
    hull = quiet_hull(ws)
    if hull.get("all_day"):
        return
    hs = int(hull["start"][:2]) * 60 + int(hull["start"][3:])
    he = int(hull["end"][:2]) * 60 + int(hull["end"][3:])
    inside = {(hs + i) % 1440 for i in range((he - hs) % 1440)}
    for s, n in windows:
        assert all((s + i) % 1440 in inside for i in range(n))


@given(level_values)
def test_strictest_property_never_loosens(levels: dict[Level, Any]) -> None:
    r = resolve("autopilot_mode", levels)
    present = _set_values(levels, "autopilot_mode")
    # No level that is set may be stricter than the effective value...
    for _, v in present:
        assert STRICTNESS[r.value] <= STRICTNESS[v]
    # ...and the effective value really was set at the reported level.
    if present:
        assert r.value == levels[r.source_level]["autopilot_mode"]
    # Adding a stricter level anywhere can only tighten the result.
    tighter = {**levels, Level.POST: {"autopilot_mode": "manual"}}
    assert resolve("autopilot_mode", tighter).value == "manual"


@given(level_values)
def test_more_approvers_always_wins(levels: dict[Level, Any]) -> None:
    r = resolve("required_approvers", levels)
    present = [v for _, v in _set_values(levels, "required_approvers")]
    assert r.value == (max(present) if present else 1)
