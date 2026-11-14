"""Does the Editor's confidence score predict approval? (P3-M1.6, docs/14 §2.4). Pure.

Assisted autopilot (P3-M3) publishes unattended above a confidence threshold, so the score
has to mean what 06 §3 says: the chance a post could go out as-is. This measures it on
history. Each agent-written version a person decided on is one outcome: approved as it
was, or not (rejected, expired, or replaced by an edit). Outcomes are banded by the
version's confidence, and the score "separates" when the top band is approved clearly
more often than the bottom one, each with enough decisions to say so.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

BANDS: Final = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0001))
MIN_PER_BAND: Final = 10  # decisions a band needs before its rate is shown
MIN_GAP: Final = 0.15  # top band approved this much more often than the bottom one


@dataclass(frozen=True, slots=True)
class Band:
    low: float
    high: float
    decided: int
    approved_unedited: int

    @property
    def rate(self) -> float | None:
        if self.decided < MIN_PER_BAND:
            return None
        return round(self.approved_unedited / self.decided, 4)


@dataclass(frozen=True, slots=True)
class Calibration:
    bands: tuple[Band, ...]
    decided: int
    # True: high confidence is approved clearly more often; False: it is not; None: too
    # few decisions at both ends to tell.
    separates: bool | None


def calibrate(outcomes: Iterable[tuple[float, bool]]) -> Calibration:
    """``outcomes``: (confidence 0..1, approved as written) per decided agent version."""
    counts = [[0, 0] for _ in BANDS]
    total = 0
    for confidence, approved in outcomes:
        c = min(max(confidence, 0.0), 1.0)
        for i, (lo, hi) in enumerate(BANDS):
            if lo <= c < hi:
                counts[i][0] += 1
                counts[i][1] += int(approved)
                total += 1
                break
    bands = tuple(
        Band(lo, min(hi, 1.0), n, a) for (lo, hi), (n, a) in zip(BANDS, counts, strict=True)
    )
    low = next((b for b in bands if b.rate is not None), None)
    high = next((b for b in reversed(bands) if b.rate is not None), None)
    separates: bool | None = None
    if low is not None and high is not None and low is not high:
        low_rate, high_rate = low.rate, high.rate
        if low_rate is not None and high_rate is not None:
            separates = high_rate - low_rate >= MIN_GAP
    return Calibration(bands, total, separates)
