"""Assignment, results, and the refusal to name a winner too early."""

from llm_gateway.layer2_models.schemas import (
    ChatRequest,
    Experiment,
    Outcome,
    Trace,
    Variant,
)
from llm_gateway.layer3_storage.store import new_request_id
from llm_gateway.layer4_providers.step3_offline import OfflineProvider, solve
from llm_gateway.layer8_experiments.step2_experiments import (
    ExperimentRunner,
    assign,
)
from llm_gateway.layer10_evaluation.dataset import QUESTIONS, grade, question_at


def two_arms() -> Experiment:
    return Experiment(name="prompt-style", question="does working-out help?",
                      variants=[
                          Variant(name="direct", system="Answer with just the number.",
                                  weight=0.5),
                          Variant(name="stepwise", system="Work through it step by step.",
                                  weight=0.5)])


def test_an_even_split_is_roughly_even():
    experiment = two_arms()
    counts = {}
    for index in range(2000):
        name = assign(experiment, "unit-%d" % index).name
        counts[name] = counts.get(name, 0) + 1
    for count in counts.values():
        assert 900 < count < 1100


def test_a_weighted_split_is_respected():
    experiment = Experiment(name="risky", variants=[
        Variant(name="safe", weight=0.9), Variant(name="risky", weight=0.1)])
    risky = 0
    for index in range(2000):
        if assign(experiment, "unit-%d" % index).name == "risky":
            risky = risky + 1
    assert 150 < risky < 260


def test_the_same_unit_always_lands_in_the_same_arm():
    # When the unit is a user, this is what stops one person seeing variant A
    # on Monday and B on Tuesday.
    experiment = two_arms()
    first = assign(experiment, "user-42").name
    for _ in range(20):
        assert assign(experiment, "user-42").name == first


def test_an_inactive_experiment_assigns_nobody(store):
    experiment = two_arms()
    experiment.active = False
    store.save_experiment(experiment)
    assert ExperimentRunner(store).pick("prompt-style", "unit-1") is None


def record(store, variant, score, cost=0.0, seconds=0.01):
    store.record(Trace(request_id=new_request_id(), outcome=Outcome.OK,
                       experiment="prompt-style", variant=variant, score=score,
                       cost_usd=cost, seconds=seconds))


def test_it_refuses_to_look_below_the_minimum(store):
    store.save_experiment(two_arms())
    for _ in range(10):
        record(store, "direct", 0.0)
        record(store, "stepwise", 1.0)

    verdict = ExperimentRunner(store, minimum_samples_per_arm=30).results("prompt-style")
    assert verdict.winner == ""
    assert "Not enough graded samples" in verdict.verdict


def test_a_real_difference_at_scale_is_named(store):
    store.save_experiment(two_arms())
    for index in range(400):
        record(store, "direct", 1.0 if index % 100 < 45 else 0.0)
        record(store, "stepwise", 1.0 if index % 100 < 60 else 0.0)

    verdict = ExperimentRunner(store, minimum_samples_per_arm=30).results("prompt-style")
    assert verdict.winner == "stepwise"
    assert verdict.confident
    assert verdict.confidence_interval[0] > 0     # zero is outside the interval


def test_a_small_difference_at_small_scale_is_not_named(store):
    store.save_experiment(two_arms())
    for index in range(40):
        record(store, "direct", 1.0 if index % 10 < 6 else 0.0)
        record(store, "stepwise", 1.0 if index % 10 < 7 else 0.0)

    verdict = ExperimentRunner(store, minimum_samples_per_arm=30).results("prompt-style")
    assert verdict.winner == ""
    assert not verdict.confident
    assert "Too close to call" in verdict.verdict
    assert verdict.samples_needed_per_arm > 0


def test_ungraded_samples_are_counted_separately(store):
    store.save_experiment(two_arms())
    for _ in range(50):
        record(store, "direct", None)
        record(store, "stepwise", None)

    verdict = ExperimentRunner(store, minimum_samples_per_arm=30).results("prompt-style")
    for arm in verdict.arms:
        assert arm.samples == 50
        assert arm.scored == 0
    assert "Not enough graded samples" in verdict.verdict


def test_three_arms_are_refused_rather_than_mishandled(store):
    store.save_experiment(Experiment(name="prompt-style", variants=[
        Variant(name="a"), Variant(name="b"), Variant(name="c")]))
    verdict = ExperimentRunner(store).results("prompt-style")
    assert "exactly two" in verdict.verdict


def test_an_unknown_experiment_says_so(store):
    assert "no experiment" in ExperimentRunner(store).results("nope").verdict


def test_the_whole_thing_end_to_end(gateway):
    """The prompt really does change the success rate, measurably."""
    gateway.store.save_experiment(two_arms())

    for index in range(300):
        prompt, expected = question_at(index, 10 + index)
        response = gateway.handle(ChatRequest(
            prompt=prompt, experiment="prompt-style", no_cache=True), "demo")
        if response.outcome == Outcome.OK:
            gateway.store.set_score(response.request_id, grade(response.text, expected))

    verdict = gateway.experiments.results("prompt-style")
    rates = {}
    for arm in verdict.arms:
        rates[arm.variant] = arm.success_rate
    assert rates["stepwise"] > rates["direct"]


def test_grading_is_lenient_about_form_and_strict_about_value():
    assert grade("The answer is 36.", 36) == 1.0
    assert grade("Working through it...\nThe answer is 36.", 36) == 1.0
    assert grade("The answer is 35.", 36) == 0.0
    assert grade("I am not sure.", 36) == 0.0


# --------------------------------------------------------------------------
# The benchmark has to be scoreable, or every arm is measured against a
# ceiling nobody set on purpose.
# --------------------------------------------------------------------------

def test_the_solver_and_the_dataset_agree_on_every_question():
    """
    Every benchmark question must be answerable correctly by the offline solver.

    This is the test that was missing. One question's expected answer disagreed
    with the solver, so that question was graded wrong on EVERY request, for
    every model and every prompt variant. A fifth of the benchmark was dead and
    the only symptom was success rates a bit lower than they should have been -
    which reads as a result, not as a fault.

    A benchmark question that nothing can get right is not a hard question. It
    is a broken one, and it drags every arm down equally, so it hides itself.
    """
    for index in range(len(QUESTIONS)):
        prompt, expected = question_at(index, 20)
        solved = solve(prompt)
        assert solved is not None, (
            "question %d cannot be solved at all: %r" % (index + 1, prompt))
        assert solved == expected, (
            "question %d: the dataset expects %s but the solver answers %s.\n"
            "  %r\n"
            "One of the two is wrong, and until they agree this question is "
            "graded incorrect on every single request."
            % (index + 1, expected, solved, prompt))


def test_a_correct_answer_actually_scores_on_every_question():
    """
    Grade the dataset's own answer. It must score 1.0 every time.

    Separate from the test above because they fail for different reasons: that
    one catches arithmetic the solver disagrees with, this one catches a grader
    that cannot find the number - for instance if an answer were ever negative
    or formatted in a way the pattern misses.
    """
    for index in range(len(QUESTIONS)):
        prompt, expected = question_at(index, 20)
        assert grade("The answer is %d." % expected, expected) == 1.0, (
            "question %d: the correct answer does not grade as correct" % (index + 1))


def test_the_reasoning_prompt_really_does_help():
    """
    The A/B demo needs a real effect to find, so check the effect exists.

    Not a statistics test - an arithmetic one. If the offline provider ever
    stopped honouring the reasoning bonus, every experiment in this project
    would correctly report "no difference" and the demonstration would be
    measuring nothing while looking perfectly healthy.
    """
    provider = OfflineProvider()
    direct = 0
    stepwise = 0
    for index in range(200):
        prompt, expected = question_at(index, 10 + index)
        if grade(provider.answer("Answer with just the number.", prompt,
                                 "offline-fast"), expected) >= 1.0:
            direct = direct + 1
        if grade(provider.answer("Work through it step by step.", prompt,
                                 "offline-fast"), expected) >= 1.0:
            stepwise = stepwise + 1

    assert stepwise > direct + 20, (
        "asking for working-out should win by roughly 18 points over 200 "
        "questions, but direct scored %d and stepwise scored %d. Either the "
        "bonus is not being applied or the benchmark cannot score it."
        % (direct, stepwise))
