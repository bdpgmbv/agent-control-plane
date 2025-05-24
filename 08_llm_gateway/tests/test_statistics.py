"""
The arithmetic behind the only interesting question an A/B test asks.

Checked against values anybody can look up, because a statistics implementation
that is subtly wrong produces confident nonsense - which is worse than an
obvious crash, since somebody will act on it.
"""

from llm_gateway.layer8_experiments.step1_statistics import (
    compare_rates,
    describe,
    normal_cdf,
    normal_quantile,
    samples_needed,
)


def test_the_normal_distribution_matches_the_tables():
    assert abs(normal_cdf(0.0) - 0.5) < 1e-9
    assert abs(normal_cdf(1.0) - 0.8413447) < 1e-6
    assert abs(normal_cdf(1.96) - 0.9750021) < 1e-6
    assert abs(normal_cdf(-1.96) - 0.0249979) < 1e-6


def test_the_inverse_matches_too():
    assert abs(normal_quantile(0.975) - 1.959964) < 1e-4
    assert abs(normal_quantile(0.95) - 1.644854) < 1e-4
    assert abs(normal_quantile(0.80) - 0.8416212) < 1e-4
    assert abs(normal_quantile(0.5)) < 1e-6


def test_a_small_sample_cannot_settle_a_real_difference():
    # 60% against 70% with twenty each. The gap is real but the evidence is not.
    result = compare_rates(12, 20, 14, 20)
    assert not result.significant
    assert result.p_value > 0.05
    assert result.interval_low < 0 < result.interval_high
    assert result.samples_needed_per_arm > 100


def test_the_same_difference_at_a_large_sample_is_settled():
    result = compare_rates(360, 600, 420, 600)
    assert result.significant
    assert result.p_value < 0.05
    # Zero is outside the interval, which is the same statement said usefully.
    assert result.interval_low > 0


def test_identical_rates_are_never_a_winner():
    result = compare_rates(300, 600, 300, 600)
    assert not result.significant
    assert abs(result.difference) < 1e-9


def test_both_at_zero_or_both_at_one_is_not_a_comparison():
    assert not compare_rates(0, 50, 0, 50).significant
    assert not compare_rates(50, 50, 50, 50).significant


def test_an_empty_arm_is_reported_not_divided_by():
    result = compare_rates(0, 0, 5, 10)
    assert not result.significant
    assert "no graded samples" in result.note


def test_a_smaller_difference_needs_more_samples():
    # The relationship is roughly inverse-square, so halving the gap should
    # roughly quadruple what it takes to see it.
    big = samples_needed(0.50, 0.70)
    small = samples_needed(0.50, 0.60)
    assert small > big * 3


def test_no_difference_at_all_needs_no_estimate():
    # An infinite number. Saying so beats returning something that looks computed.
    assert samples_needed(0.5, 0.5) == 0


def test_the_wording_says_what_to_do_next():
    unsure = describe(compare_rates(12, 20, 14, 20), "A", "B")
    assert "Too close to call" in unsure
    assert "more samples" in unsure

    sure = describe(compare_rates(360, 600, 420, 600), "A", "B")
    assert "B is better" in sure
    assert "confident" in sure


def test_a_worse_challenger_is_reported_as_worse():
    result = compare_rates(420, 600, 360, 600)
    assert result.significant
    assert result.difference < 0
    assert "A is better" in describe(result, "A", "B")
