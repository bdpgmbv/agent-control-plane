"""
LAYER 5, STEP 3 - THE ONBOARDING WORKFLOW
=========================================
Eight steps, in order:

    1 intake             read the request        (agent)
    2 validate           check it                (code)
    3 create_account     directory account       (irreversible, compensable)
    4 order_equipment    may need a human        (approval gate)
    5 provision_licences licence vendor          (flaky, compensable)
    6 enrol_payroll      payroll                 (a duplicate is a second salary)
    7 write_welcome      draft the note          (agent)
    8 notify_manager     send it                 (irreversible, NOT compensable)

THE THING THAT CANNOT BE MADE ATOMIC
------------------------------------
Steps 3, 5 and 6 each do two things that must both happen or neither: they call
an outside system, and they write down that they called it. There is no way to
make those two atomic. Whatever order you choose, the process can die in the gap.

    call first, then record   -> crash in the gap means it happened and we have
                                 no record, so a resume does it AGAIN
    record first, then call   -> crash in the gap means we have a record of
                                 something that never happened

You do not solve this by being careful. You solve it by making the SECOND call
harmless - by handing the outside system a key it recognises, so that asking
twice produces one account and returns the same id both times. The record in our
database is then an optimisation, not the guarantee.

That is why every id in layer5/step2_external.py is derived from the input
rather than generated. Those services are standing in for ones that accept an
idempotency key, because a service that does not accept one cannot be used
safely in a workflow that can be interrupted - and noticing that is most of the
value of building this.

Step 8 is deliberately not compensable. Once the email is sent it is sent. The
workflow puts it last for exactly that reason: everything that might still fail
has already finished.
"""

import time
from datetime import date, datetime

from enterprise_workflow.layer2_models.schemas import StepOutcome
from enterprise_workflow.layer3_database.store import AlreadyDone
from enterprise_workflow.layer4_agents.step2_agents import (
    run_intake,
    run_policy_explanation,
    run_welcome_draft,
)
from enterprise_workflow.layer5_steps.step1_base import Step, StepContext, StepRegistry
from enterprise_workflow.layer5_steps.step2_external import (
    IDENTITY,
    LICENCES,
    PAYROLL,
    ExternalServiceError,
)

EQUIPMENT_PRICES = {
    "laptop": 1800.0,
    "monitor": 320.0,
    "desk": 450.0,
    "chair": 380.0,
    "phone": 900.0,
    "headset": 120.0,
}
DEFAULT_EQUIPMENT_PRICE = 200.0


def do_once(context: StepContext, step_name: str, key: str, kind: str, work):
    """
    Run `work` unless this exact key is already recorded.

    The shared shape of every side-effecting step. `work` must be safe to call
    twice - see the note at the top of this file about why that is a property of
    the SERVICE and not something this function can provide.
    """
    store = context.store
    if store is None:
        # A step with nowhere to record what it did cannot be made idempotent,
        # so it must not do the thing at all. Failing here is the only safe
        # answer; proceeding would create an account nothing knows about.
        raise RuntimeError(
            "step %r was given no store, so it cannot record what it does" % step_name)

    existing = store.find_side_effect(key)
    if existing is not None:
        return StepOutcome.success(
            existing.detail,
            "already done earlier in this run; reused rather than repeated")

    result = work()

    # A deliberately widened version of the gap described at the top of this
    # file: the outside world has changed, and our database does not know yet.
    # Normally this window is microseconds and you cannot aim at it. With a
    # delay you can, which is how scripts/crash_test.py kills a worker at the
    # single worst moment in the whole workflow rather than at a convenient one.
    # Zero unless a test sets it.
    gap = float(context.value("delay_before_record_seconds", 0) or 0)
    if gap > 0:
        time.sleep(gap)

    try:
        store.record_side_effect(context.run_id, step_name, key, kind, result)
    except AlreadyDone:
        # Another worker recorded it between our read and our write. Its call
        # and ours went to the same keyed service, so they produced the same
        # thing; take the recorded one and move on.
        recorded = store.find_side_effect(key)
        detail = recorded.detail if recorded is not None else result
        return StepOutcome.success(detail, "another worker did this at the same moment")

    return StepOutcome.success(result)


# ================================================================
#  1. Intake
# ================================================================

class IntakeStep(Step):
    name = "intake"
    title = "Read the request"
    retryable = True

    def run(self, context: StepContext) -> StepOutcome:
        request_text = context.value("request_text", "")
        if str(request_text).strip() == "":
            return StepOutcome.permanent_failure(
                "the request is empty, so there is nothing to read")

        result = run_intake(str(request_text), context.client)

        return StepOutcome.success(
            {"fields": result.fields, "intake_source": result.source,
             "intake_missing": result.missing},
            result.note)


# ================================================================
#  2. Validate
# ================================================================

class ValidateStep(Step):
    name = "validate"
    title = "Check the request makes sense"
    retryable = False   # the input will not change between attempts

    def run(self, context: StepContext) -> StepOutcome:
        fields = context.value("fields", {}) or {}
        problems = []

        for key in ("full_name", "email", "department", "start_date", "salary"):
            if fields.get(key) in (None, "", []):
                problems.append("%s is missing" % key.replace("_", " "))

        email = fields.get("email") or ""
        if email != "" and "@" not in str(email):
            problems.append("%r is not an email address" % email)

        start_date_text = fields.get("start_date") or ""
        if start_date_text != "":
            try:
                parsed = date.fromisoformat(str(start_date_text))
            except ValueError:
                problems.append("%r is not a date in YYYY-MM-DD form" % start_date_text)
            else:
                today = context.value("today_override", "")
                today_date = (date.fromisoformat(today) if today
                              else datetime.now().date())
                if parsed < today_date:
                    problems.append(
                        "the start date %s is in the past" % parsed.isoformat())

        salary = fields.get("salary")
        if salary is not None:
            try:
                if float(salary) <= 0:
                    problems.append("the salary must be above zero")
            except (TypeError, ValueError):
                problems.append("%r is not a salary" % salary)

        if len(problems) > 0:
            # Permanent on purpose. Retrying will read the same request and find
            # the same problems, three times more slowly.
            return StepOutcome.permanent_failure(
                "this request cannot be processed: " + "; ".join(problems))

        return StepOutcome.success({"validated": True},
                                   "every required field is present and sane")


# ================================================================
#  3. Create the account
# ================================================================

class CreateAccountStep(Step):
    name = "create_account"
    title = "Create the directory account"
    retryable = True

    def idempotency_key(self, context: StepContext) -> str:
        fields = context.value("fields", {}) or {}
        return "account:%s" % (fields.get("email") or context.run_id)

    def run(self, context: StepContext) -> StepOutcome:
        fields = context.value("fields", {}) or {}
        fail_times = int(context.value("fail_identity_times", 0) or 0)

        def work():
            return IDENTITY.create_account(
                str(fields.get("email")), str(fields.get("full_name")), fail_times)

        try:
            return do_once(context, self.name, self.idempotency_key(context),
                           "directory_account", work)
        except ExternalServiceError as error:
            if error.transient:
                return StepOutcome.transient_failure(str(error))
            return StepOutcome.permanent_failure(str(error))

    def compensate(self, context: StepContext, effect_detail: dict) -> str:
        return IDENTITY.delete_account(effect_detail.get("account_id", "unknown"))


# ================================================================
#  4. Order equipment - the approval gate
# ================================================================

class OrderEquipmentStep(Step):
    name = "order_equipment"
    title = "Order the equipment"
    retryable = True

    def total_cost(self, context: StepContext) -> tuple[float, list[str]]:
        fields = context.value("fields", {}) or {}
        items = fields.get("equipment") or ["laptop"]
        total = 0.0
        for item in items:
            total = total + EQUIPMENT_PRICES.get(str(item).lower(), DEFAULT_EQUIPMENT_PRICE)
        return round(total, 2), list(items)

    def needs_approval(self, context: StepContext) -> tuple[bool, str, dict]:
        """
        Above the limit, a person decides.

        The workflow parks here, possibly for days. That is the whole reason the
        state is in a database rather than in a variable: a process that has to
        stay alive until a manager reads their email is a process that will be
        restarted before the manager does.
        """
        total, items = self.total_cost(context)

        if context.settings is None:
            # The limit is unknown. Ask a person - the alternative is silently
            # skipping a money gate because a setting was not wired up, which
            # fails in the expensive direction.
            limit = 0.0
        else:
            limit = context.settings.approval_required_above
            if total <= limit:
                return (False, "", {})

        fields = context.value("fields", {}) or {}
        question = run_policy_explanation(
            str(fields.get("full_name")), str(fields.get("department")),
            items, total, limit, context.client)

        return (True, question, {"total": total, "items": items, "limit": limit})

    def idempotency_key(self, context: StepContext) -> str:
        return "equipment:%s" % context.run_id

    def run(self, context: StepContext) -> StepOutcome:
        total, items = self.total_cost(context)

        def work():
            return {"items": items, "total": total, "order_id": "EQ-" + context.run_id[-8:]}

        return do_once(context, self.name, self.idempotency_key(context),
                       "equipment_order", work)

    def compensate(self, context: StepContext, effect_detail: dict) -> str:
        return "cancelled equipment order %s" % effect_detail.get("order_id", "unknown")


# ================================================================
#  5. Provision licences
# ================================================================

class ProvisionLicencesStep(Step):
    name = "provision_licences"
    title = "Buy the software licences"
    retryable = True

    def idempotency_key(self, context: StepContext) -> str:
        account = context.value("account_id", "") or context.run_id
        return "licences:%s" % account

    def run(self, context: StepContext) -> StepOutcome:
        fields = context.value("fields", {}) or {}
        account_id = context.value("account_id", "")
        if account_id in ("", None):
            return StepOutcome.permanent_failure(
                "there is no account to attach licences to")

        licences = LICENCES.licences_for(str(fields.get("department")))
        fail_times = int(context.value("fail_licences_times", 0) or 0)

        def work():
            return LICENCES.provision(str(account_id), licences, fail_times)

        try:
            return do_once(context, self.name, self.idempotency_key(context),
                           "licence_order", work)
        except ExternalServiceError as error:
            if error.transient:
                return StepOutcome.transient_failure(str(error))
            return StepOutcome.permanent_failure(str(error))

    def compensate(self, context: StepContext, effect_detail: dict) -> str:
        return LICENCES.release(effect_detail.get("order_id", "unknown"))


# ================================================================
#  6. Enrol in payroll - the one where a duplicate costs money
# ================================================================

class EnrolPayrollStep(Step):
    name = "enrol_payroll"
    title = "Enrol in payroll"
    retryable = True

    def idempotency_key(self, context: StepContext) -> str:
        """
        Keyed on the account, not on the attempt.

        If this were keyed on anything that changes between attempts - a
        timestamp, a retry number, the worker's name - then every retry would
        look like new work and the person would be enrolled twice. Twice enrolled
        is twice paid, and nobody reports that bug.
        """
        account = context.value("account_id", "") or context.run_id
        return "payroll:%s" % account

    def run(self, context: StepContext) -> StepOutcome:
        fields = context.value("fields", {}) or {}
        account_id = context.value("account_id", "")
        if account_id in ("", None):
            return StepOutcome.permanent_failure("there is no account to enrol")

        fail_times = int(context.value("fail_payroll_times", 0) or 0)
        salary = float(fields.get("salary") or 0)
        start_date = str(fields.get("start_date") or "")

        def work():
            return PAYROLL.enrol(str(account_id), salary, start_date, fail_times)

        try:
            return do_once(context, self.name, self.idempotency_key(context),
                           "payroll_enrolment", work)
        except ExternalServiceError as error:
            if error.transient:
                return StepOutcome.transient_failure(str(error))
            return StepOutcome.permanent_failure(str(error))

    def compensate(self, context: StepContext, effect_detail: dict) -> str:
        return PAYROLL.remove(effect_detail.get("payroll_id", "unknown"))


# ================================================================
#  7. Write the welcome note
# ================================================================

class WriteWelcomeStep(Step):
    name = "write_welcome"
    title = "Draft the welcome note"
    retryable = True

    def run(self, context: StepContext) -> StepOutcome:
        fields = context.value("fields", {}) or {}
        note = run_welcome_draft(
            str(fields.get("full_name")), str(fields.get("department")),
            str(fields.get("start_date")), fields.get("equipment") or [],
            context.client)
        return StepOutcome.success({"welcome_note": note}, "drafted")


# ================================================================
#  8. Tell the manager - last, because it cannot be taken back
# ================================================================

class NotifyManagerStep(Step):
    name = "notify_manager"
    title = "Send the welcome note"
    retryable = True

    def idempotency_key(self, context: StepContext) -> str:
        return "notify:%s" % context.run_id

    def run(self, context: StepContext) -> StepOutcome:
        fields = context.value("fields", {}) or {}

        def work():
            return {
                "to": fields.get("manager") or "the hiring manager",
                "subject": "%s starts on %s" % (fields.get("full_name"),
                                                fields.get("start_date")),
                "sent": True,
            }

        return do_once(context, self.name, self.idempotency_key(context),
                       "notification", work)

    # No compensate(). An email cannot be unsent, which is why this step is
    # last: by the time it runs, nothing that could fail is still outstanding.


# ================================================================
#  The workflow
# ================================================================

def build_registry() -> StepRegistry:
    registry = StepRegistry()
    registry.add(IntakeStep())
    registry.add(ValidateStep())
    registry.add(CreateAccountStep())
    registry.add(OrderEquipmentStep())
    registry.add(ProvisionLicencesStep())
    registry.add(EnrolPayrollStep())
    registry.add(WriteWelcomeStep())
    registry.add(NotifyManagerStep())
    return registry


ONBOARDING_STEPS = [
    "intake", "validate", "create_account", "order_equipment",
    "provision_licences", "enrol_payroll", "write_welcome", "notify_manager",
]

WORKFLOWS = {"employee_onboarding": ONBOARDING_STEPS}
REGISTRY = build_registry()
