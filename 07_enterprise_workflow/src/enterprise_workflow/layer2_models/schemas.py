"""
LAYER 2 - DATA SHAPES
=====================
The objects every layer agrees on, and the two state machines that drive
everything else.

A RUN is one workflow in flight. A STEP RUN is one attempt at one step of it.
Keeping those separate is what makes retries and resumption expressible: a step
that failed twice and then succeeded has three step runs and one outcome, and
both of those facts are worth keeping.

    RunState:   PENDING -> RUNNING -> (WAITING_APPROVAL) -> SUCCEEDED
                                   -> FAILED -> COMPENSATING -> COMPENSATED
                                   -> CANCELLED

    StepState:  READY -> CLAIMED -> RUNNING -> SUCCEEDED
                                            -> FAILED -> READY   (retry)
                                            -> FAILED (permanent)
                                  -> BLOCKED (waiting on an approval)
                                  -> SKIPPED (a condition said it was not needed)

Every transition is written to the database before the next one is attempted.
That is the whole design: the process holds nothing that matters. Kill it
between any two lines and the database still describes exactly where the
workflow got to.
"""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_text() -> str:
    return now_utc().isoformat()


class RunState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    CANCELLED = "cancelled"

    def is_finished(self) -> bool:
        return self in (RunState.SUCCEEDED, RunState.FAILED,
                        RunState.COMPENSATED, RunState.CANCELLED)


class StepState(str, Enum):
    READY = "ready"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    COMPENSATED = "compensated"

    def is_finished(self) -> bool:
        return self in (StepState.SUCCEEDED, StepState.SKIPPED,
                        StepState.COMPENSATED)


class FailureKind(str, Enum):
    """
    Why a step failed, which decides whether retrying it can possibly help.

    This distinction is the difference between a workflow that recovers and one
    that hammers a broken request three times and then gives up three times
    slower. A timeout might work next time. A malformed start date will not.
    """

    TRANSIENT = "transient"       # the network, a busy service, a timeout
    PERMANENT = "permanent"       # bad input, a rule violation, a 404
    NEEDS_HUMAN = "needs_human"   # nothing automatic can resolve it


class StepOutcome(BaseModel):
    """What one attempt at a step produced."""

    ok: bool = False
    output: dict = Field(default_factory=dict)
    message: str = ""
    failure_kind: FailureKind | None = None

    @classmethod
    def success(cls, output: dict | None = None, message: str = "") -> "StepOutcome":
        return cls(ok=True, output=output or {}, message=message)

    @classmethod
    def transient_failure(cls, message: str) -> "StepOutcome":
        return cls(ok=False, message=message, failure_kind=FailureKind.TRANSIENT)

    @classmethod
    def permanent_failure(cls, message: str) -> "StepOutcome":
        return cls(ok=False, message=message, failure_kind=FailureKind.PERMANENT)

    @classmethod
    def needs_human(cls, message: str) -> "StepOutcome":
        return cls(ok=False, message=message, failure_kind=FailureKind.NEEDS_HUMAN)


class StepRecord(BaseModel):
    """One step of one run, as the database holds it."""

    run_id: str
    step_name: str
    position: int
    state: StepState = StepState.READY
    attempts: int = 0
    output: dict = Field(default_factory=dict)
    error: str = ""
    failure_kind: str = ""

    # Which worker holds it, and until when. Empty means nobody.
    claimed_by: str = ""
    lease_expires_at: str = ""

    # When a retry may next be attempted. Empty means "now".
    next_attempt_at: str = ""

    created_at: str = Field(default_factory=now_text)
    updated_at: str = Field(default_factory=now_text)

    def is_available(self) -> bool:
        return self.state == StepState.READY


class Run(BaseModel):
    """One workflow in flight."""

    run_id: str
    workflow_name: str
    state: RunState = RunState.PENDING
    input: dict = Field(default_factory=dict)
    context: dict = Field(default_factory=dict)   # accumulated step outputs
    error: str = ""
    created_at: str = Field(default_factory=now_text)
    updated_at: str = Field(default_factory=now_text)
    finished_at: str = ""

    # Set when a human has cancelled it. The engine checks this between steps
    # rather than trying to interrupt a step that is already running - killing
    # work mid-flight is how you get half-created accounts.
    cancel_requested: bool = False


class ApprovalState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class Approval(BaseModel):
    """A question the workflow is parked on until a person answers it."""

    approval_id: str
    run_id: str
    step_name: str
    question: str
    detail: dict = Field(default_factory=dict)
    state: ApprovalState = ApprovalState.PENDING
    decided_by: str = ""
    decided_at: str = ""
    note: str = ""
    expires_at: str = ""
    created_at: str = Field(default_factory=now_text)


class Event(BaseModel):
    """
    One line of the run's history, append-only.

    This is not logging. It is the answer to "what happened to this workflow,
    in order, including the parts that failed" - and on a process that can be
    killed and restarted, reconstructing that from logs across several worker
    lifetimes is not something anyone should have to do.
    """

    event_id: int | None = None
    run_id: str
    step_name: str = ""
    kind: str = ""
    detail: dict = Field(default_factory=dict)
    at: str = Field(default_factory=now_text)


class SideEffect(BaseModel):
    """
    A record that something irreversible happened in the outside world.

    An account was created, a licence was bought, somebody was enrolled in
    payroll. This table is what makes a step idempotent: before doing the thing,
    the step asks whether this exact effect has already been recorded, and the
    uniqueness of `idempotency_key` is enforced by the database rather than by
    the step remembering to check.
    """

    effect_id: int | None = None
    run_id: str
    step_name: str
    idempotency_key: str
    kind: str
    detail: dict = Field(default_factory=dict)
    compensated: bool = False
    at: str = Field(default_factory=now_text)


# =============================================================
#  API shapes
# =============================================================

class StartRunRequest(BaseModel):
    workflow_name: str = "employee_onboarding"
    input: dict = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    approved: bool
    decided_by: str = "manager"
    note: str = ""


class RunView(BaseModel):
    """A run and everything about it, for the interface."""

    run: Run
    steps: list[StepRecord] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    side_effects: list[SideEffect] = Field(default_factory=list)
