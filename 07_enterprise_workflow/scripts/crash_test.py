"""
Kill the worker at the worst possible moment, and check nothing happens twice.

    python scripts/crash_test.py

This is the claim the whole project rests on, so it is tested by actually doing
it to a real process. Two kinds of crash, because they fail differently:

  BOUNDARY   the worker dies cleanly between two steps. The in-flight step is
             finished and recorded; the next one has not started.

  MID-STEP   the worker dies inside a step, in the window between calling the
             outside system and writing down that it called it. The account
             exists. The database does not know. This is the case that
             separates a workflow engine from a for-loop, and the only defence
             is that the second call is harmless.

The mid-step kill is aimed, not hoped for: the step is told to pause in that
window (`delay_before_record_seconds`), the test waits for the step to be
RUNNING, and then sends SIGKILL. Without the pause the window is microseconds
wide and the kill lands somewhere harmless, which is how the first version of
this script reported eight successes while genuinely testing one.

After every crash, two things are checked:

  1. the run finished, and finished successfully
  2. every irreversible effect appears EXACTLY ONCE

The second is the one that matters. A workflow that completes twice has not
recovered - it has paid somebody two salaries.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from enterprise_workflow.layer3_database.store import WorkflowStore
from enterprise_workflow.layer5_steps.step3_onboarding import (
    ONBOARDING_STEPS,
    REGISTRY,
    WORKFLOWS,
)
from enterprise_workflow.layer6_engine.step1_engine import Engine, EngineSettings

REQUEST = """Hiring Ada Lovelace as a backend engineer.
Email ada.lovelace@example.com, starting 2026-11-03.
Salary £95,000. She needs a headset."""

LEASE_SECONDS = 2.0

# Steps that change the outside world, and therefore the only ones where a
# mid-step crash can duplicate anything.
SIDE_EFFECT_STEPS = ["create_account", "order_equipment",
                     "provision_licences", "enrol_payroll", "notify_manager"]


def start_worker(database: Path, worker_id: str, crash_after: int = 0) -> subprocess.Popen:
    environment = dict(os.environ)
    environment["LLM_PROVIDER"] = "offline"
    environment["OPENAI_API_KEY"] = ""
    environment["SQLITE_PATH"] = str(database)
    environment["LEASE_SECONDS"] = str(LEASE_SECONDS)
    environment["RETRY_BASE_SECONDS"] = "0.1"
    environment["POLL_SECONDS"] = "0.05"
    environment["PYTHONUNBUFFERED"] = "1"

    command = [sys.executable, str(PROJECT_ROOT / "scripts" / "worker.py"),
               "--database", str(database), "--worker-id", worker_id, "--offline"]
    if crash_after > 0:
        command.extend(["--crash-after", str(crash_after)])

    return subprocess.Popen(
        command, cwd=str(PROJECT_ROOT), env=environment,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)


def kill(process: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def steps_done(store: WorkflowStore, run_id: str) -> int:
    done = 0
    for step in store.get_steps(run_id):
        if step.state.value in ("succeeded", "skipped"):
            done = done + 1
    return done


def wait_until(check, timeout: float = 25.0, interval: float = 0.02) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(interval)
    return False


def wait_for_finish(store: WorkflowStore, run_id: str, timeout: float = 40.0) -> str:
    def finished():
        run = store.get_run(run_id)
        return run is not None and run.state.is_finished()

    wait_until(finished, timeout=timeout, interval=0.05)
    run = store.get_run(run_id)
    return run.state.value if run is not None else "gone"


def duplicate_effects(store: WorkflowStore, run_id: str) -> list[str]:
    seen: dict = {}
    for effect in store.list_side_effects(run_id):
        seen[effect.idempotency_key] = seen.get(effect.idempotency_key, 0) + 1
    duplicates = []
    for key, count in seen.items():
        if count > 1:
            duplicates.append("%s x%d" % (key, count))
    return duplicates


def make_run(database: Path, run_input: dict):
    if database.exists():
        database.unlink()
    for suffix in ("-wal", "-shm"):
        extra = Path(str(database) + suffix)
        if extra.exists():
            extra.unlink()

    store = WorkflowStore(str(database))
    engine = Engine(store, REGISTRY, WORKFLOWS,
                    EngineSettings(lease_seconds=LEASE_SECONDS), worker_id="setup")
    run = engine.start("employee_onboarding", run_input)
    return store, run


def boundary_case(kill_after: int, workspace: Path) -> dict:
    """Die cleanly between two steps, at a moment this test chooses."""
    database = workspace / ("boundary_%d.db" % kill_after)
    store, run = make_run(database, {
        "request_text": REQUEST, "today_override": "2026-09-25"})

    doomed = start_worker(database, "doomed", crash_after=kill_after)
    wait_until(lambda: doomed.poll() is not None, timeout=25.0)
    done_at_crash = steps_done(store, run.run_id)

    time.sleep(LEASE_SECONDS + 0.4)

    recovery = start_worker(database, "recovery")
    final_state = wait_for_finish(store, run.run_id)
    kill(recovery)

    outcome = {
        "label": "boundary, after %d step(s)" % kill_after,
        "crashed_at": done_at_crash,
        "really_crashed": doomed.poll() is not None and done_at_crash < len(ONBOARDING_STEPS),
        "final_state": final_state,
        "effects": len(store.list_side_effects(run.run_id)),
        "duplicates": duplicate_effects(store, run.run_id),
    }
    store.close()
    return outcome


def mid_step_case(target_step: str, workspace: Path) -> dict:
    """
    Die INSIDE a step, after the outside world changed and before we wrote it down.
    """
    database = workspace / ("midstep_%s.db" % target_step)
    store, run = make_run(database, {
        "request_text": REQUEST,
        "today_override": "2026-09-25",
        "delay_before_record_seconds": 1.5,
    })

    doomed = start_worker(database, "doomed")

    # Wait until the step we are aiming at is actually running, then let it get
    # into the gap before killing.
    def running_now():
        step = store.get_step(run.run_id, target_step)
        return step is not None and step.state.value == "running"

    reached = wait_until(running_now, timeout=30.0)
    time.sleep(0.7)                      # now we are inside the gap
    killed_while_running = running_now()
    kill(doomed)

    # Prove the kill landed where this test claims it did. In the gap, the
    # outside system has been called and nothing has been written down, so the
    # side effect must be ABSENT right now - and must appear exactly once after
    # recovery. Without this check the test would pass just as happily if every
    # kill had landed somewhere harmless, which is how the previous version of
    # this script reported eight successes while testing one.
    effects_at_crash = []
    for effect in store.list_side_effects(run.run_id):
        effects_at_crash.append(effect.step_name)
    landed_in_the_gap = target_step not in effects_at_crash

    time.sleep(LEASE_SECONDS + 0.4)

    recovery = start_worker(database, "recovery")
    final_state = wait_for_finish(store, run.run_id)
    kill(recovery)

    outcome = {
        "label": "mid-step, inside %s" % target_step,
        "crashed_at": -1,
        "really_crashed": reached and killed_while_running and landed_in_the_gap,
        "in_the_gap": landed_in_the_gap,
        "final_state": final_state,
        "effects": len(store.list_side_effects(run.run_id)),
        "duplicates": duplicate_effects(store, run.run_id),
    }
    store.close()
    return outcome


def main() -> int:
    workspace = PROJECT_ROOT / "data" / "crash_test"
    workspace.mkdir(parents=True, exist_ok=True)

    print("=" * 88)
    print("  KILLING A REAL WORKER PROCESS")
    print("=" * 88)
    print()
    print("  %-34s %-12s %-12s %-8s %s"
          % ("what was interrupted", "really?", "ended as", "effects", "duplicates"))
    print("  " + "-" * 84)

    outcomes = []
    for kill_after in range(1, len(ONBOARDING_STEPS)):
        outcomes.append(boundary_case(kill_after, workspace))
    for step_name in SIDE_EFFECT_STEPS:
        outcomes.append(mid_step_case(step_name, workspace))

    failures = []
    untested = []
    for outcome in outcomes:
        duplicate_text = ", ".join(outcome["duplicates"]) if outcome["duplicates"] else "none"
        really = "yes" if outcome["really_crashed"] else "NO - not tested"
        print("  %-34s %-12s %-12s %-8s %s"
              % (outcome["label"], really, outcome["final_state"],
                 outcome["effects"], duplicate_text))

        if not outcome["really_crashed"]:
            untested.append(outcome["label"])
        if outcome["final_state"] != "succeeded":
            failures.append("%s: ended as %s" % (outcome["label"], outcome["final_state"]))
        if len(outcome["duplicates"]) > 0:
            failures.append("%s: DUPLICATED %s"
                            % (outcome["label"], ", ".join(outcome["duplicates"])))

    print()
    print("=" * 88)
    if len(untested) > 0:
        print("  %d case(s) did not actually interrupt anything, so they tested nothing:"
              % len(untested))
        for line in untested:
            print("     " + line)
        print()
    if len(failures) == 0:
        print("  Every interrupted run recovered and completed. No effect happened twice.")
    else:
        for line in failures:
            print("  FAILED: " + line)
    print("=" * 88)

    return 1 if (len(failures) > 0 or len(untested) > 0) else 0


if __name__ == "__main__":
    sys.exit(main())
