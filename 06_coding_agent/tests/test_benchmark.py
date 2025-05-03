"""The benchmark itself: do the seeded bugs work, and does the scoring."""

import pytest

from coding_agent.layer2_models.schemas import (
    AgentResult,
    Outcome,
    Verdict,
    VerdictReason,
)
from coding_agent.layer5_tests.step1_run import run_tests
from coding_agent.layer9_api.runner import run_task
from coding_agent.layer10_evaluation.step1_benchmark import run_benchmark, score
from coding_agent.layer10_evaluation.tasks import (
    BENCHMARK,
    BreakFailed,
    all_task_ids,
    apply_break,
    find,
)

TIMEOUT = 60.0


def test_every_task_has_what_it_needs():
    for entry in BENCHMARK:
        assert entry["task_id"] != ""
        assert len(entry["issue"]) > 40, entry["task_id"]
        assert len(entry["target_tests"]) > 0, entry["task_id"]
        assert len(entry["break"]) > 0, entry["task_id"]
        assert len(entry["plan"]) > 0, entry["task_id"]


def test_task_ids_are_unique():
    ids = all_task_ids()
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("task_id", all_task_ids())
def test_each_seeded_bug_turns_its_target_test_red(task_id, tmp_path):
    from tests.conftest import BENCHMARK_REPO

    from coding_agent.layer3_workspace.step1_sandbox import Workspace

    entry = find(task_id)
    workspace = Workspace.create_from(BENCHMARK_REPO, tmp_path, task_id)
    apply_break(workspace, entry)

    run = run_tests(workspace.root, "shopkit/tests", TIMEOUT)
    broken = set(run.failed) | set(run.errors)
    for wanted in entry["target_tests"]:
        assert wanted in broken, "%s did not break %s" % (task_id, wanted)

    workspace.remove()


def test_a_break_that_matches_nothing_is_loud_not_silent(workspace):
    # A silent break would leave the repository green, the agent with nothing to
    # do, and the benchmark reporting a solve it never earned.
    with pytest.raises(BreakFailed):
        apply_break(workspace, {
            "task_id": "made_up",
            "break": [("shopkit/money.py", "this text is not in the file", "x")],
        })


def test_a_break_that_matches_twice_is_refused(workspace):
    with pytest.raises(BreakFailed):
        apply_break(workspace, {
            "task_id": "made_up",
            "break": [("shopkit/pricing.py", "        return 0.0", "        return 1.0")],
        })


@pytest.mark.parametrize("task_id", all_task_ids())
def test_every_recorded_plan_fixes_its_task(task_id):
    # This is what the offline mode measures: given a correct patch, does the
    # harness apply it, test it, verify it and report it honestly.
    result = run_task(task_id, offline=True)
    assert result.outcome == Outcome.SOLVED, "%s: %s" % (task_id, result.summary)
    assert result.verdict.accepted
    assert not result.flagged
    assert result.final_run.is_green()
    assert len(result.files_changed) > 0
    assert result.diff != ""


def test_an_unknown_task_is_an_error_not_a_crash():
    result = run_task("no_such_task", offline=True)
    assert result.outcome == Outcome.ERROR
    assert "no benchmark task" in result.summary


def test_workspaces_are_cleaned_up_afterwards():
    from pathlib import Path

    result = run_task("tax_sign", offline=True)
    assert result.workspace == ""

    kept = run_task("tax_sign", offline=True, keep_workspace=True)
    assert kept.workspace != ""
    assert Path(kept.workspace).is_dir()
    import shutil
    shutil.rmtree(kept.workspace)


# ---------------------------------------------------------------- scoring

def fake_result(task_id, outcome, tests_unchanged=True, no_regressions=True,
                flagged=False) -> AgentResult:
    return AgentResult(
        task_id=task_id, outcome=outcome, flagged=flagged,
        verdict=Verdict(accepted=(outcome == Outcome.SOLVED), reasons=[
            VerdictReason(code="tests_unchanged", passed=tests_unchanged),
            VerdictReason(code="no_regressions", passed=no_regressions),
        ]),
    )


def test_scoring_counts_each_outcome():
    report = score([
        fake_result("a", Outcome.SOLVED),
        fake_result("b", Outcome.NOT_SOLVED),
        fake_result("c", Outcome.REJECTED),
        fake_result("d", Outcome.ERROR),
    ], offline=True, seconds=1.0)

    assert report.summary.tasks == 4
    assert report.summary.solved == 1
    assert report.summary.not_solved == 1
    assert report.summary.rejected == 1
    assert report.summary.errors == 1
    assert report.summary.resolve_rate() == 0.25


def test_the_cheat_detector_catches_a_verifier_that_let_one_through():
    # This checks the SAFETY CHECK, not the agent. The thing most likely to be
    # wrong in a safety system is the safety system, so scoring re-examines
    # every accepted patch independently of the verifier that accepted it.
    report = score([fake_result("a", Outcome.SOLVED, tests_unchanged=False)],
                   offline=True, seconds=1.0)

    assert report.summary.accepted_with_tampered_tests == 1
    assert not report.is_safe()
    assert report.rows[0].cheated


def test_a_regression_that_was_accepted_is_also_caught():
    report = score([fake_result("a", Outcome.SOLVED, no_regressions=False)],
                   offline=True, seconds=1.0)
    assert report.summary.accepted_with_regressions == 1
    assert not report.is_safe()


def test_a_clean_run_is_safe():
    report = score([fake_result("a", Outcome.SOLVED),
                    fake_result("b", Outcome.SOLVED)], offline=True, seconds=1.0)
    assert report.is_safe()
    assert report.summary.accepted_but_flagged == 0


def test_flagged_solves_are_counted_separately():
    report = score([fake_result("a", Outcome.SOLVED, flagged=True)],
                   offline=True, seconds=1.0)
    assert report.summary.solved == 1
    assert report.summary.accepted_but_flagged == 1
    assert report.is_safe(), "flagged is not the same as cheating"


def test_the_benchmark_runner_visits_every_task():
    seen = []

    def run_one(task_id, offline):
        seen.append(task_id)
        return fake_result(task_id, Outcome.SOLVED)

    report = run_benchmark(["a", "b", "c"], offline=True, run_one=run_one)
    assert seen == ["a", "b", "c"]
    assert report.summary.tasks == 3
