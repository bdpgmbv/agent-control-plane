"""
LAYER 6 - THE ENGINE
====================
    claim a step
    if it needs a human and nobody has answered -> park the run, stop
    run it
    write down what happened
    make the next step available

That is the whole loop, and every line of it commits to the database before the
next one is attempted. The engine holds nothing between steps. Kill it anywhere
and the only thing lost is the work of the step that was in flight - which is
recoverable, because the step is idempotent and its lease will expire.

WHY THERE IS NO `for step in steps:`
------------------------------------
The obvious way to write a workflow engine is a loop over the step list, keeping
the position in a variable. That works until the process stops existing, and then
the position is gone and there is no way to work out where it got to except by
guessing from side effects.

So there is no position variable. `tick()` asks the database for one claimable
step, does it, and returns. Called again it asks again. The sequence lives in
the `position` column and the READY/BLOCKED states, which means the sequence
survives the process - and it also means two workers can run and the work simply
divides between them, with no coordination beyond the claim.

`tick()` returning False means "nothing to do right now", not "finished". A run
parked on an approval is not finished; it has nothing to do until a human acts.
"""

import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from enterprise_workflow.layer2_models.schemas import (
    ApprovalState,
    FailureKind,
    RunState,
    StepOutcome,
    StepState,
    now_utc,
)
from enterprise_workflow.layer5_steps.step1_base import StepContext


@dataclass
class EngineSettings:
    """Passed in rather than imported, so tests can vary them without .env."""

    lease_seconds: float = 30.0
    max_attempts: int = 3
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 30.0
    approval_required_above: float = 2000.0
    approval_expiry_hours: float = 72.0


@dataclass
class TickResult:
    did_work: bool = False
    run_id: str = ""
    step_name: str = ""
    what: str = ""          # "succeeded" | "retrying" | "failed" | "parked" | ...
    detail: str = ""
    notes: list[str] = field(default_factory=list)


def backoff_seconds(attempt: int, base: float, ceiling: float) -> float:
    """
    Exponential, and capped.

    Capped because an uncapped backoff on attempt 12 is a wait measured in
    hours, which stops being a retry and becomes an outage nobody has noticed.
    """
    delay = base * (2 ** max(0, attempt - 1))
    if delay > ceiling:
        return ceiling
    return delay


class Engine:
    def __init__(self, store, registry, workflows: dict, settings: EngineSettings,
                 client=None, worker_id: str = "", compensator=None) -> None:
        self.store = store
        self.registry = registry
        self.workflows = workflows
        self.settings = settings
        self.client = client
        self.worker_id = worker_id or ("worker-" + uuid.uuid4().hex[:6])

        # Injected rather than imported, so the engine does not depend on the
        # layer above it. Whoever assembles the system decides whether a failed
        # run gets cleaned up automatically; the engine only needs to know
        # whether it was given something to call.
        self.compensator = compensator

    # ---------------------------------------------------------------- starting

    def start(self, workflow_name: str, run_input: dict):
        step_names = self.workflows.get(workflow_name)
        if step_names is None:
            raise ValueError("there is no workflow called %r" % workflow_name)
        run = self.store.create_run(workflow_name, run_input, step_names)
        self.store.set_run_state(run.run_id, RunState.RUNNING)
        return self.store.get_run(run.run_id)

    # ---------------------------------------------------------------- one unit

    def tick(self) -> TickResult:
        """
        Do one step, or discover there is nothing to do.

        Housekeeping runs first, because an expired lease is the mechanism by
        which a crashed worker's step becomes available again, and an engine
        that only ever looked for READY steps would never notice one.
        """
        self.store.release_expired_leases()
        self.store.expire_approvals()
        self.resume_approved_runs()
        if self.process_cancellations() > 0:
            return TickResult(did_work=True, what="cancelled_runs")

        step_record = self.store.claim_next_step(self.worker_id, self.settings.lease_seconds)
        if step_record is None:
            return TickResult(did_work=False, what="idle")

        run = self.store.get_run(step_record.run_id)
        if run is None:
            return TickResult(did_work=False, what="idle")

        # Somebody asked for this to stop. Between steps is the only safe place
        # to honour that: interrupting a step that is mid-call is how you end up
        # with an account created and nothing recording it.
        if run.cancel_requested:
            self.store.set_step_state(run.run_id, step_record.step_name, StepState.READY)
            self.cancel_run(run.run_id)
            return TickResult(did_work=True, run_id=run.run_id,
                              step_name=step_record.step_name, what="cancelled",
                              detail="a cancellation was requested")

        step = self.registry.get(step_record.step_name)
        if step is None:
            self.fail_step_permanently(
                run, step_record.step_name, step_record.position,
                "there is no step registered called %r" % step_record.step_name)
            return TickResult(did_work=True, run_id=run.run_id,
                              step_name=step_record.step_name, what="failed",
                              detail="unknown step")

        context = StepContext(
            run_id=run.run_id, workflow_input=run.input, context=run.context,
            store=self.store, client=self.client, settings=self.settings)

        # ---- does a human have to answer first? ----
        parked = self.handle_approval(run, step_record, step, context)
        if parked is not None:
            return parked

        if not self.store.start_step(run.run_id, step_record.step_name, self.worker_id):
            # The lease was taken between the claim and here. Not an error.
            return TickResult(did_work=False, what="lost_claim",
                              run_id=run.run_id, step_name=step_record.step_name)

        outcome = self.execute(step, context, step_record.step_name, run.run_id)

        if outcome.ok:
            return self.finish_step(run, step_record, outcome)
        return self.handle_failure(run, step_record, outcome)

    # ---------------------------------------------------------------- running

    def execute(self, step, context: StepContext, step_name: str,
                run_id: str) -> StepOutcome:
        """
        Run the step, and turn anything it throws into an outcome.

        A step that raises must not take the worker down with it. If it did, one
        badly written step would stop every OTHER run on that worker - and the
        whole point of putting the state in a database is that one failure stays
        local to the thing that failed.
        """
        try:
            return step.run(context)
        except Exception as error:
            self.store.add_event(run_id, "step.raised", step_name, {
                "error": "%s: %s" % (type(error).__name__, error),
                "traceback": traceback.format_exc()[-1500:],
            })
            # An unexpected exception is treated as transient: it may be a bug,
            # but it may equally be a blip, and the attempt limit stops the
            # difference mattering for long.
            return StepOutcome.transient_failure(
                "%s: %s" % (type(error).__name__, error))

    def finish_step(self, run, step_record, outcome: StepOutcome) -> TickResult:
        run_id = run.run_id
        step_name = step_record.step_name

        self.store.set_step_state(run_id, step_name, StepState.SUCCEEDED,
                                  output=outcome.output)

        # The step's output becomes visible to later steps. Flattened on
        # purpose: a step asks for "account_id", not "the output of step 3".
        additions = dict(outcome.output or {})
        if "account_id" in additions:
            additions["account_id"] = additions["account_id"]
        self.store.merge_run_context(run_id, additions)

        self.store.add_event(run_id, "step.succeeded", step_name,
                             {"message": outcome.message})

        next_step = self.store.unblock_next_step(run_id, step_record.position)
        if next_step == "":
            self.store.set_run_state(run_id, RunState.SUCCEEDED)
            self.store.add_event(run_id, "run.succeeded")
            return TickResult(did_work=True, run_id=run_id, step_name=step_name,
                              what="run_succeeded", detail=outcome.message)

        return TickResult(did_work=True, run_id=run_id, step_name=step_name,
                          what="succeeded", detail=outcome.message)

    # ---------------------------------------------------------------- failing

    def handle_failure(self, run, step_record, outcome: StepOutcome) -> TickResult:
        run_id = run.run_id
        step_name = step_record.step_name
        step = self.registry.get(step_name)

        kind = outcome.failure_kind or FailureKind.TRANSIENT
        attempts = step_record.attempts + 1   # start_step already counted this one

        retryable = (kind == FailureKind.TRANSIENT
                     and step is not None and step.retryable
                     and attempts < self.settings.max_attempts)

        if retryable:
            delay = backoff_seconds(attempts, self.settings.retry_base_seconds,
                                    self.settings.retry_max_seconds)
            next_at = (now_utc() + timedelta(seconds=delay)).isoformat()
            self.store.set_step_state(run_id, step_name, StepState.READY,
                                      error=outcome.message,
                                      failure_kind=kind.value,
                                      next_attempt_at=next_at)
            self.store.add_event(run_id, "step.retrying", step_name, {
                "attempt": attempts, "of": self.settings.max_attempts,
                "in_seconds": round(delay, 2), "error": outcome.message})
            return TickResult(did_work=True, run_id=run_id, step_name=step_name,
                              what="retrying",
                              detail="attempt %d of %d failed, trying again in %.0fs: %s"
                                     % (attempts, self.settings.max_attempts, delay,
                                        outcome.message))

        reason = outcome.message
        if kind == FailureKind.TRANSIENT and step is not None and step.retryable:
            reason = ("gave up after %d attempts: %s" % (attempts, outcome.message))

        self.fail_step_permanently(run, step_name, step_record.position, reason,
                                   kind.value)
        return TickResult(did_work=True, run_id=run_id, step_name=step_name,
                          what="failed", detail=reason)

    def fail_step_permanently(self, run, step_name: str, position: int,
                              reason: str, kind: str = "permanent") -> None:
        self.store.set_step_state(run.run_id, step_name, StepState.FAILED,
                                  error=reason, failure_kind=kind)
        self.store.add_event(run.run_id, "step.failed", step_name,
                             {"reason": reason, "kind": kind})
        self.end_run_badly(run.run_id, RunState.FAILED, reason, step_name)

    def end_run_badly(self, run_id: str, state: RunState, reason: str,
                      step_name: str = "") -> None:
        """
        The only way a run ends in a bad state, so cleanup cannot be forgotten.

        There used to be three: a step failing, an approval being rejected, and
        a cancellation. Only the first one cleaned up, so a REJECTED run left
        behind the directory account that had been created two steps earlier -
        a person who does not work here, with a live account, and nothing
        anywhere saying so. The scenario suite found it; nothing in the happy
        path ever would.
        """
        self.store.set_run_state(run_id, state, error=reason)
        self.store.add_event(run_id, "run." + state.value, step_name,
                             {"reason": reason})

        if self.compensator is None:
            return

        outstanding = 0
        for effect in self.store.list_side_effects(run_id):
            if not effect.compensated:
                outstanding = outstanding + 1
        if outstanding > 0:
            self.compensator.compensate_run(run_id)

    # ---------------------------------------------------------------- approvals

    def handle_approval(self, run, step_record, step, context) -> TickResult | None:
        """
        Park the run if this step needs a person, and nobody has answered.

        Returns a TickResult when the run was parked or resumed by a decision,
        and None when the step may simply proceed.
        """
        existing = self.store.pending_approval_for(run.run_id, step_record.step_name)
        if existing is not None:
            # Still waiting. Put the step back and leave the run parked.
            self.store.set_step_state(run.run_id, step_record.step_name, StepState.BLOCKED)
            return TickResult(did_work=False, run_id=run.run_id,
                              step_name=step_record.step_name, what="waiting",
                              detail="waiting for a human to answer")

        decided = self.latest_decision(run.run_id, step_record.step_name)
        if decided is not None:
            if decided.state == ApprovalState.APPROVED:
                return None                      # go ahead
            if decided.state == ApprovalState.REJECTED:
                self.store.set_step_state(run.run_id, step_record.step_name,
                                          StepState.FAILED,
                                          error="a human rejected this: " + decided.note,
                                          failure_kind="needs_human")
                self.store.add_event(run.run_id, "step.rejected", step_record.step_name,
                                     {"by": decided.decided_by, "note": decided.note})
                self.end_run_badly(run.run_id, RunState.FAILED,
                                   "rejected by %s" % decided.decided_by,
                                   step_record.step_name)
                return TickResult(did_work=True, run_id=run.run_id,
                                  step_name=step_record.step_name, what="rejected",
                                  detail=decided.note or "rejected")
            if decided.state == ApprovalState.EXPIRED:
                self.store.set_step_state(run.run_id, step_record.step_name,
                                          StepState.FAILED,
                                          error="nobody answered in time",
                                          failure_kind="needs_human")
                self.end_run_badly(run.run_id, RunState.FAILED,
                                   "the approval expired unanswered",
                                   step_record.step_name)
                return TickResult(did_work=True, run_id=run.run_id,
                                  step_name=step_record.step_name, what="expired",
                                  detail="the approval expired unanswered")

        needed, question, detail = step.needs_approval(context)
        if not needed:
            return None

        self.store.create_approval(run.run_id, step_record.step_name, question,
                                   detail, self.settings.approval_expiry_hours)
        self.store.set_step_state(run.run_id, step_record.step_name, StepState.BLOCKED)
        self.store.set_run_state(run.run_id, RunState.WAITING_APPROVAL)

        return TickResult(did_work=True, run_id=run.run_id,
                          step_name=step_record.step_name, what="parked",
                          detail=question)

    def latest_decision(self, run_id: str, step_name: str):
        latest = None
        for approval in self.store.list_approvals(run_id=run_id):
            if approval.step_name != step_name:
                continue
            if approval.state == ApprovalState.PENDING:
                continue
            latest = approval
        return latest

    def resume_approved_runs(self) -> int:
        """
        Wake up runs whose approval has been answered.

        A decision arrives on the API thread, hours or days after the engine
        parked the run. Nothing pushes that decision to a worker; the worker
        notices it on its next tick. That is deliberate - a push would mean the
        worker that parked the run has to still exist to receive it, which for a
        three-day wait is exactly the assumption that does not hold.
        """
        woken = 0
        for run in self.store.list_runs(limit=200, state=RunState.WAITING_APPROVAL.value):
            for approval in self.store.list_approvals(run_id=run.run_id):
                if approval.state == ApprovalState.PENDING:
                    continue
                step = self.store.get_step(run.run_id, approval.step_name)
                if step is None or step.state != StepState.BLOCKED:
                    continue
                self.store.set_step_state(run.run_id, approval.step_name, StepState.READY)
                self.store.set_run_state(run.run_id, RunState.RUNNING)
                self.store.add_event(run.run_id, "run.resumed", approval.step_name,
                                     {"after": approval.state.value})
                woken = woken + 1
        return woken

    # ---------------------------------------------------------------- cancel

    def cancel_run(self, run_id: str) -> None:
        self.end_run_badly(run_id, RunState.CANCELLED, "cancelled by request")

    def process_cancellations(self) -> int:
        """
        Finish runs that somebody asked to stop.

        This has to be housekeeping rather than something noticed while claiming
        a step, because the claim query deliberately skips cancelled runs - so a
        cancelled run has nothing claimable and would never be looked at again.
        It sat in RUNNING for ever, with whatever it had already created still
        out there. The scenario suite caught it; the interface would have shown
        a run that simply never moved.
        """
        cancelled = 0
        for state in (RunState.PENDING, RunState.RUNNING, RunState.WAITING_APPROVAL):
            for run in self.store.list_runs(limit=200, state=state.value):
                if not run.cancel_requested:
                    continue
                self.cancel_run(run.run_id)
                cancelled = cancelled + 1
        return cancelled

    # ---------------------------------------------------------------- driving

    def run_until_idle(self, limit: int = 500) -> list[TickResult]:
        """
        Tick until there is nothing to do. Used by tests and the interface.

        A real deployment runs `work_forever` in a separate process. This exists
        so that a test can advance a workflow deterministically, without sleeping
        and hoping.
        """
        results = []
        for _ in range(limit):
            result = self.tick()
            results.append(result)
            if not result.did_work:
                break
        return results

    def work_forever(self, poll_seconds: float = 0.5, stop=None) -> None:
        while True:
            if stop is not None and stop():
                return
            result = self.tick()
            if not result.did_work:
                time.sleep(poll_seconds)
