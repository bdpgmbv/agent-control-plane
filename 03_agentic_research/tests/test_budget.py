"""
TESTS FOR THE BUDGET - the piece this whole project is built around.
"""

import threading

from research_agent.layer0_shared.budget import Budget, BudgetLimit


def test_a_fresh_budget_has_room():
    budget = Budget(max_tokens=1000, max_tool_calls=5, max_seconds=60, max_depth=2)
    assert budget.check() == BudgetLimit.NONE
    assert budget.has_room() is True


def test_ordinary_work_stops_before_the_whole_budget_is_gone():
    """
    The reserve exists so there is something left to write the report with.
    Research will consume everything you give it.
    """
    budget = Budget(max_tokens=1000, max_tool_calls=5, max_seconds=60, max_depth=2)
    assert budget.working_token_limit() == 850

    budget.charge_model_call(900, 0.0)
    assert budget.check() == BudgetLimit.TOKENS
    # Synthesis may still spend, because that is what the reserve is for.
    assert budget.check(for_synthesis=True) == BudgetLimit.NONE


def test_even_the_reserve_runs_out():
    budget = Budget(max_tokens=1000, max_tool_calls=5, max_seconds=60, max_depth=2)
    budget.charge_model_call(1100, 0.0)
    assert budget.check(for_synthesis=True) == BudgetLimit.TOKENS


def test_tool_calls_are_limited_separately_from_tokens():
    """
    Different kinds of cost. Tokens are your bill; searches are load on somebody
    else's service. A run can stay inside its token budget and still hammer an API.
    """
    budget = Budget(max_tokens=1000000, max_tool_calls=2, max_seconds=60, max_depth=2)
    budget.charge_tool_call()
    assert budget.check() == BudgetLimit.NONE
    budget.charge_tool_call()
    assert budget.check() == BudgetLimit.TOOL_CALLS


def test_depth_is_limited():
    budget = Budget(max_tokens=1000, max_tool_calls=5, max_seconds=60, max_depth=2)
    assert budget.depth_allowed(0) is True
    assert budget.depth_allowed(2) is True
    assert budget.depth_allowed(3) is False


def test_the_time_limit_is_reported():
    budget = Budget(max_tokens=1000, max_tool_calls=5, max_seconds=0, max_depth=2)
    assert budget.check() == BudgetLimit.SECONDS


def test_refusals_are_recorded_so_the_report_can_say_what_was_missed():
    budget = Budget(max_tokens=10, max_tool_calls=5, max_seconds=60, max_depth=2)
    budget.charge_model_call(100, 0.0)
    budget.record_refusal("sub-question: costs of X", BudgetLimit.TOKENS)

    snapshot = budget.snapshot()
    assert snapshot["stopped_because"] == "tokens"
    assert len(snapshot["work_not_done"]) == 1
    assert "costs of X" in snapshot["work_not_done"][0]


def test_one_budget_shared_by_many_threads_counts_correctly():
    """
    THE REASON EVERY COUNTER IS BEHIND A LOCK.

    Workers run in parallel and all spend from one budget. Without the lock the
    counters lose increments under contention, and the budget silently allows
    more than it was set to - which is the exact failure it exists to prevent.
    """
    budget = Budget(max_tokens=10_000_000, max_tool_calls=100000, max_seconds=60, max_depth=2)

    threads: list[threading.Thread] = []
    charges_per_thread = 500
    thread_count = 8

    def charge_many():
        position = 0
        while position < charges_per_thread:
            budget.charge_model_call(10, 0.0001)
            budget.charge_tool_call()
            position = position + 1

    position = 0
    while position < thread_count:
        thread = threading.Thread(target=charge_many)
        threads.append(thread)
        thread.start()
        position = position + 1

    for thread in threads:
        thread.join()

    assert budget.spent_tokens == thread_count * charges_per_thread * 10
    assert budget.tool_calls_made == thread_count * charges_per_thread
    assert budget.model_calls_made == thread_count * charges_per_thread


def test_a_shared_budget_does_not_scale_with_worker_count():
    """
    The design decision, written as a test.

    Four workers must not be able to spend four budgets. This is what a
    per-worker budget would silently do.
    """
    budget = Budget(max_tokens=1000, max_tool_calls=100, max_seconds=60, max_depth=2)

    def spend():
        budget.charge_model_call(300, 0.0)

    threads: list[threading.Thread] = []
    position = 0
    while position < 4:
        thread = threading.Thread(target=spend)
        threads.append(thread)
        thread.start()
        position = position + 1

    for thread in threads:
        thread.join()

    # Four workers spending 300 each is 1200 against a 1000 limit. The budget
    # must now refuse, not carry on because each worker "had room".
    assert budget.spent_tokens == 1200
    assert budget.check() == BudgetLimit.TOKENS
