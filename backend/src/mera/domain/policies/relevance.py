"""Per-group relevance scoring (M4.8, docs/03 §5 "Relevance score is computed, not generated").

Arithmetic, not a model judgement, so the same input always produces the same score and
the parts can be shown to explain it:

    pillar_match = max over pillars p of  max(0, cos(item, p)) * weight_p / max_weight
    freshness    = 0.5 ** (age_hours / half_life_hours)          (1.0 when age <= 0)
    relevance    = clamp(pillar_match * sqrt(source_weight) * (0.5 + 0.5 * freshness), 0, 1)

The best pillar decides the match (an article need not touch every theme), weights are
relative within the group, a source weight of 1.0 is neutral, and freshness can at most
halve a score: a strong match from yesterday still beats a weak one from this hour.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

DEFAULT_HALF_LIFE_HOURS: Final = 24.0
MAX_SOURCE_WEIGHT: Final = 4.0


@dataclass(frozen=True, slots=True)
class PillarVector:
    id: str
    weight: float
    embedding: Sequence[float]


@dataclass(frozen=True, slots=True)
class Score:
    relevance: float
    pillar_match: float
    freshness: float
    source_weight: float
    pillar_scores: dict[str, float]  # pillar id -> clamped cosine similarity
    best_pillar_id: str | None


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError("vectors differ in length")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def freshness(
    published_at: datetime | None,
    at: datetime,
    *,
    half_life_hours: float = DEFAULT_HALF_LIFE_HOURS,
) -> float:
    """Exponential decay from publication. An undated item counts as just published at the
    moment it was found, which is the caller's ``at``-relative choice to make."""
    if published_at is None:
        return 1.0
    if half_life_hours <= 0:
        raise ValueError("half_life_hours must be positive")
    age_hours = (at - published_at).total_seconds() / 3600
    if age_hours <= 0:
        return 1.0
    return float(0.5 ** (age_hours / half_life_hours))


def score(
    item_embedding: Sequence[float],
    pillars: Sequence[PillarVector],
    *,
    source_weight: float,
    freshness_value: float,
) -> Score:
    return combine(
        {p.id: cosine(item_embedding, p.embedding) for p in pillars},
        {p.id: p.weight for p in pillars},
        source_weight=source_weight,
        freshness_value=freshness_value,
    )


def combine(
    similarities: Mapping[str, float],
    weights: Mapping[str, float],
    *,
    source_weight: float,
    freshness_value: float,
) -> Score:
    """The formula over precomputed cosine similarities (callers may batch the vector maths)."""
    sw = min(max(source_weight, 0.0), MAX_SOURCE_WEIGHT)
    fr = min(max(freshness_value, 0.0), 1.0)
    sims = {pid: round(min(max(float(v), 0.0), 1.0), 4) for pid, v in similarities.items()}
    positive = [pid for pid in sims if weights.get(pid, 0.0) > 0]
    max_w = max((weights[pid] for pid in positive), default=0.0)
    best_id: str | None = None
    match = 0.0
    for pid in positive:
        m = sims[pid] * weights[pid] / max_w
        if m > match:
            match, best_id = m, pid
    relevance = min(max(match * math.sqrt(sw) * (0.5 + 0.5 * fr), 0.0), 1.0)
    return Score(
        relevance=round(relevance, 4),
        pillar_match=round(match, 4),
        freshness=round(fr, 4),
        source_weight=sw,
        pillar_scores=sims,
        best_pillar_id=best_id,
    )


def workspace_max(scores: Mapping[str, float]) -> float | None:
    """``content_item.relevance_score`` is the maximum across the groups it was scored for."""
    return max(scores.values(), default=None)
