"""P3-M1.6: does confidence predict approval? The banding and its honesty rules."""

from __future__ import annotations

from mera.domain.policies.calibration import MIN_PER_BAND, calibrate


def test_a_score_that_predicts_approval_separates() -> None:
    low = [(0.4, i < 3) for i in range(MIN_PER_BAND)]  # 30% approved as written
    high = [(0.95, i < 9) for i in range(MIN_PER_BAND)]  # 90%
    c = calibrate([*low, *high])
    assert c.decided == 2 * MIN_PER_BAND
    assert c.bands[0].rate == 0.3
    assert c.bands[-1].rate == 0.9
    assert c.separates is True


def test_a_score_that_does_not_predict_does_not_separate() -> None:
    flat = [(0.4, i < 7) for i in range(MIN_PER_BAND)] + [(0.95, i < 7) for i in range(10)]
    assert calibrate(flat).separates is False


def test_too_few_decisions_say_nothing() -> None:
    c = calibrate([(0.95, True)] * (MIN_PER_BAND - 1) + [(0.3, False)] * 50)
    assert c.bands[-1].rate is None  # nine decisions: no rate shown
    assert c.separates is None  # one measured band only
    assert calibrate([]).separates is None


def test_out_of_range_scores_are_clamped_into_the_end_bands() -> None:
    c = calibrate([(1.0, True), (1.7, True), (-0.2, False)])
    assert c.bands[-1].decided == 2
    assert c.bands[0].decided == 1
