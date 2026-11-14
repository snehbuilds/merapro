"""The analytics loop's arithmetic (P3-M1.3 to 1.5, docs/14 §1.3-1.4). Pure.

**Relative engagement.** A post's engagement divided by its own account's median post,
capped at three times typical. Platforms and accounts of different sizes compare fairly:
a 1.4 means "40% better than this account usually does", on LinkedIn or on X alike.

**Aggregates** group those ratios by pillar, source, platform, account and group.

**Insights** are written from aggregates by rule, never invented: each one says which
aggregate it came from (``evidence``). A group with fewer than ``MIN_POSTS`` posts is never
the subject of an insight.

**Weights** for sources and pillars move toward their measured performance by at most
``MAX_STEP`` (15%) per run and stay within ``[WEIGHT_MIN, WEIGHT_MAX]``.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Final

MAX_RATIO: Final = 3.0
MIN_POSTS: Final = 10  # posts a pillar, source, platform, account or group needs
MIN_ACCOUNT_POSTS: Final = 5  # an account's own posts before it has a median
STRONG: Final = 1.2  # mean ratio at or above: "does well"
WEAK: Final = 0.8  # at or below: "underperforms"
COMPARISON_GAP: Final = 0.3  # best minus worst before a comparison is worth saying
MAX_STEP: Final = 0.15
WEIGHT_MIN: Final = 0.1
WEIGHT_MAX: Final = 4.0
SLOW_APPROVAL_MINUTES: Final = 6 * 60

DIMENSIONS: Final = ("pillar", "source", "platform", "account", "group")


@dataclass(frozen=True, slots=True)
class PostFact:
    account_id: uuid.UUID
    platform: str
    engagement: float
    group_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None
    pillar_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class Aggregate:
    dimension: str
    key: str
    posts: int
    mean_ratio: float
    median_engagement: float

    def evidence(self, period: tuple[str, str]) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "key": self.key,
            "posts": self.posts,
            "mean_ratio": self.mean_ratio,
            "median_engagement": self.median_engagement,
            "baseline": 1.0,
            "period": list(period),
        }


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str
    title: str
    body: str
    evidence: dict[str, Any]
    confidence: float
    subject: tuple[str, str] | None = None  # (dimension, key) the finding is about
    group_id: str | None = None
    account_id: str | None = None


@dataclass(slots=True)
class Names:
    """Display names for keys; unknown keys show as themselves."""

    pillar: dict[str, str] = field(default_factory=dict)
    source: dict[str, str] = field(default_factory=dict)
    account: dict[str, str] = field(default_factory=dict)
    group: dict[str, str] = field(default_factory=dict)

    def of(self, dimension: str, key: str) -> str:
        table: dict[str, str] = getattr(self, dimension, {}) if dimension != "platform" else {}
        return table.get(key, key)


def ratios(facts: Sequence[PostFact]) -> list[tuple[PostFact, float]]:
    """Each post's engagement over its account's median; accounts with too few posts to
    have a median are left out."""
    per: dict[uuid.UUID, list[float]] = defaultdict(list)
    for f in facts:
        per[f.account_id].append(max(0.0, f.engagement))
    medians = {a: (median(v) or 1.0) for a, v in per.items() if len(v) >= MIN_ACCOUNT_POSTS}
    return [
        (f, min(MAX_RATIO, max(0.0, f.engagement) / medians[f.account_id]))
        for f in facts
        if f.account_id in medians
    ]


def aggregate(facts: Sequence[PostFact]) -> list[Aggregate]:
    rated = ratios(facts)
    buckets: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for f, r in rated:
        keys = {
            "pillar": f.pillar_id,
            "source": f.source_id,
            "platform": f.platform,
            "account": f.account_id,
            "group": f.group_id,
        }
        for dim, key in keys.items():
            if key is not None:
                buckets[(dim, str(key))].append((r, f.engagement))
    out = [
        Aggregate(
            dim,
            key,
            len(vals),
            round(sum(r for r, _ in vals) / len(vals), 4),
            round(float(median(e for _, e in vals)), 4),
        )
        for (dim, key), vals in buckets.items()
    ]
    return sorted(out, key=lambda a: (DIMENSIONS.index(a.dimension), -a.mean_ratio, a.key))


def _confidence(posts: int) -> float:
    return round(min(1.0, posts / 30), 3)


def _pct(ratio: float) -> str:
    pct = round((ratio - 1) * 100)
    return f"{'+' if pct >= 0 else '-'}{abs(pct)}%"


_KIND = {"pillar": "topic", "source": "source", "platform": "platform"}
_LABEL = {"pillar": "Pillar", "source": "Source", "platform": "Platform"}


def findings(
    aggregates: Iterable[Aggregate],
    names: Names,
    period: tuple[str, str],
    approval_minutes: dict[str, list[float]] | None = None,
    account_platform: dict[str, str] | None = None,
) -> list[Finding]:
    aggs = [a for a in aggregates if a.posts >= MIN_POSTS]
    out: list[Finding] = []
    for a in aggs:
        if a.dimension not in _KIND:
            continue
        if a.mean_ratio >= STRONG or a.mean_ratio <= WEAK:
            good = a.mean_ratio >= STRONG
            name = names.of(a.dimension, a.key)
            out.append(
                Finding(
                    kind=_KIND[a.dimension],
                    title=(
                        f"{_LABEL[a.dimension]} {name} {'does well' if good else 'underperforms'}"
                    ),
                    body=(
                        f"Posts from {a.dimension} {name} averaged {_pct(a.mean_ratio)} against "
                        f"each account's typical post, over {a.posts} posts."
                    ),
                    evidence=a.evidence(period),
                    confidence=_confidence(a.posts),
                    subject=(a.dimension, a.key),
                )
            )
    # Groups: the posts made through a group against their accounts' typical posts.
    ranked = sorted((a for a in aggs if a.dimension == "group"), key=lambda a: -a.mean_ratio)
    if len(ranked) >= 2 and ranked[0].mean_ratio - ranked[-1].mean_ratio >= COMPARISON_GAP:
        best, worst = ranked[0], ranked[-1]
        out.append(
            Finding(
                kind="group_comparison",
                title=f"{names.of('group', best.key)} is ahead of {names.of('group', worst.key)}",
                body=(
                    f"Against their accounts' typical posts, posts through "
                    f"{names.of('group', best.key)} averaged {_pct(best.mean_ratio)} over "
                    f"{best.posts} posts and through {names.of('group', worst.key)} "
                    f"{_pct(worst.mean_ratio)} over {worst.posts} posts."
                ),
                evidence={
                    "dimension": "group",
                    "best": best.evidence(period),
                    "worst": worst.evidence(period),
                    "ranked": [{"key": a.key, "mean_ratio": a.mean_ratio} for a in ranked],
                },
                confidence=_confidence(min(best.posts, worst.posts)),
                group_id=best.key,
            )
        )
    # Accounts: each account is its own baseline, so its ratio says nothing against another
    # account. Accounts on the same platform are compared by median engagement instead.
    by_platform: dict[str, list[Aggregate]] = defaultdict(list)
    for a in aggs:
        if a.dimension == "account" and a.key in (account_platform or {}):
            by_platform[(account_platform or {})[a.key]].append(a)
    for platform, accts in sorted(by_platform.items()):
        accts.sort(key=lambda a: -a.median_engagement)
        if len(accts) < 2:
            continue
        best, worst = accts[0], accts[-1]
        if worst.median_engagement <= 0 or best.median_engagement / worst.median_engagement < 1.5:
            continue
        out.append(
            Finding(
                kind="account_comparison",
                title=(
                    f"{names.of('account', best.key)} draws more than "
                    f"{names.of('account', worst.key)} on {platform}"
                ),
                body=(
                    f"On {platform}, the median post on {names.of('account', best.key)} got "
                    f"{best.median_engagement:g} likes, comments and shares over {best.posts} "
                    f"posts; on {names.of('account', worst.key)}, "
                    f"{worst.median_engagement:g} over {worst.posts} posts."
                ),
                evidence={
                    "dimension": "account",
                    "platform": platform,
                    "best": best.evidence(period),
                    "worst": worst.evidence(period),
                },
                confidence=_confidence(min(best.posts, worst.posts)),
                account_id=best.key,
            )
        )
    for group_key, minutes in (approval_minutes or {}).items():
        if len(minutes) < MIN_POSTS:
            continue
        med = float(median(minutes))
        if med >= SLOW_APPROVAL_MINUTES:
            out.append(
                Finding(
                    kind="approval",
                    title=f"Approvals for {names.of('group', group_key)} are slow",
                    body=(
                        f"The median request for {names.of('group', group_key)} waited "
                        f"{med / 60:.1f} hours for its first decision, over {len(minutes)} "
                        "requests."
                    ),
                    evidence={
                        "dimension": "approval_turnaround",
                        "key": group_key,
                        "requests": len(minutes),
                        "median_minutes": round(med, 1),
                        "period": list(period),
                    },
                    confidence=_confidence(len(minutes)),
                    group_id=None if group_key == "workspace" else group_key,
                )
            )
    return out


def next_weight(current: float, mean_ratio: float) -> float:
    """Toward measured performance, at most ``MAX_STEP`` per run, within bounds."""
    factor = min(1 + MAX_STEP, max(1 - MAX_STEP, mean_ratio))
    return round(min(WEIGHT_MAX, max(WEIGHT_MIN, current * factor)), 4)
