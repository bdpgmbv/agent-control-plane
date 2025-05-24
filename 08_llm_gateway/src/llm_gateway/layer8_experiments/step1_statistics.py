"""
LAYER 8, STEP 1 - IS THE DIFFERENCE REAL?
=========================================
The arithmetic behind the only interesting question an A/B test asks.

Variant A got 62% right and variant B got 71%. Is B better, or did B get a
slightly easier sample? With twenty requests each, "got a slightly easier
sample" is overwhelmingly the more likely explanation, and a platform that
prints "B wins" has told you nothing while sounding like it told you something.

WHAT IS COMPUTED HERE
---------------------
  a p-value       how often a difference at least this large would appear if
                  the two variants were genuinely identical
  an interval     the range the true difference plausibly lies in
  a sample size   how many more you would need to settle it

The interval is the useful one. A p-value collapses everything into "yes or no";
an interval of -2% to +19% says, legibly, "B might be quite a lot better, or
very slightly worse, and we cannot yet tell".

NO SCIPY
--------
`math.erf` gives the normal CDF exactly, and its inverse is found here by
bisection - forty iterations, accurate to about twelve decimal places, and
obviously correct to anybody reading it. A closed-form approximation would be
faster and would need the reader to trust a table of magic constants.

WHAT THIS DOES NOT DO
---------------------
It is a two-proportion z-test, which assumes the samples are independent and
that there are enough of them for the normal approximation to hold. It does not
correct for peeking - checking repeatedly and stopping when it looks good
inflates the false positive rate badly - and it does not handle more than two
arms. Both of those are real limits and they are stated in the README rather
than being quietly absent.
"""

import math
from dataclasses import dataclass


def normal_cdf(value: float) -> float:
    """The probability a standard normal is below `value`."""
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def normal_quantile(probability: float) -> float:
    """
    The z with `probability` of the distribution below it.

    By bisection. Slower than a closed form and impossible to get subtly wrong.
    """
    if probability <= 0.0:
        return -8.0
    if probability >= 1.0:
        return 8.0

    low = -8.0
    high = 8.0
    for _ in range(80):
        middle = (low + high) / 2.0
        if normal_cdf(middle) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


@dataclass
class Comparison:
    rate_a: float = 0.0
    rate_b: float = 0.0
    difference: float = 0.0
    p_value: float = 1.0
    interval_low: float = 0.0
    interval_high: float = 0.0
    significant: bool = False
    samples_needed_per_arm: int = 0
    note: str = ""


def compare_rates(successes_a: int, samples_a: int,
                  successes_b: int, samples_b: int,
                  confidence_level: float = 0.95) -> Comparison:
    """
    Compare two success rates. B is the challenger; A is what you have now.

    A positive difference means B did better.
    """
    result = Comparison()

    if samples_a <= 0 or samples_b <= 0:
        result.note = "one of the variants has no graded samples yet"
        return result

    rate_a = successes_a / samples_a
    rate_b = successes_b / samples_b
    result.rate_a = round(rate_a, 4)
    result.rate_b = round(rate_b, 4)
    result.difference = round(rate_b - rate_a, 4)

    # --- the test. Pooled, because the null hypothesis is that both arms
    # --- share one underlying rate.
    pooled = (successes_a + successes_b) / (samples_a + samples_b)
    pooled_error = math.sqrt(pooled * (1.0 - pooled) * (1.0 / samples_a + 1.0 / samples_b))

    if pooled_error == 0.0:
        # Both arms are 0% or both are 100%. No difference to test.
        result.p_value = 1.0
        result.note = "both variants scored identically, so there is nothing to compare"
        result.samples_needed_per_arm = 0
        return result

    z = (rate_b - rate_a) / pooled_error
    result.p_value = round(2.0 * (1.0 - normal_cdf(abs(z))), 6)

    # --- the interval. UNpooled, because here we are estimating the difference
    # --- rather than testing whether it is zero.
    spread = math.sqrt(rate_a * (1.0 - rate_a) / samples_a
                       + rate_b * (1.0 - rate_b) / samples_b)
    critical = normal_quantile(1.0 - (1.0 - confidence_level) / 2.0)
    result.interval_low = round((rate_b - rate_a) - critical * spread, 4)
    result.interval_high = round((rate_b - rate_a) + critical * spread, 4)

    result.significant = result.p_value < (1.0 - confidence_level)

    if not result.significant:
        result.samples_needed_per_arm = samples_needed(rate_a, rate_b, confidence_level)

    return result


def samples_needed(rate_a: float, rate_b: float, confidence_level: float = 0.95,
                   power: float = 0.8) -> int:
    """
    How many per arm would settle a difference this size.

    The standard formula, at 80% power - the conventional choice, meaning that
    if the difference really is this big you have a four-in-five chance of
    detecting it.

    It uses the rates OBSERVED SO FAR, so it is an estimate built on an estimate
    and should be read as an order of magnitude. "About four hundred" is the
    useful content; the exact figure is not.
    """
    difference = abs(rate_b - rate_a)
    if difference < 0.0001:
        # Distinguishing a difference of nothing takes an infinite number of
        # samples. Saying so beats returning a huge number that looks computed.
        return 0

    z_alpha = normal_quantile(1.0 - (1.0 - confidence_level) / 2.0)
    z_beta = normal_quantile(power)

    variance = rate_a * (1.0 - rate_a) + rate_b * (1.0 - rate_b)
    needed = ((z_alpha + z_beta) ** 2) * variance / (difference ** 2)
    return int(math.ceil(needed))


def describe(comparison: Comparison, name_a: str, name_b: str,
             confidence_level: float = 0.95) -> str:
    """The verdict, in a sentence somebody can act on."""
    if comparison.note != "" and comparison.rate_a == 0 and comparison.rate_b == 0:
        return comparison.note

    percent = comparison.difference * 100.0
    low = comparison.interval_low * 100.0
    high = comparison.interval_high * 100.0

    if comparison.significant:
        better = name_b if comparison.difference > 0 else name_a
        return ("%s is better. The difference is %.1f points (%.0f%% confident it "
                "is between %.1f and %.1f), which would happen by chance about "
                "%.2f%% of the time."
                % (better, abs(percent), confidence_level * 100,
                   low, high, comparison.p_value * 100))

    return ("Too close to call. %s is ahead by %.1f points, but the difference "
            "could plausibly be anywhere from %.1f to %.1f - which includes zero. "
            "About %d more samples per variant would settle it."
            % (name_b if comparison.difference >= 0 else name_a, abs(percent),
               low, high, comparison.samples_needed_per_arm))
