"""
LAYER 8, STEP 2 - RUNNING AN EXPERIMENT
=======================================
Split the traffic, collect the results, and refuse to name a winner until the
difference is bigger than the noise.

ASSIGNMENT IS A HASH, NOT A COIN
--------------------------------
Which variant a request gets comes from hashing a stable `unit` - the request
id by default, or something the caller supplies. Not `random.random()`, for two
reasons.

First, reproducibility: the same unit always lands in the same arm, so a run can
be repeated exactly and a surprising result can be investigated rather than
re-rolled.

Second, and more important: when the unit is a USER rather than a request,
hashing is what stops one person seeing variant A on Monday and variant B on
Tuesday. An experiment that reassigns the same person every request is not
measuring two variants; it is measuring an average of both, on everybody.

GRADING IS SEPARATE FROM SERVING
--------------------------------
The gateway records which variant answered and returns. A score arrives later,
from a human, a checker, or an offline grader - through `PATCH /v1/traces/{id}`.

Keeping them apart is what makes the platform usable for things that cannot be
graded instantly. "Did the customer reply?" is a perfectly good success metric
and the answer arrives a day later.
"""

import hashlib
from dataclasses import dataclass, field

from llm_gateway.layer2_models.schemas import (
    ArmResult,
    Experiment,
    ExperimentVerdict,
    Variant,
)
from llm_gateway.layer8_experiments.step1_statistics import compare_rates, describe


def unit_fraction(experiment_name: str, unit: str) -> float:
    """A stable number in [0, 1) for this unit in this experiment."""
    digest = hashlib.sha256(("%s|%s" % (experiment_name, unit)).encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(16 ** 12)


def assign(experiment: Experiment, unit: str) -> Variant | None:
    """
    Which arm this unit belongs to.

    Weights are cumulative, so [0.5, 0.5] splits evenly and [0.9, 0.1] sends a
    tenth of traffic to the challenger - which is how you try something risky
    without betting the whole service on it.
    """
    if len(experiment.variants) == 0:
        return None

    total_weight = 0.0
    for variant in experiment.variants:
        total_weight = total_weight + max(0.0, variant.weight)
    if total_weight <= 0:
        return experiment.variants[0]

    position = unit_fraction(experiment.name, unit) * total_weight
    running = 0.0
    for variant in experiment.variants:
        running = running + max(0.0, variant.weight)
        if position < running:
            return variant
    return experiment.variants[-1]


@dataclass
class ArmSamples:
    scores: list[float] = field(default_factory=list)
    costs: list[float] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)
    total: int = 0


def percentile(values: list[float], fraction: float) -> float:
    if len(values) == 0:
        return 0.0
    ordered = sorted(values)
    position = int(round((len(ordered) - 1) * fraction))
    return round(ordered[position], 4)


def mean(values: list[float]) -> float:
    if len(values) == 0:
        return 0.0
    total = 0.0
    for value in values:
        total = total + value
    return round(total / len(values), 6)


class ExperimentRunner:
    def __init__(self, store, confidence_level: float = 0.95,
                 minimum_samples_per_arm: int = 30) -> None:
        self.store = store
        self.confidence_level = confidence_level
        self.minimum_samples_per_arm = minimum_samples_per_arm

    def pick(self, name: str, unit: str) -> Variant | None:
        experiment = self.store.get_experiment(name)
        if experiment is None or not experiment.active:
            return None
        return assign(experiment, unit)

    def gather(self, name: str) -> dict:
        """Group the recorded traces by which arm answered them."""
        by_variant: dict = {}
        for row in self.store.experiment_samples(name):
            variant = row["variant"]
            if variant not in by_variant:
                by_variant[variant] = ArmSamples()
            samples = by_variant[variant]
            samples.total = samples.total + 1
            samples.costs.append(row["cost_usd"] or 0.0)
            samples.latencies.append(row["seconds"] or 0.0)
            if row["score"] is not None:
                samples.scores.append(float(row["score"]))
        return by_variant

    def results(self, name: str) -> ExperimentVerdict:
        experiment = self.store.get_experiment(name)
        if experiment is None:
            return ExperimentVerdict(experiment=name,
                                     verdict="there is no experiment with that name")

        by_variant = self.gather(name)
        arms: list[ArmResult] = []

        for variant in experiment.variants:
            samples = by_variant.get(variant.name, ArmSamples())
            successes = 0
            for score in samples.scores:
                if score >= 0.5:
                    successes = successes + 1

            rate = (successes / len(samples.scores)) if len(samples.scores) > 0 else 0.0
            arms.append(ArmResult(
                variant=variant.name,
                samples=samples.total,
                scored=len(samples.scores),
                successes=successes,
                success_rate=round(rate, 4),
                mean_cost_usd=mean(samples.costs),
                mean_seconds=mean(samples.latencies),
                p95_seconds=percentile(samples.latencies, 0.95),
            ))

        verdict = ExperimentVerdict(experiment=name, arms=arms)

        if len(arms) != 2:
            verdict.verdict = (
                "this platform compares exactly two variants. This experiment has "
                "%d, and comparing more than two needs a correction for multiple "
                "testing that is not implemented here." % len(arms))
            return verdict

        first, second = arms[0], arms[1]

        if first.scored < self.minimum_samples_per_arm or \
                second.scored < self.minimum_samples_per_arm:
            needed = self.minimum_samples_per_arm
            verdict.verdict = (
                "Not enough graded samples yet: %s has %d and %s has %d, and this "
                "platform will not look below %d per arm. A difference measured on "
                "a handful of samples is mostly noise."
                % (first.variant, first.scored, second.variant, second.scored, needed))
            verdict.samples_needed_per_arm = max(0, needed - min(first.scored, second.scored))
            return verdict

        comparison = compare_rates(first.successes, first.scored,
                                   second.successes, second.scored,
                                   self.confidence_level)

        verdict.difference = comparison.difference
        verdict.confidence_interval = [comparison.interval_low, comparison.interval_high]
        verdict.confident = comparison.significant
        verdict.samples_needed_per_arm = comparison.samples_needed_per_arm
        verdict.verdict = describe(comparison, first.variant, second.variant,
                                   self.confidence_level)

        if comparison.significant:
            verdict.winner = (second.variant if comparison.difference > 0
                              else first.variant)

        return verdict
