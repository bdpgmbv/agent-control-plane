"""The database: claiming, leases, idempotency, and deciding once."""

import time

import pytest

from enterprise_workflow.layer2_models.schemas import RunState, StepState
from enterprise_workflow.layer3_database.store import AlreadyDone

STEPS = ["one", "two", "three"]


def make_run(store):
    return store.create_run("test_flow", {"a": 1}, STEPS)


def test_a_run_is_created_with_all_of_its_steps(store):
    run = make_run(store)
    steps = store.get_steps(run.run_id)
    assert len(steps) == 3

    # The plan is on disk before any of it is attempted, so a run that dies at
    # step two still shows step three waiting rather than looking complete.
    assert steps[0].state == StepState.READY
    assert steps[1].state == StepState.BLOCKED
    assert steps[2].state == StepState.BLOCKED


def test_only_one_worker_can_claim_a_step(store):
    make_run(store)
    first = store.claim_next_step("worker-a", 30)
    second = store.claim_next_step("worker-b", 30)

    assert first is not None
    assert second is None
    assert first.claimed_by == "worker-a"


def test_a_blocked_step_cannot_be_claimed(store):
    make_run(store)
    store.claim_next_step("worker-a", 30)
    # Only "one" was ready, and it is now claimed. Nothing else is available.
    assert store.claim_next_step("worker-b", 30) is None


def test_finishing_a_step_makes_the_next_one_available(store):
    run = make_run(store)
    claimed = store.claim_next_step("worker-a", 30)
    store.set_step_state(run.run_id, claimed.step_name, StepState.SUCCEEDED)

    next_name = store.unblock_next_step(run.run_id, claimed.position)
    assert next_name == "two"
    assert store.claim_next_step("worker-b", 30).step_name == "two"


def test_an_expired_lease_returns_the_step(store):
    make_run(store)
    store.claim_next_step("worker-a", lease_seconds=0.01)
    assert store.claim_next_step("worker-b", 30) is None

    time.sleep(0.05)
    released = store.release_expired_leases()
    assert released == 1

    # This is the whole crash-recovery mechanism: a timestamp that stops moving
    # when the process stops existing.
    retaken = store.claim_next_step("worker-b", 30)
    assert retaken is not None
    assert retaken.claimed_by == "worker-b"


def test_a_live_lease_is_not_stolen(store):
    make_run(store)
    store.claim_next_step("worker-a", lease_seconds=60)
    assert store.release_expired_leases() == 0


def test_starting_a_step_counts_the_attempt_before_the_work(store):
    run = make_run(store)
    claimed = store.claim_next_step("worker-a", 30)

    assert store.start_step(run.run_id, claimed.step_name, "worker-a")
    step = store.get_step(run.run_id, claimed.step_name)
    assert step.attempts == 1
    assert step.state == StepState.RUNNING


def test_a_worker_cannot_start_a_step_it_does_not_hold(store):
    run = make_run(store)
    claimed = store.claim_next_step("worker-a", 30)
    assert not store.start_step(run.run_id, claimed.step_name, "worker-b")


def test_the_same_effect_cannot_be_recorded_twice(store):
    run = make_run(store)
    store.record_side_effect(run.run_id, "one", "acct:ada", "account", {"id": "A1"})

    with pytest.raises(AlreadyDone):
        store.record_side_effect(run.run_id, "one", "acct:ada", "account", {"id": "A2"})

    # And the first one is what survives.
    assert store.find_side_effect("acct:ada").detail["id"] == "A1"


def test_different_keys_are_different_effects(store):
    run = make_run(store)
    store.record_side_effect(run.run_id, "one", "acct:ada", "account", {})
    store.record_side_effect(run.run_id, "one", "acct:grace", "account", {})
    assert len(store.list_side_effects(run.run_id)) == 2


def test_an_approval_can_only_be_decided_once(store):
    run = make_run(store)
    approval = store.create_approval(run.run_id, "two", "may we?", {}, 72)

    assert store.decide_approval(approval.approval_id, True, "grace")
    assert not store.decide_approval(approval.approval_id, False, "alan")

    decided = store.get_approval(approval.approval_id)
    assert decided.decided_by == "grace"
    assert decided.state.value == "approved"


def test_approvals_expire(store):
    run = make_run(store)
    store.create_approval(run.run_id, "two", "may we?", {}, expiry_hours=-1)
    assert store.expire_approvals() == 1
    assert store.list_approvals(run.run_id, pending_only=True) == []


def test_events_are_append_only_and_ordered(store):
    run = make_run(store)
    store.add_event(run.run_id, "first")
    store.add_event(run.run_id, "second")
    kinds = []
    for event in store.list_events(run.run_id):
        kinds.append(event.kind)
    assert kinds == ["run.created", "first", "second"]


def test_context_accumulates(store):
    run = make_run(store)
    store.merge_run_context(run.run_id, {"a": 1})
    store.merge_run_context(run.run_id, {"b": 2})
    assert store.get_run(run.run_id).context == {"a": 1, "b": 2}


def test_a_cancelled_run_has_nothing_claimable(store):
    run = make_run(store)
    store.request_cancel(run.run_id)
    store.set_run_state(run.run_id, RunState.RUNNING)
    # Deliberate: a cancelled run must not have more work done on it. The engine
    # notices the cancellation in housekeeping instead.
    assert store.claim_next_step("worker-a", 30) is None
