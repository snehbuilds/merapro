"""The one settings-resolution function (docs/03 §2.2, ADR-0012).

    post override  →  social_account  →  account_group  →  workspace  →  system default

Most settings take the **most specific** value that is set. Four take the **strictest**
value set at *any* level, because permissiveness must never inherit downward:

- ``autopilot_mode``: ``manual`` < ``assisted`` < ``full`` (manual is strictest);
- ``required_approvers``: more is stricter;
- ``daily_post_cap``: fewer is stricter (product decision, Sprint 8: a group can tighten
  the workspace's cap, never loosen it);
- ``quiet_hours``: every level's window applies, so the result covers all of them (same
  decision; review S8-M3).

Every result carries the level it came from, so the UI can say "inherited from group Tech
News" and a user can see why a post is waiting.

The group level is the *targeting* group (``post_variant.via_account_group_id``), passed
in by the caller, never chosen here among an account's several groups.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final


class Level(StrEnum):
    POST = "post"
    ACCOUNT = "account"
    GROUP = "group"
    WORKSPACE = "workspace"
    SYSTEM = "system"


class Rule(StrEnum):
    MOST_SPECIFIC = "most_specific"
    STRICTEST = "strictest"


# Most specific first; strictest-rule ties are reported from the most specific level too.
ORDER: Final[tuple[Level, ...]] = (
    Level.POST,
    Level.ACCOUNT,
    Level.GROUP,
    Level.WORKSPACE,
    Level.SYSTEM,
)

_AUTOPILOT_STRICTNESS: Final[dict[str, int]] = {"manual": 0, "assisted": 1, "full": 2}


def quiet_minutes(window: Mapping[str, str]) -> int:
    """Length of a quiet-hours window in minutes; ``22:00``-``07:00`` wraps midnight."""

    def minutes(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)

    start, end = minutes(window["start"]), minutes(window["end"])
    return (end - start) % 1440


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _same_window(a: Mapping[str, str], b: Mapping[str, str]) -> bool:
    return a.get("start") == b.get("start") and a.get("end") == b.get("end")


def quiet_hull(windows: list[Mapping[str, str]]) -> dict[str, str]:
    """The shortest single window (on the 24-hour clock) that covers every window given:
    the complement of the longest stretch of the day no window covers."""

    def minutes(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)

    covered = [False] * 1440
    for w in windows:
        start, length = minutes(w["start"]), quiet_minutes(w)
        for i in range(length):
            covered[(start + i) % 1440] = True
    if all(covered):
        return {"start": "00:00", "end": "00:00", "all_day": "true"}
    if not any(covered):
        return dict(windows[0])
    # Longest circular run of uncovered minutes.
    best_start, best_len, run_start, run_len = 0, 0, None, 0
    for i in range(2880):
        if not covered[i % 1440]:
            if run_start is None:
                run_start, run_len = i, 0
            run_len += 1
            if run_len > best_len and run_len <= 1440:
                best_start, best_len = run_start, run_len
        else:
            run_start = None
    gap_start, gap_end = best_start % 1440, (best_start + best_len) % 1440
    return {"start": _hhmm(gap_end), "end": _hhmm(gap_start)}


@dataclass(frozen=True, slots=True)
class SettingSpec:
    key: str
    default: Any
    rule: Rule
    # For STRICTEST: smaller sort key = stricter.
    strictness: Callable[[Any], Any] | None = None


SPECS: Final[dict[str, SettingSpec]] = {
    s.key: s
    for s in (
        SettingSpec("brand_voice_id", None, Rule.MOST_SPECIFIC),
        SettingSpec("posting_times", None, Rule.MOST_SPECIFIC),
        SettingSpec("content_pillar_ids", None, Rule.MOST_SPECIFIC),
        SettingSpec("daily_post_cap", 5, Rule.STRICTEST, strictness=lambda v: int(v)),
        SettingSpec("quiet_hours", None, Rule.STRICTEST, strictness=lambda v: -quiet_minutes(v)),
        SettingSpec(
            "autopilot_mode",
            "manual",
            Rule.STRICTEST,
            strictness=lambda v: _AUTOPILOT_STRICTNESS[v],
        ),
        SettingSpec("required_approvers", 1, Rule.STRICTEST, strictness=lambda v: -int(v)),
    )
}


@dataclass(frozen=True, slots=True)
class Resolved:
    key: str
    value: Any
    source_level: Level
    rule: Rule


def resolve(key: str, levels: Mapping[Level, Mapping[str, Any] | None]) -> Resolved:
    """Resolve ``key`` from per-level settings. A level that is absent, or whose mapping
    lacks the key or holds ``None`` for it, is "unset" and contributes nothing."""
    spec = SPECS.get(key)
    if spec is None:
        raise KeyError(f"unknown setting {key!r}")
    present: list[tuple[Level, Any]] = []
    for level in ORDER[:-1]:
        values = levels.get(level)
        if values is not None and values.get(key) is not None:
            present.append((level, values[key]))
    if not present:
        return Resolved(key, spec.default, Level.SYSTEM, spec.rule)
    if spec.rule is Rule.MOST_SPECIFIC:
        level, value = present[0]
        return Resolved(key, value, level, spec.rule)
    if key == "quiet_hours":
        # Every level's quiet hours apply, so the effective window covers all of them; a
        # longer window elsewhere never shortens another level's (review S8-M3).
        hull = quiet_hull([v for _, v in present])
        source = next((lv for lv, v in present if _same_window(v, hull)), None)
        if source is None:
            source = max(present, key=lambda lv: quiet_minutes(lv[1]))[0]
        return Resolved(key, hull, source, spec.rule)
    assert spec.strictness is not None
    # min() keeps the first (most specific) of equally strict values.
    level, value = min(present, key=lambda lv: spec.strictness(lv[1]))  # type: ignore[misc]
    return Resolved(key, value, level, spec.rule)


def resolve_all(levels: Mapping[Level, Mapping[str, Any] | None]) -> list[Resolved]:
    return [resolve(key, levels) for key in SPECS]
