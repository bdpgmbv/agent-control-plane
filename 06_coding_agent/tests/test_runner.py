"""Running the test suite: parsing, timeouts, and leaving the workspace alone."""

import time

from coding_agent.layer2_models.schemas import TestOutcome
from coding_agent.layer5_tests.step1_run import (
    build_command,
    parse_output,
    run_tests,
    shorten_for_prompt,
)

TIMEOUT = 60.0


def test_the_benchmark_suite_is_green_to_begin_with(workspace):
    run = run_tests(workspace.root, "shopkit/tests", TIMEOUT)
    assert run.is_green()
    assert run.outcome == TestOutcome.PASSED
    assert len(run.passed) == 32


def test_a_seeded_bug_turns_the_named_test_red(broken_workspace):
    run = run_tests(broken_workspace.root, "shopkit/tests", TIMEOUT)
    assert not run.is_green()
    assert "shopkit/tests/test_pricing.py::test_tax_is_added_not_subtracted" in run.failed


def test_running_the_suite_changes_nothing(workspace):
    # If the run wrote caches into the workspace, every snapshot comparison in
    # layer 8 would be full of noise.
    before = workspace.snapshot()
    run_tests(workspace.root, "shopkit/tests", TIMEOUT)
    after = workspace.snapshot()
    assert after.changed_against(before) == ([], [], [])


def test_a_hanging_test_is_killed(workspace):
    workspace.write("shopkit/tests/test_hang.py",
                    "import time\n\n\ndef test_hangs():\n    time.sleep(120)\n")
    started = time.time()
    run = run_tests(workspace.root, "shopkit/tests/test_hang.py", timeout_seconds=3.0)
    elapsed = time.time() - started

    assert run.outcome == TestOutcome.TIMEOUT
    assert elapsed < 20, "the timeout did not actually stop it"


def test_a_file_that_does_not_import_is_an_error_not_a_pass(workspace):
    workspace.write("shopkit/tests/test_broken.py", "import does_not_exist\n")
    run = run_tests(workspace.root, "shopkit/tests", TIMEOUT)
    assert not run.is_green()
    assert run.outcome == TestOutcome.ERROR


def test_output_parsing():
    text = (
        "shopkit/tests/test_a.py::test_one PASSED   [ 33%]\n"
        "shopkit/tests/test_a.py::test_two FAILED   [ 66%]\n"
        "shopkit/tests/test_b.py::test_three ERROR  [100%]\n"
    )
    passed, failed, errors = parse_output(text)
    assert passed == ["shopkit/tests/test_a.py::test_one"]
    assert failed == ["shopkit/tests/test_a.py::test_two"]
    assert errors == ["shopkit/tests/test_b.py::test_three"]


def test_the_command_is_fixed_and_writes_nothing():
    command = build_command("shopkit/tests")
    assert "-p" in command and "no:cacheprovider" in command
    assert command[-1] == "shopkit/tests"


def test_failure_output_is_truncated_from_the_front(broken_workspace):
    # The END of pytest output holds the assertion and the values; the start is
    # a list of tests that passed. This is the opposite of how source files are
    # truncated, and for the same reason.
    run = run_tests(broken_workspace.root, "shopkit/tests", TIMEOUT)
    shortened = shorten_for_prompt(run, 300)
    assert len(shortened) < len(run.output)
    assert shortened.endswith(run.output[-100:])


def test_the_harness_own_pytest_config_does_not_leak_into_the_sandbox(workspace):
    """
    The project's own pytest.ini must have no effect on a sandboxed run.

    Workspaces are created inside this project, so pytest walked up from one and
    found the harness's config - the one for THESE tests - and applied its
    `addopts = -q`. That turned off the per-test output the parser reads, so
    every sandboxed run reported "0 passed, 0 failed" and every task was
    rejected for a target test that had in fact run and passed.

    This test fails if the pinned configuration is ever removed. Note that it
    passes trivially when run standalone and only has teeth inside a pytest run,
    which is exactly the situation that produced the bug.
    """
    from coding_agent.layer5_tests.step1_run import SANDBOX_CONFIG, build_command

    command = build_command("shopkit/tests", workspace.root)
    assert "-c" in command
    assert str(SANDBOX_CONFIG) in command
    assert SANDBOX_CONFIG.is_file()

    run = run_tests(workspace.root, "shopkit/tests", TIMEOUT)
    assert len(run.passed) == 32, "per-test output was not parsed: %s" % run.summary()


def test_the_sandbox_config_lives_outside_the_workspace(workspace):
    # If it were inside, the agent could edit it and change which tests run.
    from coding_agent.layer5_tests.step1_run import SANDBOX_CONFIG

    assert not workspace.contains(str(SANDBOX_CONFIG))
