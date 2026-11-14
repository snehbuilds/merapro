"""Hashtag lift per account (P3-M1.1). Pure.

For one account: each post's engagement relative to the account's median post, capped so
one viral post counts as three typical ones and no more (as the Scheduler agent's slot
scores do), averaged over the posts that used the tag. A lift of 1.0 is typical.

The number is reported with its sample size and is not smoothed: consumers decide what is
evidence. Below ``MIN_HASHTAG_SAMPLES`` posts it is "not enough data" everywhere it is
shown or used, so a tag used twice on two lucky days never claims a lift.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median
from typing import Final

MAX_RATIO: Final = 3.0
MIN_HASHTAG_SAMPLES: Final = 10  # docs/14 Q12 (proposed)
MIN_ACCOUNT_POSTS: Final = 5  # fewer posts than this: no median worth comparing to


@dataclass(frozen=True, slots=True)
class TagStat:
    tag: str
    post_count: int
    avg_engagement: float
    lift: float

    @property
    def measured(self) -> bool:
        return self.post_count >= MIN_HASHTAG_SAMPLES


def tag_stats(posts: Iterable[tuple[frozenset[str], float]]) -> list[TagStat]:
    """``posts``: (normalised tags, engagement) per published post of one account.
    Highest lift first; empty when the account has too few posts to have a median."""
    rows = [(tags, max(0.0, e)) for tags, e in posts]
    if len(rows) < MIN_ACCOUNT_POSTS:
        return []
    base = median(e for _, e in rows) or 1.0
    ratios: dict[str, list[float]] = defaultdict(list)
    raw: dict[str, list[float]] = defaultdict(list)
    for tags, e in rows:
        for tag in tags:
            ratios[tag].append(min(MAX_RATIO, e / base))
            raw[tag].append(e)
    out = [
        TagStat(
            tag=tag,
            post_count=len(vals),
            avg_engagement=round(sum(raw[tag]) / len(raw[tag]), 4),
            lift=round(sum(vals) / len(vals), 4),
        )
        for tag, vals in ratios.items()
    ]
    return sorted(out, key=lambda t: (-t.lift, -t.post_count, t.tag))
