"""P3-M1.3 to 1.5: aggregates, findings from rules, bounded weights, grounded summaries."""

from __future__ import annotations

import uuid

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mera.application.agents.analytics import AnalyticsSummary, Recommendation, grounded
from mera.domain.policies.insights import (
    MAX_STEP,
    MIN_POSTS,
    WEIGHT_MAX,
    WEIGHT_MIN,
    Names,
    PostFact,
    aggregate,
    findings,
    next_weight,
)

A, B = uuid.uuid4(), uuid.uuid4()
SRC_GOOD, SRC_BAD = uuid.uuid4(), uuid.uuid4()
PERIOD = ("2026-10-16", "2026-11-13")


def _facts() -> list[PostFact]:
    """Account A on LinkedIn: 12 posts from a strong source (30), 12 from a weak one (10).
    Account B on LinkedIn: smaller, 12 posts at 4."""
    out = [PostFact(A, "linkedin_member", 30.0, source_id=SRC_GOOD) for _ in range(12)]
    out += [PostFact(A, "linkedin_member", 10.0, source_id=SRC_BAD) for _ in range(12)]
    out += [PostFact(B, "linkedin_member", 4.0) for _ in range(12)]
    return out


def test_sources_are_judged_against_each_accounts_typical_post() -> None:
    aggs = {(a.dimension, a.key): a for a in aggregate(_facts())}
    good, bad = aggs[("source", str(SRC_GOOD))], aggs[("source", str(SRC_BAD))]
    assert good.posts == 12
    assert good.mean_ratio > 1.2  # 30 against A's median of 20
    assert bad.mean_ratio < 0.8
    names = Names(source={str(SRC_GOOD): "Postgres Weekly", str(SRC_BAD): "Generic Feed"})
    found = findings(
        aggregate(_facts()),
        names,
        PERIOD,
        account_platform={str(A): "linkedin_member", str(B): "linkedin_member"},
    )
    titles = {f.title for f in found}
    assert "Source Postgres Weekly does well" in titles
    assert "Source Generic Feed underperforms" in titles
    for f in found:
        assert f.evidence  # every finding carries the numbers it came from
    acct = next(f for f in found if f.kind == "account_comparison")
    assert acct.evidence["best"]["median_engagement"] == 20.0
    assert acct.evidence["worst"]["median_engagement"] == 4.0


def test_too_few_posts_are_never_a_finding() -> None:
    few = [PostFact(A, "x", 50.0, source_id=SRC_GOOD)] * (MIN_POSTS - 1)
    few += [PostFact(A, "x", 5.0)] * 20
    assert not [f for f in findings(aggregate(few), Names(), PERIOD) if f.kind == "source"]


def test_slow_approvals_are_reported_with_their_count() -> None:
    found = findings([], Names(group={"g1": "Company"}), PERIOD, {"g1": [600.0] * MIN_POSTS})
    assert [f.kind for f in found] == ["approval"]
    assert "10.0 hours" in found[0].body


@given(st.floats(0.05, 4.0), st.floats(0.0, 3.0))
def test_a_weight_moves_at_most_fifteen_percent_and_stays_in_bounds(w: float, ratio: float) -> None:
    after = next_weight(w, ratio)
    assert WEIGHT_MIN <= after <= WEIGHT_MAX
    if w * (1 - MAX_STEP) >= WEIGHT_MIN and w * (1 + MAX_STEP) <= WEIGHT_MAX:
        assert abs(after - w) <= w * MAX_STEP + 1e-3


def test_the_summary_may_cite_only_what_it_was_given() -> None:
    out = AnalyticsSummary(
        summary="Good week.",
        cites=["a", "invented"],
        recommendations=[
            Recommendation(text="Do more of a.", cites=["a"]),
            Recommendation(text="Made up.", cites=["nope"]),
        ],
    )
    g = grounded(out, {"a"})
    assert g.cites == ["a"]
    assert [r.text for r in g.recommendations] == ["Do more of a."]


@pytest.mark.parametrize("ratio", [0.0, 3.0])
def test_extremes_move_by_exactly_the_step(ratio: float) -> None:
    assert next_weight(1.0, ratio) == pytest.approx(1.0 + (MAX_STEP if ratio > 1 else -MAX_STEP))
