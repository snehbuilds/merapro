"""Calling an A/B result honestly (P3-M2.3). Pure.

Each label is one post on one account; what is compared is its engagement rate
(likes + comments + shares over impressions). A winner is called only when both labels
have at least ``MIN_IMPRESSIONS`` impressions and a two-proportion z-test puts the
difference below ``ALPHA``; otherwise the result is "too early" or "no clear difference".
Where the platform does not report impressions there is no rate, and no winner.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

MIN_IMPRESSIONS: Final = 500
ALPHA: Final = 0.05


@dataclass(frozen=True, slots=True)
class Verdict:
    outcome: str  # too_early, no_difference, a, b, not_reported
    a_rate: float | None
    b_rate: float | None
    p_value: float | None


def _rate(eng: float, imp: float | None) -> float | None:
    return eng / imp if imp else None


def verdict(a_eng: float, a_imp: float | None, b_eng: float, b_imp: float | None) -> Verdict:
    a_rate, b_rate = _rate(a_eng, a_imp), _rate(b_eng, b_imp)
    if a_imp is None or b_imp is None:
        return Verdict("not_reported", a_rate, b_rate, None)
    if a_imp < MIN_IMPRESSIONS or b_imp < MIN_IMPRESSIONS:
        return Verdict("too_early", a_rate, b_rate, None)
    assert a_rate is not None
    assert b_rate is not None
    pooled = (a_eng + b_eng) / (a_imp + b_imp)
    se = math.sqrt(pooled * (1 - pooled) * (1 / a_imp + 1 / b_imp))
    if se == 0:
        return Verdict("no_difference", a_rate, b_rate, 1.0)
    z = (a_rate - b_rate) / se
    p = math.erfc(abs(z) / math.sqrt(2))
    if p >= ALPHA:
        return Verdict("no_difference", a_rate, b_rate, round(p, 4))
    return Verdict("a" if a_rate > b_rate else "b", a_rate, b_rate, round(p, 6))
