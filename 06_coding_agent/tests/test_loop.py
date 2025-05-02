"""The agent loop: what stops it, and what it is told."""

import json

from tests.conftest import PROTECTED

from coding_agent.layer0_shared.usage import Budget
from coding_agent.layer7_agent.step2_recorded import RecordedClient
from coding_agent.layer7_agent.step3_loop import (
    MAX_REFUSAL_CHARACTERS,
    LoopSettings,
    run_agent,
    summarise_step_for_history,
)
from coding_agent.layer10_evaluation.tasks import find

ISSUE = "total_with_tax is subtracting the tax instead of adding it"
TARGET = ["shopkit/tests/test_pricing.py::test_tax_is_added_not_subtracted"]


def settings():
    return LoopSettings(protected_globs=PROTECTED, test_target="shopkit/tests",
                        test_timeout_seconds=60.0)


def budget(iterations=4):
    return Budget(max_iterations=iterations, max_tokens=500000, max_seconds=120.0)


def reply(thinking, edits=None, read_files=None, done=False):
    return json.dumps({"thinking": thinking, "read_files": read_files or [],
                       "edits": edits or [], "done": done})


def fixing_edit():
    return {"path": "shopkit/pricing.py",
            "old_text": "    return round_money(subtotal - tax)",
            "new_text": "    return round_money(subtotal + tax)",
            "reason": "tax is added"}


def test_a_correct_patch_ends_the_loop(broken_workspace):
    client = RecordedClient([reply("fixing it", [fixing_edit()])])
    outcome = run_agent(broken_workspace, ISSUE, TARGET, [], client, budget(), settings())

    assert outcome.final_run.is_green()
    assert outcome.stopped_because == "the suite is green"
    assert len(outcome.steps) == 1


def test_the_loop_stops_at_the_iteration_limit(broken_workspace):
    # Three replies that change nothing useful. The loop must stop, not spin.
    useless = reply("thinking about it", [{"path": "shopkit/pricing.py",
                                           "old_text": "nowhere to be found",
                                           "new_text": "x"}])
    client = RecordedClient([useless, useless, useless, useless, useless])
    spent = budget(iterations=3)
    run_agent(broken_workspace, ISSUE, TARGET, [], client, spent, settings())

    assert spent.iterations_used == 3
    assert "3 attempts" in spent.stop_reason


def test_the_loop_stops_when_the_token_budget_runs_out(broken_workspace):
    client = RecordedClient([reply("t", []), reply("t", []), reply("t", [])],
                            tokens_per_reply=(4000, 1000))
    spent = Budget(max_iterations=99, max_tokens=6000, max_seconds=120.0)
    run_agent(broken_workspace, ISSUE, TARGET, [], client, spent, settings())
    assert "token budget" in spent.stop_reason


def test_the_agent_saying_it_is_done_does_not_make_it_so(broken_workspace):
    client = RecordedClient([reply("Looks correct to me.", [], done=True)])
    outcome = run_agent(broken_workspace, ISSUE, TARGET, [], client, budget(), settings())

    assert not outcome.final_run.is_green()
    assert "its own account" in outcome.stopped_because.lower() or \
           "not what decides" in outcome.stopped_because.lower()


def test_a_refused_edit_is_reported_back_to_the_agent(broken_workspace):
    client = RecordedClient([
        reply("edit the test", [{"path": "shopkit/tests/test_pricing.py",
                                 "old_text": "== 120.0", "new_text": "== 80.0"}]),
        reply("fine, the source then", [fixing_edit()]),
    ])
    outcome = run_agent(broken_workspace, ISSUE, TARGET, [], client, budget(), settings())

    assert len(client.prompts) == 2
    assert "REFUSED" in client.prompts[1]
    assert "protected" in client.prompts[1] or "may not be edited" in client.prompts[1]
    assert outcome.final_run.is_green()


def test_the_full_ambiguity_message_reaches_the_agent(workspace):
    # This used to be truncated to 90 characters, which cut off everything
    # useful and left the agent requoting the same string until it ran out.
    from coding_agent.layer10_evaluation.tasks import apply_break

    apply_break(workspace, find("rounding_truncates"))
    client = RecordedClient([
        reply("fix it", [{"path": "shopkit/money.py",
                          "old_text": "rounded = int(scaled)",
                          "new_text": "rounded = round(scaled)"}]),
        reply("again", []),
    ])
    run_agent(workspace, "rounding truncates",
              ["shopkit/tests/test_money.py::test_does_not_truncate"],
              [], client, budget(), settings())

    second_prompt = client.prompts[1]
    assert "around line" in second_prompt
    assert "a unique old_text for THIS one:" in second_prompt


def test_the_refusal_limit_is_generous_enough_for_that_message():
    assert MAX_REFUSAL_CHARACTERS >= 1000


def test_asking_for_a_file_already_in_context_is_pointed_out(broken_workspace):
    client = RecordedClient([
        reply("let me look", [], ["shopkit/pricing.py"]),
        reply("now fix it", [fixing_edit()]),
    ])
    run_agent(broken_workspace, ISSUE, TARGET, [], client, budget(), settings())
    assert "ALREADY in the context" in client.prompts[1]


def test_asking_for_a_file_that_does_not_exist_is_reported(broken_workspace):
    client = RecordedClient([
        reply("look at this", [], ["shopkit/nowhere.py"]),
        reply("fix", [fixing_edit()]),
    ])
    run_agent(broken_workspace, ISSUE, TARGET, [], client, budget(), settings())
    assert "does not exist" in client.prompts[1]


def test_an_unreadable_reply_costs_an_iteration_but_not_the_run(broken_workspace):
    client = RecordedClient(["this is not json at all", reply("fix", [fixing_edit()])])
    outcome = run_agent(broken_workspace, ISSUE, TARGET, [], client, budget(), settings())
    assert outcome.final_run.is_green()
    assert outcome.steps[0].action == "error"


def test_an_already_green_repository_is_recognised(workspace):
    client = RecordedClient([reply("nothing to do", [])])
    outcome = run_agent(workspace, ISSUE, TARGET, [], client, budget(), settings())
    assert "already green" in outcome.stopped_because
    assert len(client.prompts) == 0, "the model should never have been called"


def test_history_carries_what_happened(broken_workspace):
    from coding_agent.layer2_models.schemas import AgentStep, EditResult, EditStatus, FileEdit

    step = AgentStep(number=1, action="edit", edits=[EditResult(
        edit=FileEdit(path="a.py", old_text="x", new_text="y"),
        status=EditStatus.PROTECTED, message="a.py may not be edited")])
    text = summarise_step_for_history(step)
    assert "REFUSED" in text
    assert "may not be edited" in text
