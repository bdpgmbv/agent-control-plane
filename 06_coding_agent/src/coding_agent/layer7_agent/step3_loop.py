"""
LAYER 7, STEP 3 - THE LOOP
==========================
    run the tests
    while the budget allows and the suite is not green:
        show the agent the failure and the code
        read its reply
        apply whatever edits it asked for
        run the tests again

The shape is the argument. Notice what decides whether the loop continues: the
test run, and the budget. Not the agent. The agent proposes; two facts dispose.

This is the difference between a loop that terminates and one that does not. An
agent asked "are you finished?" will eventually say yes whether or not it is,
and will occasionally say no forever. `budget.may_continue()` is arithmetic and
`run.is_green()` is an exit code, and neither of them is persuadable.

The other thing worth reading for is how failure is handled. Every refused edit,
every unparseable reply, every test run that got worse rather than better is fed
back to the agent as text, because it has no memory between calls. A harness that
silently swallows a refused edit leaves the agent convinced it made a change it
did not make, and the next iteration is then spent reasoning about a file that
never moved.
"""

import time

from coding_agent.layer0_shared.model_client import ModelUnavailable
from coding_agent.layer0_shared.usage import Budget
from coding_agent.layer2_models.schemas import AgentStep, TestOutcome, TestRun
from coding_agent.layer4_explore.step1_map import build_outlines, render_map
from coding_agent.layer4_explore.step2_rank import choose_files, rank_files
from coding_agent.layer4_explore.step3_context import build_context
from coding_agent.layer5_tests.step1_run import run_tests, shorten_for_prompt
from coding_agent.layer6_edit.step2_apply import apply_edits, describe_results
from coding_agent.layer7_agent.step1_prompt import (
    SYSTEM_PROMPT,
    build_user_prompt,
    done_from_reply,
    edits_from_reply,
    files_from_reply,
    parse_reply,
    thinking_from_reply,
)


class LoopSettings:
    """
    The knobs the loop needs, gathered so tests can vary them without .env.

    Same reasoning as project 05's ValidationSettings: a threshold that can only
    be changed by editing a file is a threshold nobody will ever measure.
    """

    def __init__(self, max_files_in_context: int = 6,
                 max_file_characters: int = 8000,
                 max_test_output_characters: int = 4000,
                 test_timeout_seconds: float = 60.0,
                 max_edit_file_bytes: int = 400000,
                 protected_globs: list[str] | None = None,
                 test_target: str = "") -> None:
        self.max_files_in_context = max_files_in_context
        self.max_file_characters = max_file_characters
        self.max_test_output_characters = max_test_output_characters
        self.test_timeout_seconds = test_timeout_seconds
        self.max_edit_file_bytes = max_edit_file_bytes
        if protected_globs is None:
            protected_globs = ["tests/*", "test_*.py", "*_test.py", "conftest.py"]
        self.protected_globs = protected_globs
        self.test_target = test_target


class LoopOutcome:
    def __init__(self) -> None:
        self.steps: list[AgentStep] = []
        self.baseline_run: TestRun | None = None
        self.final_run: TestRun | None = None
        self.stopped_because = ""
        self.model_failed = ""


# A refusal message is the agent's only channel for learning how to succeed, so
# it is not summarised away. The ambiguity message quotes every match with its
# surrounding lines, which is the whole point of it.
#
# This limit used to be 90 characters. That silently truncated the ambiguity
# message to its first sentence - "that text appears 2 times, so there is no way
# to know which one you mean" - and threw away the part that says where they are.
# The result was an agent that was told what was wrong six times in a row and
# never once told how to fix it, which is exactly how it behaved: it requoted the
# same string until the budget ran out.
#
# Improving the error message did nothing until this number changed too. A better
# message that gets truncated before it arrives is not a better message.
MAX_REFUSAL_CHARACTERS = 1500
MAX_REASON_CHARACTERS = 80


def summarise_step_for_history(step: AgentStep) -> str:
    """What the agent is handed back next time, so it stops repeating itself."""
    if step.action == "explore":
        return "read %s - no edit made" % ", ".join(step.files_read)

    parts = []
    for result in step.edits:
        if result.ok():
            parts.append("edited %s (%s)"
                         % (result.edit.path, result.edit.reason[:MAX_REASON_CHARACTERS]))
        else:
            parts.append("tried to edit %s but it was REFUSED.\n%s"
                         % (result.edit.path, result.message[:MAX_REFUSAL_CHARACTERS]))

    if step.test_run is not None:
        parts.append("tests then said: %s" % step.test_run.summary())

    if len(parts) == 0:
        return step.note or "nothing happened"
    return "\n".join(parts)


def run_agent(workspace, issue: str, target_tests: list[str], hint_paths: list[str],
              client, budget: Budget, settings: LoopSettings) -> LoopOutcome:
    outcome = LoopOutcome()
    history: list[str] = []
    extra_files: list[str] = []      # files the agent asked to see

    # ---- the baseline. Everything is measured against this. ----
    baseline = run_tests(workspace.root, settings.test_target or None,
                         settings.test_timeout_seconds)
    outcome.baseline_run = baseline
    outcome.final_run = baseline

    if baseline.is_green():
        outcome.stopped_because = (
            "the suite was already green before the agent did anything, so "
            "there is nothing here to fix"
        )
        return outcome

    latest = baseline

    while budget.may_continue():
        budget.start_iteration()
        step_started = time.time()
        step = AgentStep(number=budget.iterations_used)

        # ---- 1. work out what to show it ----
        outlines = build_outlines(workspace)
        repo_map = render_map(outlines)

        contents = {}
        for name in workspace.source_files():
            try:
                contents[name] = workspace.read(name, settings.max_edit_file_bytes)
            except Exception:
                continue

        ranked = rank_files(outlines, issue, target_tests, hint_paths, contents)
        chosen = choose_files(ranked, settings.max_files_in_context)

        paths = []
        for entry in chosen:
            paths.append(entry.path)
        # Anything the agent explicitly asked for goes in, whatever it scored.
        for name in extra_files:
            if name not in paths and name in contents:
                paths.append(name)

        bundle = build_context(workspace, repo_map, paths,
                               settings.max_file_characters,
                               workspace.source_files())

        prompt = build_user_prompt(
            issue, target_tests,
            shorten_for_prompt(latest, settings.max_test_output_characters),
            bundle.text, history,
        )

        # ---- 2. ask ----
        try:
            reply = client.complete(SYSTEM_PROMPT, prompt, max_tokens=2500)
        except ModelUnavailable as error:
            outcome.model_failed = error.friendly_message()
            outcome.stopped_because = "the model became unavailable: %s" % error.friendly_message()
            step.action = "give_up"
            step.note = outcome.model_failed
            step.seconds = round(time.time() - step_started, 3)
            outcome.steps.append(step)
            return outcome

        budget.record("agent", reply.input_tokens, reply.output_tokens)
        step.tokens = reply.total_tokens()

        parsed, parse_error = parse_reply(reply.text)
        if parse_error != "":
            step.action = "error"
            step.note = parse_error
            history.append("your reply could not be read: %s" % parse_error)
            step.seconds = round(time.time() - step_started, 3)
            outcome.steps.append(step)
            continue

        step.thinking = thinking_from_reply(parsed)
        wanted_files = files_from_reply(parsed)
        edits, complaints = edits_from_reply(parsed)

        # ---- 3. an explore step: it asked to see something ----
        if len(edits) == 0 and len(wanted_files) > 0:
            step.action = "explore"
            accepted = []
            for name in wanted_files:
                if workspace.exists(name):
                    if name not in extra_files:
                        extra_files.append(name)
                    accepted.append(name)
                else:
                    history.append("you asked for %s, which does not exist" % name)
            already_shown = []
            for name in accepted:
                if name in bundle.files_included:
                    already_shown.append(name)
            if len(already_shown) > 0:
                # It asked for something it was already looking at. Saying so
                # stops it spending iterations re-requesting the same file,
                # which is what it does when it is stuck.
                history.append(
                    "%s was ALREADY in the context you were given - re-reading "
                    "it will not tell you anything new. Make an edit, or read a "
                    "different file." % ", ".join(already_shown)
                )
            step.files_read = accepted
            step.note = "read %d file(s)" % len(accepted)
            history.append(summarise_step_for_history(step))
            step.seconds = round(time.time() - step_started, 3)
            outcome.steps.append(step)
            continue

        # ---- 4. it says it is finished ----
        if len(edits) == 0 and done_from_reply(parsed):
            step.action = "give_up"
            step.note = "the agent said it was finished without making a change"
            outcome.stopped_because = (
                "the agent reported it was finished, but the suite is not green. "
                "Its own account of the work is not what decides this."
            )
            step.seconds = round(time.time() - step_started, 3)
            outcome.steps.append(step)
            return outcome

        # ---- 5. nothing usable came back ----
        if len(edits) == 0:
            step.action = "error"
            problem = "; ".join(complaints) if len(complaints) > 0 else \
                "no edits and no files requested"
            step.note = problem
            history.append("your last reply did nothing: %s" % problem)
            step.seconds = round(time.time() - step_started, 3)
            outcome.steps.append(step)
            continue

        # ---- 6. apply, then test ----
        step.action = "edit"
        results, applied = apply_edits(workspace, edits, settings.protected_globs,
                                       settings.max_edit_file_bytes)
        step.edits = results

        if not applied:
            # Nothing was written. The agent must be told, or it will spend the
            # next iteration reasoning about a change that never happened.
            step.note = "no edit was applied:\n" + describe_results(results)
            history.append(summarise_step_for_history(step))
            step.seconds = round(time.time() - step_started, 3)
            outcome.steps.append(step)
            continue

        latest = run_tests(workspace.root, settings.test_target or None,
                           settings.test_timeout_seconds)
        step.test_run = latest
        outcome.final_run = latest

        history.append(summarise_step_for_history(step))
        step.seconds = round(time.time() - step_started, 3)
        outcome.steps.append(step)

        if latest.is_green():
            outcome.stopped_because = "the suite is green"
            return outcome

        if latest.outcome == TestOutcome.TIMEOUT:
            outcome.stopped_because = (
                "the test suite stopped finishing after this change, which "
                "usually means the edit introduced a loop that does not end"
            )
            return outcome

    if outcome.stopped_because == "":
        outcome.stopped_because = budget.stop_reason or "the budget ran out"
    return outcome
