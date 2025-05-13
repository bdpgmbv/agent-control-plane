"""The approval queue."""

from tests.conftest import EXPENSIVE_REQUEST, base_input

from enterprise_workflow.layer8_approvals.step1_queue import ApprovalQueue


def test_a_parked_run_appears_in_the_queue(engine, store):
    engine.start("employee_onboarding", base_input(request_text=EXPENSIVE_REQUEST))
    engine.run_until_idle()

    cards = ApprovalQueue(store).pending()
    assert len(cards) == 1
    assert cards[0].person == "Ada Lovelace"
    assert cards[0].detail["total"] > 2000.0
    assert cards[0].expires_in_hours > 0


def test_deciding_is_recorded_once(engine, store):
    engine.start("employee_onboarding", base_input(request_text=EXPENSIVE_REQUEST))
    engine.run_until_idle()

    queue = ApprovalQueue(store)
    card = queue.pending()[0]

    first = queue.decide(card.approval_id, True, "grace")
    assert first.ok

    second = queue.decide(card.approval_id, False, "alan")
    assert not second.ok
    assert "already approved" in second.message


def test_a_decision_must_be_attributed(engine, store):
    engine.start("employee_onboarding", base_input(request_text=EXPENSIVE_REQUEST))
    engine.run_until_idle()

    queue = ApprovalQueue(store)
    card = queue.pending()[0]
    result = queue.decide(card.approval_id, True, "   ")
    assert not result.ok
    assert "attributed" in result.message


def test_an_unknown_approval_is_refused(store):
    result = ApprovalQueue(store).decide("apr_nope", True, "grace")
    assert not result.ok


def test_answering_does_not_itself_resume_the_run(engine, store):
    """
    A decision writes one row and returns. The worker that parked the run three
    days ago does not exist any more, so there is nobody to notify.
    """
    run = engine.start("employee_onboarding", base_input(request_text=EXPENSIVE_REQUEST))
    engine.run_until_idle()

    queue = ApprovalQueue(store)
    queue.decide(queue.pending()[0].approval_id, True, "grace")

    # Still parked until somebody ticks.
    assert store.get_run(run.run_id).state.value == "waiting_approval"

    engine.run_until_idle()
    assert store.get_run(run.run_id).state.value == "succeeded"
