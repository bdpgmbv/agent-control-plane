"""
The engine: what advances a run, what stops it, and what it does when things go
wrong.

Most of these describe something failing. That is the point - the happy path is
one of eight outcomes, and the other seven are the reason a workflow engine
exists rather than a for-loop.
"""

import time

from tests.conftest import EXPENSIVE_REQUEST, base_input

from enterprise_workflow.layer2_models.schemas import RunState, StepState
from enterprise_workflow.layer5_steps.step3_onboarding import REGISTRY, WORKFLOWS
from enterprise_workflow.layer6_engine.step1_engine import (
    Engine,
    EngineSettings,
    backoff_seconds,
)


def test_a_clean_run_finishes(engine, store):
    run = engine.start("employee_onboarding", base_input())
    engine.run_until_idle()

    final = store.get_run(run.run_id)
    assert final.state == RunState.SUCCEEDED
    for step in store.get_steps(run.run_id):
        assert step.state == StepState.SUCCEEDED, step.step_name


def test_the_steps_happen_in_order(engine, store):
    run = engine.start("employee_onboarding", base_input())
    engine.run_until_idle()

    order = []
    for event in store.list_events(run.run_id):
        if event.kind == "step.succeeded":
            order.append(event.step_name)
    assert order == WORKFLOWS["employee_onboarding"]


def test_a_transient_failure_is_retried(engine, store):
    run = engine.start("employee_onboarding", base_input(fail_licences_times=2))
    engine.run_until_idle()

    assert store.get_run(run.run_id).state == RunState.SUCCEEDED
    assert store.get_step(run.run_id, "provision_licences").attempts == 3


def test_retrying_stops_at_the_limit(engine_without_cleanup, store):
    run = engine_without_cleanup.start("employee_onboarding",
                                       base_input(fail_licences_times=99))
    engine_without_cleanup.run_until_idle()

    assert store.get_run(run.run_id).state == RunState.FAILED
    assert store.get_step(run.run_id, "provision_licences").attempts == 3


def test_a_permanent_failure_is_not_retried(engine, store):
    run = engine.start("employee_onboarding", base_input(
        request_text=base_input()["request_text"].replace("2026-11-03", "2020-01-06")))
    engine.run_until_idle()

    assert store.get_run(run.run_id).state == RunState.FAILED
    # One attempt, not three. The input will not change.
    assert store.get_step(run.run_id, "validate").attempts == 1


def test_the_backoff_grows_and_is_capped():
    assert backoff_seconds(1, 1.0, 30.0) == 1
    assert backoff_seconds(3, 1.0, 30.0) == 4
    # Capped, because an uncapped backoff stops being a retry and becomes an
    # outage nobody has noticed.
    assert backoff_seconds(12, 1.0, 30.0) == 30


def test_a_step_that_raises_does_not_take_the_worker_down(engine, store):
    class Exploding:
        name = "intake"
        retryable = False

        def run(self, context):
            raise RuntimeError("something unexpected")

        def idempotency_key(self, context):
            return ""

        def needs_approval(self, context):
            return (False, "", {})

        def can_compensate(self):
            return False

        def changes_the_outside_world(self):
            return False

    original = REGISTRY.steps["intake"]
    REGISTRY.steps["intake"] = Exploding()
    try:
        run = engine.start("employee_onboarding", base_input())
        result = engine.tick()
        assert result.did_work
        assert store.get_run(run.run_id).state == RunState.FAILED
    finally:
        REGISTRY.steps["intake"] = original


def test_an_expensive_order_parks_the_run(engine, store):
    run = engine.start("employee_onboarding",
                       base_input(request_text=EXPENSIVE_REQUEST))
    engine.run_until_idle()

    assert store.get_run(run.run_id).state == RunState.WAITING_APPROVAL
    pending = store.list_approvals(run.run_id, pending_only=True)
    assert len(pending) == 1
    assert pending[0].step_name == "order_equipment"


def test_a_parked_run_does_nothing_until_somebody_answers(engine, store):
    run = engine.start("employee_onboarding",
                       base_input(request_text=EXPENSIVE_REQUEST))
    engine.run_until_idle()
    before = store.get_steps(run.run_id)

    engine.run_until_idle()
    engine.run_until_idle()

    after = store.get_steps(run.run_id)
    for index in range(len(before)):
        assert before[index].state == after[index].state


def test_approving_lets_it_continue_on_a_different_worker(store, settings):
    """
    The decision arrives days later, on a process that did not park the run.

    Nothing is pushed to a worker; the next one to tick notices. That is the
    only design that survives the worker being restarted while it waits.
    """
    from enterprise_workflow.layer7_recovery.step1_compensate import Compensator

    first = Engine(store, REGISTRY, WORKFLOWS, settings, client=None,
                   worker_id="worker-parked",
                   compensator=Compensator(store, REGISTRY, None, settings))
    run = first.start("employee_onboarding", base_input(request_text=EXPENSIVE_REQUEST))
    first.run_until_idle()

    approval = store.list_approvals(run.run_id, pending_only=True)[0]
    store.decide_approval(approval.approval_id, True, "grace")

    second = Engine(store, REGISTRY, WORKFLOWS, settings, client=None,
                    worker_id="worker-fresh",
                    compensator=Compensator(store, REGISTRY, None, settings))
    second.run_until_idle()

    assert store.get_run(run.run_id).state == RunState.SUCCEEDED


def test_rejecting_ends_the_run(engine, store):
    run = engine.start("employee_onboarding", base_input(request_text=EXPENSIVE_REQUEST))
    engine.run_until_idle()

    approval = store.list_approvals(run.run_id, pending_only=True)[0]
    store.decide_approval(approval.approval_id, False, "grace", "over budget")
    engine.run_until_idle()

    assert store.get_run(run.run_id).state == RunState.COMPENSATED


def test_a_rejected_run_does_not_leave_the_account_behind(engine, store):
    # The account is created two steps before the approval gate. A rejection
    # that forgets it leaves a live account for somebody who is not joining.
    run = engine.start("employee_onboarding", base_input(request_text=EXPENSIVE_REQUEST))
    engine.run_until_idle()
    approval = store.list_approvals(run.run_id, pending_only=True)[0]
    store.decide_approval(approval.approval_id, False, "grace")
    engine.run_until_idle()

    for effect in store.list_side_effects(run.run_id):
        assert effect.compensated, effect.idempotency_key


def test_cancelling_stops_the_run_and_cleans_up(engine, store):
    run = engine.start("employee_onboarding", base_input())
    for _ in range(3):
        engine.tick()

    store.request_cancel(run.run_id)
    engine.run_until_idle()

    final = store.get_run(run.run_id)
    # Cancelled, not compensated: why it ended and what was done about it are
    # different facts, and a reader needs both.
    assert final.state == RunState.CANCELLED
    for effect in store.list_side_effects(run.run_id):
        assert effect.compensated


def test_a_cancelled_run_is_noticed_even_with_nothing_claimable(engine, store):
    # The claim query skips cancelled runs, so this can only be found by
    # housekeeping. Without it the run sat in RUNNING for ever.
    run = engine.start("employee_onboarding", base_input())
    engine.tick()
    store.request_cancel(run.run_id)

    result = engine.tick()
    assert result.did_work
    assert store.get_run(run.run_id).state == RunState.CANCELLED


def test_a_crashed_worker_step_is_taken_over(store, settings):
    """A worker claims a step and dies. Another finishes the run."""
    from enterprise_workflow.layer7_recovery.step1_compensate import Compensator

    quick = EngineSettings(lease_seconds=0.05, max_attempts=3,
                           retry_base_seconds=0.0, retry_max_seconds=0.0)
    doomed = Engine(store, REGISTRY, WORKFLOWS, quick, client=None,
                    worker_id="doomed")
    run = doomed.start("employee_onboarding", base_input())

    claimed = store.claim_next_step("doomed", 0.05)
    store.start_step(run.run_id, claimed.step_name, "doomed")
    # ... and the process dies here, having written nothing further.

    time.sleep(0.1)

    survivor = Engine(store, REGISTRY, WORKFLOWS, settings, client=None,
                      worker_id="survivor",
                      compensator=Compensator(store, REGISTRY, None, settings))
    survivor.run_until_idle()

    assert store.get_run(run.run_id).state == RunState.SUCCEEDED

    keys = []
    for effect in store.list_side_effects(run.run_id):
        keys.append(effect.idempotency_key)
    assert len(keys) == len(set(keys)), "an effect happened twice"


def test_two_workers_on_one_run_do_not_duplicate_anything(store, settings):
    from enterprise_workflow.layer7_recovery.step1_compensate import Compensator

    first = Engine(store, REGISTRY, WORKFLOWS, settings, client=None, worker_id="a",
                   compensator=Compensator(store, REGISTRY, None, settings))
    second = Engine(store, REGISTRY, WORKFLOWS, settings, client=None, worker_id="b",
                    compensator=Compensator(store, REGISTRY, None, settings))

    run = first.start("employee_onboarding", base_input())
    for _ in range(40):
        first.tick()
        second.tick()
        if store.get_run(run.run_id).state.is_finished():
            break

    assert store.get_run(run.run_id).state == RunState.SUCCEEDED
    keys = []
    for effect in store.list_side_effects(run.run_id):
        keys.append(effect.idempotency_key)
    assert len(keys) == len(set(keys))


def test_an_unknown_workflow_is_refused(engine):
    import pytest
    with pytest.raises(ValueError):
        engine.start("no_such_workflow", {})
