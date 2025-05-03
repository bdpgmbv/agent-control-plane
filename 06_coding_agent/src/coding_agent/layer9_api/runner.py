"""
LAYER 9 - RUNNING ONE TASK
==========================
Every layer in one function, in order:

    copy the repo -> break it -> snapshot -> baseline tests -> agent loop
                  -> snapshot -> verify -> diff -> report

Reading `run_task` top to bottom is the fastest way to understand this project.

Two details are easy to get wrong and both were:

  the "before" snapshot is taken AFTER the bug is introduced. The agent inherits
  a broken repository, and that broken state is what its work is measured
  against. Snapshotting the pristine repo would make the seeded bug itself look
  like the agent's change, and every diff would contain the bug being removed as
  though the agent had put it there.

  the verdict is computed from the snapshots and the test runs, never from
  anything the agent said about itself. The agent's own account lives in
  `steps[].thinking` where a person can read it, and it has no influence on
  whether the patch is accepted.
"""

import difflib
import time

from coding_agent.layer0_shared.model_client import build_chat_client
from coding_agent.layer0_shared.usage import Budget
from coding_agent.layer1_config.settings import SETTINGS
from coding_agent.layer2_models.schemas import AgentResult, Outcome
from coding_agent.layer3_workspace.step1_sandbox import Workspace
from coding_agent.layer7_agent.step2_recorded import RecordedClient
from coding_agent.layer7_agent.step3_loop import LoopSettings, run_agent
from coding_agent.layer8_verify.step1_verify import verify
from coding_agent.layer10_evaluation.tasks import apply_break, find, to_task


def has_shortcut_warning(verdict) -> bool:
    for reason in verdict.reasons:
        if reason.code == "no_shortcuts" and not reason.passed:
            return True
    return False

TEST_TARGET = "shopkit/tests"


def build_loop_settings() -> LoopSettings:
    return LoopSettings(
        max_files_in_context=SETTINGS.max_files_in_context,
        max_file_characters=SETTINGS.max_file_characters,
        max_test_output_characters=SETTINGS.max_test_output_characters,
        test_timeout_seconds=SETTINGS.test_timeout_seconds,
        max_edit_file_bytes=SETTINGS.max_edit_file_bytes,
        protected_globs=SETTINGS.protected_globs,
        test_target=TEST_TARGET,
    )


def read_all_source(workspace) -> dict:
    """
    A copy of every source file before the agent starts, for the diff.

    Holding the whole repository in memory is fine at this size and would not be
    on a large one. The honest alternative there is a second directory copy, or
    git - which is what a real harness would use and what this deliberately does
    not, so that the mechanism stays visible.
    """
    contents = {}
    for name in workspace.source_files():
        try:
            contents[name] = workspace.read(name, SETTINGS.max_edit_file_bytes)
        except Exception:
            continue
    return contents


def build_diff(before_contents: dict, workspace, changed_paths: list[str]) -> str:
    pieces = []
    for path in changed_paths:
        old_text = before_contents.get(path, "")
        try:
            new_text = workspace.read(path)
        except Exception:
            new_text = ""

        lines = difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile="a/" + path,
            tofile="b/" + path,
            n=3,
        )
        for line in lines:
            pieces.append(line.rstrip("\n"))

    return "\n".join(pieces)


def build_client(offline: bool, recorded_plan: list[str] | None):
    if offline:
        return RecordedClient(recorded_plan or [], tokens_per_reply=(0, 0))
    return build_chat_client()


def run_task(task_id: str, offline: bool | None = None,
             keep_workspace: bool = False,
             plan_override: list[str] | None = None) -> AgentResult:
    """
    `plan_override` replaces the task's recorded plan with a different one, and
    exists so that scripts/try_to_break_it.py can drive a deliberately dishonest
    agent through the REAL harness rather than through a copy of it.

    That matters more than it looks. A safety test that reimplements the thing it
    is testing proves nothing about the code that actually runs. These attacks go
    through the same workspace, the same edit applier and the same verifier as a
    genuine run, so a hole in any of them shows up here.
    """
    entry = find(task_id)
    if entry is None:
        result = AgentResult(task_id=task_id, outcome=Outcome.ERROR)
        result.summary = "there is no benchmark task called %r" % task_id
        return result

    if offline is None:
        offline = SETTINGS.is_offline()

    started = time.time()
    task = to_task(entry)
    result = AgentResult(task_id=task.task_id, title=task.title, offline=offline)

    workspace = None
    try:
        # ---- 1. a private copy of the repository ----
        workspace = Workspace.create_from(
            SETTINGS.benchmark_repo, SETTINGS.workspace_root, task.task_id,
        )
        result.workspace = str(workspace.root)

        # ---- 2. introduce the bug ----
        apply_break(workspace, entry)

        # ---- 3. the starting state, AFTER the break ----
        before_snapshot = workspace.snapshot()
        before_contents = read_all_source(workspace)

        # ---- 4. the agent ----
        budget = Budget(
            max_iterations=SETTINGS.max_iterations,
            max_tokens=SETTINGS.max_tokens_per_task,
            max_seconds=SETTINGS.max_seconds_per_task,
            usd_per_1k_input=SETTINGS.usd_per_1k_input,
            usd_per_1k_output=SETTINGS.usd_per_1k_output,
        )
        plan = plan_override if plan_override is not None else entry.get("plan")
        client = build_client(offline, plan)

        loop = run_agent(
            workspace, task.issue, task.target_tests, task.hint_paths,
            client, budget, build_loop_settings(),
        )

        result.steps = loop.steps
        result.baseline_run = loop.baseline_run
        result.final_run = loop.final_run
        result.iterations = budget.iterations_used
        result.model_calls = budget.total_calls()
        result.tokens = budget.tokens_used()
        result.cost_usd = budget.cost_usd()

        # ---- 5. what actually changed ----
        after_snapshot = workspace.snapshot()
        modified, added, removed = after_snapshot.changed_against(before_snapshot)
        changed = []
        for path in modified:
            changed.append(path)
        for path in added:
            changed.append(path)
        result.files_changed = changed
        result.diff = build_diff(before_contents, workspace, changed)

        # ---- 6. the verdict, from facts only ----
        # run_agent always sets both runs before returning, but the types say
        # they are optional and the harness should not assume its own
        # invariants. If either is missing something went wrong upstream, and
        # saying so beats an AttributeError three frames deeper.
        if loop.baseline_run is None or loop.final_run is None:
            result.outcome = Outcome.ERROR
            result.summary = ("the test suite produced no result, so there is "
                              "nothing to verify against")
            return result

        test_sources = []
        for wanted in task.target_tests:
            test_path = str(wanted).split("::")[0]
            if test_path in before_contents:
                test_sources.append(before_contents[test_path])

        result.verdict = verify(
            workspace, loop.baseline_run, loop.final_run,
            before_snapshot, after_snapshot, task.target_tests,
            SETTINGS.protected_globs,
            before_contents=before_contents,
            test_sources=test_sources,
        )
        result.flagged = not result.verdict.accepted or has_shortcut_warning(result.verdict)

        # ---- 7. what to call it ----
        if result.verdict.accepted:
            result.outcome = Outcome.SOLVED
            result.summary = "fixed in %d iteration(s): %s" % (
                result.iterations, ", ".join(changed) or "no files")
        elif loop.final_run is not None and loop.final_run.is_green():
            # Green, and still not accepted. This is the case the whole project
            # exists for, so it gets its own outcome and its own wording.
            result.outcome = Outcome.REJECTED
            result.summary = (
                "the test suite is green but the patch was REJECTED: %s"
                % "; ".join(result.verdict.failed_codes())
            )
        else:
            result.outcome = Outcome.NOT_SOLVED
            reason = loop.stopped_because or "the agent did not fix it"
            result.summary = "not fixed - %s" % reason

        if loop.model_failed != "":
            result.summary = result.summary + " (" + loop.model_failed + ")"

    except Exception as error:
        result.outcome = Outcome.ERROR
        result.summary = "the harness failed: %s: %s" % (type(error).__name__, error)

    finally:
        result.seconds = round(time.time() - started, 3)
        if workspace is not None and not keep_workspace:
            workspace.remove()
            result.workspace = ""

    return result
