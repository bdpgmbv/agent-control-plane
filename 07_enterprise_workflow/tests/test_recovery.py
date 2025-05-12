"""Compensation: undoing what already happened, in the right order."""

from tests.conftest import base_input

from enterprise_workflow.layer2_models.schemas import RunState
from enterprise_workflow.layer5_steps.step3_onboarding import REGISTRY
from enterprise_workflow.layer7_recovery.step1_compensate import Compensator


def test_a_failed_run_is_cleaned_up(engine, store):
    run = engine.start("employee_onboarding", base_input(fail_payroll_times=99))
    engine.run_until_idle()

    assert store.get_run(run.run_id).state == RunState.COMPENSATED
    effects = store.list_side_effects(run.run_id)
    assert len(effects) == 3          # account, equipment, licences
    for effect in effects:
        assert effect.compensated, effect.idempotency_key


def test_things_are_undone_newest_first(engine, store):
    """
    The licences are attached to the account, so releasing them has to happen
    before the account is deleted - otherwise the subscription bills for ever
    with nothing pointing at it.
    """
    run = engine.start("employee_onboarding", base_input(fail_payroll_times=99))
    engine.run_until_idle()

    order = []
    for event in store.list_events(run.run_id):
        if event.kind == "compensation.done":
            order.append(event.step_name)

    assert order == ["provision_licences", "order_equipment", "create_account"]


def test_a_run_that_did_nothing_has_nothing_to_undo(engine, store):
    run = engine.start("employee_onboarding", base_input(request_text="   "))
    engine.run_until_idle()

    assert store.get_run(run.run_id).state == RunState.FAILED
    assert store.list_side_effects(run.run_id) == []


def test_compensating_twice_does_not_undo_twice(engine, store):
    run = engine.start("employee_onboarding", base_input(fail_payroll_times=99))
    engine.run_until_idle()

    compensator = Compensator(store, REGISTRY)
    again = compensator.compensate_run(run.run_id)

    assert again.undone == []          # everything was already marked done
    assert again.finished


def test_a_failed_undo_leaves_the_run_needing_a_person(store, settings):
    """
    Compensation asks the same flaky vendors that caused the problem. When it
    fails, the run must NOT be marked compensated - that would say the mess was
    cleaned up when it was not.
    """
    class Stubborn:
        name = "create_account"
        retryable = True

        def compensate(self, context, effect_detail):
            raise RuntimeError("the directory is refusing deletes")

        def can_compensate(self):
            return True

    run = store.create_run("employee_onboarding", {}, ["create_account"])
    store.record_side_effect(run.run_id, "create_account", "acct:x", "account", {})

    registry_stub = type("R", (), {"get": staticmethod(lambda name: Stubborn())})()
    report = Compensator(store, registry_stub).compensate_run(run.run_id)

    assert len(report.could_not_undo) == 1
    assert not report.finished
    assert store.get_run(run.run_id).state == RunState.COMPENSATING


def test_something_that_cannot_be_undone_is_recorded_not_hidden(store, settings):
    class SendsEmail:
        name = "notify_manager"

        def can_compensate(self):
            return False

    run = store.create_run("employee_onboarding", {}, ["notify_manager"])
    store.record_side_effect(run.run_id, "notify_manager", "notify:x", "notification", {})

    registry_stub = type("R", (), {"get": staticmethod(lambda name: SendsEmail())})()
    report = Compensator(store, registry_stub).compensate_run(run.run_id)

    # Somebody has to know an email went out to a person who is not joining.
    assert len(report.nothing_to_undo) == 1
    assert report.finished
