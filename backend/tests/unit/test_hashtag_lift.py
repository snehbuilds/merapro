"""P3-M1.1 hashtag lift: the arithmetic and tag extraction, without a database."""

from __future__ import annotations

import pytest

from mera.application.agents.reviewers import SuggestedTag, TagEvidence
from mera.application.analytics.hashtag_stats import clean, tags_of
from mera.application.hashtags import apply_evidence
from mera.domain.policies.hashtag_lift import MIN_HASHTAG_SAMPLES, tag_stats
from mera.infrastructure.db.models import PostVersion


def test_lift_is_relative_to_the_account_median_and_capped() -> None:
    posts = [(frozenset({"postgres"}), 40.0)] * 12 + [(frozenset(), 10.0)] * 14
    posts += [(frozenset({"viral"}), 10_000.0)]
    stats = {s.tag: s for s in tag_stats(posts)}
    assert stats["postgres"].lift == pytest.approx(3.0)  # 4x typical, capped at 3
    assert stats["postgres"].post_count == 12
    assert stats["postgres"].measured
    assert stats["postgres"].avg_engagement == pytest.approx(40.0)
    # One viral post counts as three typical ones, no more, and is not evidence.
    assert stats["viral"].lift == pytest.approx(3.0)
    assert not stats["viral"].measured
    assert stats["viral"].post_count < MIN_HASHTAG_SAMPLES


def test_too_few_posts_have_no_median_to_compare_to() -> None:
    assert tag_stats([(frozenset({"a"}), 5.0)] * 4) == []


def test_negative_engagement_is_zero_and_a_zero_median_does_not_divide_by_zero() -> None:
    stats = tag_stats([(frozenset({"x"}), -3.0)] * 6)
    assert stats[0].lift == 0.0


def test_tags_come_from_the_version_and_the_text_with_aliases_folded() -> None:
    v = PostVersion(hashtags=["#Postgres", "AI", "two words"], body="Read this #ML now, #ai")
    assert tags_of(v, {"ml": "machinelearning"}) == {"postgres", "ai", "machinelearning"}
    assert clean(" #Rust ") == "rust"
    assert clean("") is None
    assert clean("a,b") is None


def _tag(tag: str, rank: int, basis: str = "content_only") -> SuggestedTag:
    return SuggestedTag(tag=tag, rank=rank, basis=basis)  # type: ignore[arg-type]


def test_numbers_and_order_are_meras_not_the_models() -> None:
    """P3-M1.2: the model picks tags; basis, samples, lift and rank come from hashtag_stat."""
    evidence = (
        TagEvidence("postgres", 14, 2.1),
        TagEvidence("databases", 12, 0.6),
        TagEvidence("sql", 3, None),  # used, but too few posts to be evidence
    )
    model = [
        _tag("sql", 1, basis="history"),  # claims history it does not have
        _tag("databases", 2),
        _tag("ai", 3, basis="history"),  # never measured at all
        _tag("postgres", 4),
    ]
    out = apply_evidence(model, evidence)
    assert [t.tag for t in out] == ["postgres", "sql", "ai", "databases"]
    assert [t.rank for t in out] == [1, 2, 3, 4]
    by = {t.tag: t for t in out}
    assert (by["postgres"].basis, by["postgres"].sample_size) == ("history", 14)
    assert by["postgres"].lift_vs_account_median == 2.1
    assert by["databases"].lift_vs_account_median == 0.6  # below typical: ranked last
    assert (by["sql"].basis, by["sql"].sample_size, by["sql"].lift_vs_account_median) == (
        "content_only",
        3,
        None,
    )
    assert (by["ai"].basis, by["ai"].sample_size) == ("content_only", 0)


def test_the_agent_is_shown_the_evidence_with_its_limits() -> None:
    from mera.application.agents.contracts import AccountSummary
    from mera.application.agents.reviewers import HashtagAgent, HashtagRequest
    from mera.application.drafting import core

    req = HashtagRequest(
        account=AccountSummary("Tech", "tech", "personal", "x", "X"),
        platform=core.platform_spec("x"),
        body="Postgres 18 ships async I/O.",
        topics=(),
        evidence=(TagEvidence("postgres", 14, 2.1), TagEvidence("sql", 3, None)),
    )
    system = HashtagAgent.messages(HashtagAgent.__new__(HashtagAgent), req)[0].content
    assert "#postgres: lift 2.10 over 14 posts" in system
    assert "#sql: used on 3 posts, too few to measure" in system
    empty = HashtagRequest(account=req.account, platform=req.platform, body=req.body, topics=())
    assert "none yet" in HashtagAgent.messages(HashtagAgent.__new__(HashtagAgent), empty)[0].content
