"""P3-M2.3: an A/B result is called only on enough impressions and a clear difference."""

from __future__ import annotations

from mera.domain.policies.ab import MIN_IMPRESSIONS, verdict


def test_too_early_without_enough_impressions() -> None:
    assert verdict(50, MIN_IMPRESSIONS - 1, 10, 5000).outcome == "too_early"


def test_no_impressions_reported_means_no_winner() -> None:
    assert verdict(50, None, 10, 1000).outcome == "not_reported"


def test_a_clear_difference_names_the_winner() -> None:
    v = verdict(150, 2000, 60, 2000)  # 7.5% against 3%
    assert v.outcome == "a"
    assert v.p_value is not None
    assert v.p_value < 0.05
    assert verdict(60, 2000, 150, 2000).outcome == "b"


def test_a_small_difference_is_not_a_winner() -> None:
    assert verdict(61, 2000, 60, 2000).outcome == "no_difference"
