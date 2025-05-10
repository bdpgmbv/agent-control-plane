"""The steps themselves: keys, validation, and what each one promises."""

from enterprise_workflow.layer5_steps.step1_base import StepContext
from enterprise_workflow.layer5_steps.step2_external import (
    FAILURES,
    IDENTITY,
    LICENCES,
    PAYROLL,
    ExternalServiceError,
)
from enterprise_workflow.layer5_steps.step3_onboarding import (
    ONBOARDING_STEPS,
    REGISTRY,
)


def context_for(store, fields: dict, **extra) -> StepContext:
    run = store.create_run("employee_onboarding", {}, ONBOARDING_STEPS)
    merged = {"fields": fields}
    for key, value in extra.items():
        merged[key] = value
    return StepContext(run_id=run.run_id, workflow_input=merged, context=merged,
                       store=store, client=None, settings=None)


GOOD_FIELDS = {
    "full_name": "Ada Lovelace", "email": "ada@example.com",
    "department": "engineering", "start_date": "2026-11-03",
    "salary": 95000.0, "equipment": ["headset"],
}


def test_every_step_in_the_workflow_is_registered():
    for name in ONBOARDING_STEPS:
        assert REGISTRY.get(name) is not None, name


def test_the_steps_that_change_the_world_are_all_idempotent(store):
    context = context_for(store, GOOD_FIELDS)
    for name in ("create_account", "order_equipment", "provision_licences",
                 "enrol_payroll", "notify_manager"):
        step = REGISTRY.get(name)
        assert step.changes_the_outside_world(), name
        assert step.idempotency_key(context) != "", name


def test_everything_that_can_be_undone_except_the_email(store):
    for name in ("create_account", "order_equipment", "provision_licences",
                 "enrol_payroll"):
        assert REGISTRY.get(name).can_compensate(), name

    # Deliberate, and why it is the last step: nothing can unsend it.
    assert not REGISTRY.get("notify_manager").can_compensate()


def test_the_payroll_key_does_not_change_between_attempts(store):
    # If it did, every retry would look like new work, and being enrolled twice
    # means being paid twice.
    context = context_for(store, GOOD_FIELDS, account_id="ACC-1")
    step = REGISTRY.get("enrol_payroll")
    first = step.idempotency_key(context)
    second = step.idempotency_key(context)
    assert first == second
    assert "ACC-1" in first


def test_validation_rejects_what_it_should(store):
    step = REGISTRY.get("validate")

    cases = [
        ({}, "missing"),
        (dict(GOOD_FIELDS, email="not-an-email"), "not an email"),
        (dict(GOOD_FIELDS, start_date="2020-01-06"), "in the past"),
        (dict(GOOD_FIELDS, salary=-5), "above zero"),
        (dict(GOOD_FIELDS, start_date="next tuesday"), "YYYY-MM-DD"),
    ]
    for fields, expected in cases:
        outcome = step.run(context_for(store, fields, today_override="2026-09-25"))
        assert not outcome.ok, fields
        assert expected in outcome.message, (expected, outcome.message)


def test_validation_never_asks_for_a_retry(store):
    # The input cannot change between attempts, so three attempts produce the
    # same answer three times more slowly.
    step = REGISTRY.get("validate")
    assert not step.retryable
    outcome = step.run(context_for(store, {}, today_override="2026-09-25"))
    assert outcome.failure_kind.value == "permanent"


def test_validation_accepts_a_good_request(store):
    outcome = REGISTRY.get("validate").run(
        context_for(store, GOOD_FIELDS, today_override="2026-09-25"))
    assert outcome.ok


def test_running_a_side_effecting_step_twice_does_it_once(store):
    context = context_for(store, GOOD_FIELDS)
    step = REGISTRY.get("create_account")

    first = step.run(context)
    second = step.run(context)

    assert first.ok and second.ok
    assert first.output["account_id"] == second.output["account_id"]
    assert len(store.list_side_effects(context.run_id)) == 1
    assert "already done" in second.message


def test_a_transient_service_failure_is_reported_as_transient(store):
    FAILURES.reset()
    context = context_for(store, GOOD_FIELDS, fail_identity_times=1)
    outcome = REGISTRY.get("create_account").run(context)
    assert not outcome.ok
    assert outcome.failure_kind.value == "transient"


def test_a_bad_input_failure_is_reported_as_permanent(store):
    context = context_for(store, dict(GOOD_FIELDS, email="not-an-email"))
    outcome = REGISTRY.get("create_account").run(context)
    assert not outcome.ok
    assert outcome.failure_kind.value == "permanent"


def test_approval_is_only_needed_above_the_limit(store):
    class Settings:
        approval_required_above = 2000.0

    step = REGISTRY.get("order_equipment")

    cheap = context_for(store, dict(GOOD_FIELDS, equipment=["headset"]))
    cheap.settings = Settings()
    needed, _, _ = step.needs_approval(cheap)
    assert not needed

    dear = context_for(store, dict(GOOD_FIELDS, equipment=["laptop", "desk", "chair"]))
    dear.settings = Settings()
    needed, question, detail = step.needs_approval(dear)
    assert needed
    assert detail["total"] > 2000.0
    assert question != ""


def test_external_ids_are_derived_not_generated():
    # The same input gives the same id, which is what lets a resumed step ask
    # again without creating a second account.
    FAILURES.reset()
    first = IDENTITY.create_account("ada@example.com", "Ada")
    second = IDENTITY.create_account("ada@example.com", "Ada")
    assert first["account_id"] == second["account_id"]


def test_a_failure_budget_runs_out():
    FAILURES.reset()
    for _ in range(2):
        try:
            LICENCES.provision("ACC-1", ["ide"], fail_times=2)
            raise AssertionError("should have failed")
        except ExternalServiceError:
            pass
    assert LICENCES.provision("ACC-1", ["ide"], fail_times=2)["order_id"] != ""


def test_payroll_refuses_a_nonsense_salary_permanently():
    FAILURES.reset()
    try:
        PAYROLL.enrol("ACC-1", -1, "2026-11-03")
        raise AssertionError("should have failed")
    except ExternalServiceError as error:
        assert not error.transient
