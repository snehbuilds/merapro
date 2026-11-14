"""M4 domain policies: URL canonicalisation, keyword filter, relevance arithmetic."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mera.domain.policies import keyword_filter, relevance
from mera.domain.policies.url_canon import InvalidUrl, canonicalize, url_hash

# ---------------------------------------------------------------- URL canonicalisation


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (
            "https://Example.COM/post?utm_source=x&id=7&fbclid=abc#frag",
            "https://example.com/post?id=7",
        ),
        ("http://example.com:80/a", "http://example.com/a"),
        ("https://example.com:443/a?b=2&a=1", "https://example.com/a?a=1&b=2"),
        ("https://example.com", "https://example.com/"),
        ("https://example.com./x?ref=hn&mc_cid=1&gclid=2", "https://example.com/x"),
        ("https://example.com/a%20b", "https://example.com/a%20b"),
        ("https://example.com/a b", "https://example.com/a%20b"),
    ],
)
def test_canonical_forms_collapse(a: str, b: str) -> None:
    assert canonicalize(a) == b
    assert url_hash(canonicalize(a)) == url_hash(b)


def test_canonical_keeps_meaningful_parts() -> None:
    assert canonicalize("https://example.com:8443/Path/") == "https://example.com:8443/Path/"
    assert canonicalize("https://[2001:db8::1]/x") == "https://[2001:db8::1]/x"
    assert canonicalize("https://example.com/?q=") == "https://example.com/?q="


@pytest.mark.parametrize(
    "bad", ["ftp://example.com/x", "javascript:alert(1)", "https:///nohost", "https://e.com:99999/"]
)
def test_canonical_rejects(bad: str) -> None:
    with pytest.raises(InvalidUrl):
        canonicalize(bad)


@given(
    st.from_regex(
        r"https://[a-z]{3,10}\.com/[a-z0-9]{0,8}(\?[a-z]{1,4}=[a-z0-9]{0,4})?", fullmatch=True
    )
)
def test_canonicalize_is_idempotent(url: str) -> None:
    once = canonicalize(url)
    assert canonicalize(once) == once


# ---------------------------------------------------------------------- keyword filter


def test_keyword_filter_word_boundaries_and_phrases() -> None:
    text = "Google announces a new Postgres extension for open  source vector search"
    assert keyword_filter.apply(text, include=["go"], exclude=[]).reason == "no_include_match"
    assert keyword_filter.apply(text, include=["postgres"], exclude=[]).admitted
    assert keyword_filter.apply(text, include=["open source"], exclude=[]).admitted
    assert keyword_filter.apply(text, include=[], exclude=[]).admitted
    r = keyword_filter.apply(text, include=["postgres"], exclude=["google"])
    assert (r.admitted, r.reason) == (False, "excluded:google")
    assert keyword_filter.apply("C++ and c# news", include=["c++"], exclude=[]).admitted


def test_keyword_filter_language() -> None:
    r = keyword_filter.apply("x", include=[], exclude=[], language="en", detected_language="de")
    assert (r.admitted, r.reason) == (False, "language:de")
    assert keyword_filter.apply(
        "x", include=[], exclude=[], language="en", detected_language=None
    ).admitted


def test_normalize_keywords() -> None:
    assert keyword_filter.normalize_keywords(["  Postgres ", "postgres", "", "Open   Source"]) == [
        "postgres",
        "open source",
    ]


# -------------------------------------------------------------------------- relevance


def _unit(*xs: float) -> list[float]:
    n = math.sqrt(sum(x * x for x in xs))
    return [x / n for x in xs]


def test_cosine_basics() -> None:
    assert relevance.cosine([1, 0], [1, 0]) == pytest.approx(1)
    assert relevance.cosine([1, 0], [0, 1]) == pytest.approx(0)
    assert relevance.cosine([0, 0], [1, 0]) == 0
    with pytest.raises(ValueError, match="length"):
        relevance.cosine([1], [1, 2])


def test_freshness_half_life() -> None:
    now = datetime(2026, 11, 8, 12, tzinfo=UTC)
    assert relevance.freshness(None, now) == 1.0
    assert relevance.freshness(now + timedelta(hours=1), now) == 1.0
    assert relevance.freshness(now - timedelta(hours=24), now) == pytest.approx(0.5)
    assert relevance.freshness(now - timedelta(hours=48), now) == pytest.approx(0.25)
    with pytest.raises(ValueError, match="positive"):
        relevance.freshness(now - timedelta(hours=1), now, half_life_hours=0)


def test_score_is_explainable() -> None:
    pillars = [
        relevance.PillarVector("db", 1.4, _unit(1, 0, 0)),
        relevance.PillarVector("ops", 1.0, _unit(0, 1, 0)),
    ]
    s = relevance.score(_unit(0.2, 1, 0), pillars, source_weight=1.0, freshness_value=1.0)
    # The ops pillar matches better, but is discounted by its lower weight.
    assert s.pillar_scores["ops"] > s.pillar_scores["db"]
    assert s.best_pillar_id == "ops"
    assert s.pillar_match == pytest.approx(s.pillar_scores["ops"] * 1.0 / 1.4, abs=1e-4)
    assert s.relevance == pytest.approx(s.pillar_match, abs=1e-4)
    # Stale halves at most; source weight scales by its square root.
    stale = relevance.score(_unit(0.2, 1, 0), pillars, source_weight=1.0, freshness_value=0.0)
    assert stale.relevance == pytest.approx(s.relevance / 2, abs=1e-4)
    heavy = relevance.score(_unit(0.2, 1, 0), pillars, source_weight=4.0, freshness_value=0.0)
    assert heavy.relevance == pytest.approx(min(s.relevance, 1.0), abs=1e-3)


def test_score_edges() -> None:
    assert relevance.score([1, 0], [], source_weight=1, freshness_value=1).relevance == 0
    zero_w = [relevance.PillarVector("p", 0.0, [1, 0])]
    s = relevance.score([1, 0], zero_w, source_weight=1, freshness_value=1)
    assert (s.relevance, s.best_pillar_id) == (0, None)
    opposite = [relevance.PillarVector("p", 1.0, [-1, 0])]
    assert relevance.score([1, 0], opposite, source_weight=1, freshness_value=1).pillar_scores == {
        "p": 0.0
    }
    assert relevance.workspace_max({}) is None
    assert relevance.workspace_max({"a": 0.2, "b": 0.7}) == 0.7


vec = st.lists(st.floats(-1, 1, allow_nan=False), min_size=3, max_size=3).filter(
    lambda v: any(abs(x) > 1e-3 for x in v)
)


@given(
    item=vec,
    pillars=st.lists(st.tuples(vec, st.floats(0, 4)), min_size=0, max_size=4),
    sw=st.floats(-1, 10, allow_nan=False),
    fr=st.floats(-1, 2, allow_nan=False),
)
def test_relevance_bounded_and_deterministic(
    item: list[float], pillars: list[tuple[list[float], float]], sw: float, fr: float
) -> None:
    pvs = [relevance.PillarVector(str(i), w, v) for i, (v, w) in enumerate(pillars)]
    a = relevance.score(item, pvs, source_weight=sw, freshness_value=fr)
    b = relevance.score(item, pvs, source_weight=sw, freshness_value=fr)
    assert a == b
    assert 0.0 <= a.relevance <= 1.0
    assert all(0.0 <= v <= 1.0 for v in a.pillar_scores.values())
