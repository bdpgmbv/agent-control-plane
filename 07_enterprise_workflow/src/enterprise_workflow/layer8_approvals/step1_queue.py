"""
LAYER 8 - THE APPROVAL QUEUE
============================
What a person sees, and what happens when they answer.

The queue is thin on purpose - the store already enforces the rule that matters
(a decision can only be made once, because the UPDATE carries `state = 'pending'`
in its WHERE clause). What this layer adds is the part a person needs: the
question in context, what it will cost, how long it has been waiting, and when
it expires.

WHY A DECISION DOES NOT RESUME THE RUN HERE
-------------------------------------------
Answering an approval writes one row and returns. It does not start the next
step, and it does not notify a worker.

That is deliberate. The worker that parked this run three days ago does not
exist any more, so there is nobody to notify. The decision goes in the database,
and the next worker to tick notices it - which means the API stays fast, the
decision survives a restart of everything, and there is no path where a run is
approved but the approval never reaches anyone.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from enterprise_workflow.layer2_models.schemas import ApprovalState


def hours_between(earlier_text: str, later: datetime) -> float:
    try:
        earlier = datetime.fromisoformat(earlier_text)
    except ValueError:
        return 0.0
    if earlier.tzinfo is None:
        earlier = earlier.replace(tzinfo=UTC)
    return round((later - earlier).total_seconds() / 3600.0, 2)


@dataclass
class ApprovalCard:
    """One waiting question, with everything needed to answer it."""

    approval_id: str
    run_id: str
    step_name: str
    question: str
    detail: dict = field(default_factory=dict)
    waiting_hours: float = 0.0
    expires_in_hours: float = 0.0
    person: str = ""
    workflow_name: str = ""


@dataclass
class DecisionResult:
    ok: bool = False
    message: str = ""
    approval_id: str = ""


class ApprovalQueue:
    def __init__(self, store) -> None:
        self.store = store

    def pending(self, limit: int = 100) -> list[ApprovalCard]:
        now = datetime.now(UTC)
        cards: list[ApprovalCard] = []

        for approval in self.store.list_approvals(pending_only=True):
            run = self.store.get_run(approval.run_id)
            person = ""
            workflow_name = ""
            if run is not None:
                workflow_name = run.workflow_name
                fields = run.context.get("fields") or {}
                person = str(fields.get("full_name") or "")

            expires_in = 0.0
            if approval.expires_at != "":
                expires_in = -hours_between(approval.expires_at, now)

            cards.append(ApprovalCard(
                approval_id=approval.approval_id,
                run_id=approval.run_id,
                step_name=approval.step_name,
                question=approval.question,
                detail=approval.detail,
                waiting_hours=hours_between(approval.created_at, now),
                expires_in_hours=round(expires_in, 2),
                person=person,
                workflow_name=workflow_name,
            ))
            if len(cards) >= limit:
                break

        return cards

    def decide(self, approval_id: str, approved: bool, decided_by: str,
               note: str = "") -> DecisionResult:
        approval = self.store.get_approval(approval_id)
        if approval is None:
            return DecisionResult(ok=False, message="there is no approval with that id")

        if approval.state != ApprovalState.PENDING:
            # Two managers clicking at the same moment. The first one wins, and
            # the second is told so rather than silently overwriting it.
            return DecisionResult(
                ok=False, approval_id=approval_id,
                message="this was already %s by %s"
                        % (approval.state.value, approval.decided_by or "someone"))

        if decided_by.strip() == "":
            return DecisionResult(
                ok=False, approval_id=approval_id,
                message="a decision has to be attributed to somebody")

        recorded = self.store.decide_approval(approval_id, approved, decided_by, note)
        if not recorded:
            return DecisionResult(
                ok=False, approval_id=approval_id,
                message="somebody else answered this a moment ago")

        return DecisionResult(
            ok=True, approval_id=approval_id,
            message="recorded as %s. The next worker to look will pick the run up."
                    % ("approved" if approved else "rejected"))
