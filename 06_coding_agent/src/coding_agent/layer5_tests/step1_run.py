"""
LAYER 5, STEP 1 - RUNNING THE SUITE
===================================
The objective signal the whole agent is built around.

A coding agent that decides for itself whether it has fixed something will say
yes. It is a fluent writer, and "I have corrected the calculation" is a fluent
sentence regardless of whether the calculation is correct. Running the tests
replaces that judgement with a fact, and the loop in layer 7 is organised so that
this fact, not the model's account of itself, decides what happens next.

Three details do real work:

  the command is fixed        the agent never chooses what to run. Letting it
                              would make "run the tests" an arbitrary shell
                              call, and the agent would eventually discover
                              that `pytest --co -q` exits zero
  the timeout kills the group a hanging test spawns children; killing the
                              parent leaves them running and the harness waits
                              forever on a pipe that never closes
  nothing is written          the bytecode and pytest caches are disabled, so
                              running the suite does not change the workspace.
                              Otherwise every snapshot comparison in layer 8
                              would be full of noise
"""

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from coding_agent.layer2_models.schemas import TestOutcome, TestRun

# "shopkit/tests/test_money.py::test_rounds_to_pennies PASSED   [  3%]"
RESULT_PATTERN = re.compile(
    r"^(?P<test>\S+::\S+)\s+(?P<status>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b"
)

# "ERROR shopkit/tests/test_x.py" - a collection failure, which has no test id.
COLLECTION_ERROR_PATTERN = re.compile(r"^(?:ERROR|ImportError while importing)\s+(?P<where>\S+)")


# The configuration the sandboxed run uses. Passing it explicitly is what stops
# pytest discovering a different one by walking up out of the workspace.
SANDBOX_CONFIG = Path(__file__).resolve().parent / "sandbox_pytest.ini"


def build_command(test_target: str | None,
                  workspace_root: str | Path | None = None) -> list[str]:
    """
    The one command this harness runs.

    `-c` pins the configuration, `--rootdir` pins where paths resolve from,
    `-p no:cacheprovider` and PYTHONDONTWRITEBYTECODE keep the run read-only,
    and `-v` is what makes the output parseable per test rather than per file.

    The `-c` is load-bearing. Without it pytest searches upwards from the
    working directory for an ini file, and since workspaces are created inside
    this project it found the harness's own pytest.ini and applied its
    `addopts = -q`. Every sandboxed run then reported zero tests, because the
    lines the parser reads had been turned off by a config file belonging to a
    completely different test suite.
    """
    command = [
        sys.executable, "-m", "pytest",
        "-c", str(SANDBOX_CONFIG),
        "-v",
        "--tb=short",
        "-p", "no:cacheprovider",
        "--no-header",
    ]
    if workspace_root is not None:
        command.extend(["--rootdir", str(workspace_root)])
    if test_target is not None and str(test_target).strip() != "":
        command.append(str(test_target))
    return command


def parse_output(text: str) -> tuple[list[str], list[str], list[str]]:
    """Returns (passed, failed, errors) as test ids."""
    passed = []
    failed = []
    errors = []

    for line in text.split("\n"):
        stripped = line.strip()

        match = RESULT_PATTERN.match(stripped)
        if match is not None:
            test_id = match.group("test")
            status = match.group("status")
            if status == "PASSED" or status == "XFAIL":
                passed.append(test_id)
            elif status == "FAILED" or status == "XPASS":
                failed.append(test_id)
            elif status == "ERROR":
                errors.append(test_id)
            continue

        collection = COLLECTION_ERROR_PATTERN.match(stripped)
        if collection is not None:
            where = collection.group("where")
            if where not in errors:
                errors.append(where)

    return passed, failed, errors


def kill_process_group(process) -> None:
    """
    Kill the whole group, then make sure.

    A test that starts a subprocess and hangs leaves a child holding the pipe
    open. Killing only the parent means communicate() never returns and the
    timeout that was supposed to save the run becomes the thing that hangs it.
    """
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            process.kill()
        except ProcessLookupError:
            pass


def run_tests(workspace_root: str | Path, test_target: str | None = None,
              timeout_seconds: float = 60.0,
              max_output_characters: int = 20000) -> TestRun:
    command = build_command(test_target, workspace_root)
    started = time.time()

    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    # The workspace root is what makes `import shopkit` resolve to the copy
    # rather than to anything installed.
    environment["PYTHONPATH"] = str(workspace_root)
    environment.pop("PYTEST_ADDOPTS", None)

    try:
        process = subprocess.Popen(
            command,
            cwd=str(workspace_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=environment,
            start_new_session=True,
        )
    except OSError as error:
        return TestRun(
            outcome=TestOutcome.ERROR,
            output="could not start the test runner: %s" % error,
            command=" ".join(command),
            seconds=0.0,
            return_code=-1,
        )

    try:
        output, _ = process.communicate(timeout=timeout_seconds)
        return_code = process.returncode
        timed_out = False
    except subprocess.TimeoutExpired:
        kill_process_group(process)
        try:
            output, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            output = ""
        return_code = -9
        timed_out = True

    seconds = round(time.time() - started, 3)

    if output is None:
        output = ""
    if len(output) > max_output_characters:
        half = max_output_characters // 2
        output = (output[:half]
                  + "\n\n... test output truncated in the middle ...\n\n"
                  + output[-half:])

    if timed_out:
        return TestRun(
            outcome=TestOutcome.TIMEOUT,
            output=output,
            seconds=seconds,
            command=" ".join(command),
            return_code=return_code,
        )

    passed, failed, errors = parse_output(output)

    if len(errors) > 0:
        outcome = TestOutcome.ERROR
    elif len(failed) > 0:
        outcome = TestOutcome.FAILED
    elif return_code == 0:
        outcome = TestOutcome.PASSED
    elif len(passed) > 0:
        # Non-zero exit with no parsed failure. Something went wrong that the
        # per-test lines do not explain, and pretending it passed would be worse
        # than saying so.
        outcome = TestOutcome.ERROR
    else:
        outcome = TestOutcome.ERROR

    return TestRun(
        outcome=outcome,
        passed=passed,
        failed=failed,
        errors=errors,
        output=output,
        seconds=seconds,
        command=" ".join(command),
        return_code=return_code,
    )


def shorten_for_prompt(run: TestRun, max_characters: int) -> str:
    """
    What the agent is shown about a failed run.

    The tail of pytest output is the useful part - the assertion, the values,
    the traceback - and the head is a list of tests that passed. So this keeps
    the END, which is the opposite of how source files are truncated in layer 4,
    and for the same reason: keep the part that carries the information.
    """
    text = run.output
    if len(text) <= max_characters:
        return text
    return ("... earlier output omitted ...\n\n" + text[-max_characters:])
