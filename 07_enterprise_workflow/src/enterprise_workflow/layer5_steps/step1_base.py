"""
LAYER 5, STEP 1 - WHAT A STEP IS
================================
A step declares four things about itself, and the engine reads all four.

    retryable       may a failure be tried again? A timeout, yes. A start date
                    in the past, never - and retrying it costs three attempts
                    to arrive at the same answer three times more slowly.

    idempotency_key how to recognise that this exact work was already done.
                    Any step that changes the outside world must have one;
                    without it, being interrupted is the same as being asked
                    to do the work twice.

    needs_approval  whether a human has to answer before it may proceed.

    compensate      how to undo it, if a later step fails permanently.

The engine never asks a step whether it is finished, and never asks it what to
do next. It asks the database. A step's only job is to do one thing and report
what happened - which is what makes a step safe to kill.
"""

from dataclasses import dataclass, field
from typing import Protocol

from enterprise_workflow.layer2_models.schemas import StepOutcome
from enterprise_workflow.layer3_database.store import WorkflowStore


class StepSettings(Protocol):
    """
    The only setting a step is allowed to care about.

    A Protocol rather than the engine's own settings class, because the engine
    is the layer ABOVE this one and a step importing it would invert the
    dependency. This says what a step needs without saying where it comes from,
    which is also a useful constraint on what a step is permitted to know.
    """

    approval_required_above: float


@dataclass
class StepContext:
    """Everything a step is allowed to see."""

    run_id: str
    workflow_input: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)   # outputs of earlier steps
    store: WorkflowStore | None = None
    client: object = None                         # the model client, or None offline
    settings: StepSettings | None = None

    def value(self, key: str, default=None):
        """Look in the accumulated context first, then the original input."""
        if key in self.context:
            return self.context[key]
        return self.workflow_input.get(key, default)


class Step:
    """
    One unit of work.

    Subclasses override `run`, and `compensate` if the work can be undone.
    """

    name = "unnamed"
    title = ""
    retryable = False
    describes_itself_as = ""

    def run(self, context: StepContext) -> StepOutcome:
        raise NotImplementedError

    def idempotency_key(self, context: StepContext) -> str:
        """
        A stable name for the work this step is about to do.

        Stable is the whole requirement. It must be the same on a retry, on a
        resume after a crash, and on a different worker - which means it can be
        built from the run id and the input, and never from a clock, a random
        number, or anything about the process doing the work.
        """
        return ""

    def changes_the_outside_world(self) -> bool:
        return self.idempotency_key.__qualname__ != "Step.idempotency_key"

    def needs_approval(self, context: StepContext) -> tuple[bool, str, dict]:
        """Returns (needed, question, detail)."""
        return (False, "", {})

    def compensate(self, context: StepContext, effect_detail: dict) -> str:
        """
        Undo this step. Returns a description of what was undone.

        Compensation is not a rollback. The account really was created and the
        licence really was bought; what happens here is a second, opposite
        action that leaves the world in an acceptable state. A database can roll
        back a transaction. Nothing can un-send an email.
        """
        return ""

    def can_compensate(self) -> bool:
        return self.compensate.__qualname__ != "Step.compensate"


class StepRegistry:
    """Name -> Step. The workflow definition is a list of names."""

    def __init__(self) -> None:
        self.steps: dict[str, Step] = {}

    def add(self, step: Step) -> Step:
        self.steps[step.name] = step
        return step

    def get(self, name: str) -> Step | None:
        return self.steps.get(name)

    def names(self) -> list[str]:
        return list(self.steps.keys())
