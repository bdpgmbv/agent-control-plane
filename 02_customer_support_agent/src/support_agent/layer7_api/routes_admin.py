"""
LAYER 7 - API: THE HUMAN SIDE
=============================
Everything a support agent needs and a customer must not have: the ticket queue,
the approval queue, and the audit log.

THE APPROVAL ENDPOINT IS THE IMPORTANT ONE
    Approving does not "mark it done". It re-runs the held action from the top,
    through the executor, with every other check still in force. A refund
    approved on Tuesday is not issued on Thursday if the order was refunded some
    other way in between.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from support_agent.layer0_shared.logging_setup import get_logger, log_event
from support_agent.layer0_shared.metrics import metrics
from support_agent.layer1_config.settings import ApiKeyRecord
from support_agent.layer2_models.schemas import ApprovalRequest, AuditRecord, Message, ToolCall
from support_agent.layer3_storage.database import get_database
from support_agent.layer4_tools.base import ToolContext
from support_agent.layer4_tools.executor import ToolExecutor
from support_agent.layer4_tools.registry import describe_all
from support_agent.layer7_api.security import require_caller, require_human_agent

router = APIRouter()
log = get_logger(__name__)


def now_text() -> str:
    return datetime.now(UTC).isoformat()


@router.get("/api/tools")
async def list_tools() -> list[dict]:
    """Every tool the agent has, and how risky each one is."""
    return describe_all()


@router.get("/api/tickets")
async def list_tickets(caller: ApiKeyRecord = Depends(require_caller)) -> list[dict]:
    database = get_database()
    if caller.is_human_agent():
        return database.list_tickets()
    return database.list_tickets(caller.customer_id)


@router.get("/api/approvals")
async def list_approvals(
    status: str = "",
    caller: ApiKeyRecord = Depends(require_human_agent),
) -> list[dict]:
    """The approval queue. Support staff only."""
    return get_database().list_approvals(status)


@router.post("/api/approvals/decide")
async def decide_approval(
    request: ApprovalRequest,
    caller: ApiKeyRecord = Depends(require_human_agent),
) -> dict:
    """
    Approve or reject a held action.

    On approval the tool runs again from the top with human approval attached.
    That is why this is short: all the safety is still in the tool.
    """
    database = get_database()
    approval = database.get_approval(request.approval_id)

    if approval is None:
        raise HTTPException(status_code=404, detail="No such approval.")

    if approval["status"] != "pending":
        # Guards against two people clicking approve at the same time.
        raise HTTPException(
            status_code=409,
            detail="That approval has already been %s." % approval["status"],
        )

    decided_by = caller.role + ":" + caller.key

    # --- rejection ---
    if not request.approve:
        database.decide_approval(request.approval_id, False, decided_by, now_text(), request.note)
        metrics.increment("approvals_rejected_total")

        database.append_audit(
            AuditRecord(
                conversation_id=approval["conversation_id"],
                actor=decided_by,
                customer_id=approval["customer_id"],
                action="approval_rejected",
                tool_name=approval["tool_name"],
                risk="write_high",
                allowed=True,
                outcome="rejected",
                detail=request.note,
            )
        )

        message = (
            "A colleague has reviewed your request and is not able to approve it. "
            "%s" % request.note
        ).strip()
        database.append_message(approval["conversation_id"], Message(role="assistant", content=message))

        return {"approval_id": request.approval_id, "status": "rejected", "message": message}

    # --- approval: run the held action for real ---
    executor = ToolExecutor(database)
    context = ToolContext(
        caller=caller,
        customer_id=approval["customer_id"],
        conversation_id=approval["conversation_id"],
        database=database,
        approved_by_human=decided_by,
    )

    call = ToolCall(
        call_id="approval_" + request.approval_id,
        tool_name=approval["tool_name"],
        arguments=approval["arguments"],
    )

    result, trace = executor.execute(call, context, [approval["tool_name"]])

    database.decide_approval(request.approval_id, True, decided_by, now_text(), request.note)
    metrics.increment("approvals_granted_total")

    database.append_audit(
        AuditRecord(
            conversation_id=approval["conversation_id"],
            actor=decided_by,
            customer_id=approval["customer_id"],
            action="approval_granted",
            tool_name=approval["tool_name"],
            risk="write_high",
            allowed=True,
            outcome=trace.outcome,
            detail=trace.summary,
        )
    )

    if result.ok:
        message = (
            "Good news: a colleague has approved this. %s"
            % describe_approved_result(approval["tool_name"], result.data)
        )
    else:
        # The approval was given, but a check that is not about the amount still
        # refused it. The customer is told the truth.
        message = (
            "A colleague approved this, but it could not be completed: %s"
            % result.error_message
        )

    database.append_message(approval["conversation_id"], Message(role="assistant", content=message))

    log_event(
        log,
        "approval.decided",
        approval_id=request.approval_id,
        approved=True,
        outcome=trace.outcome,
    )

    return {
        "approval_id": request.approval_id,
        "status": "approved",
        "tool_outcome": trace.outcome,
        "result": result.model_dump(),
        "message": message,
    }


def describe_approved_result(tool_name: str, data: dict) -> str:
    if tool_name == "issue_refund":
        return (
            "Your refund of %.2f has been issued, reference %s. It takes 5 to 10 "
            "business days to reach your account."
            % (data.get("amount", 0.0), data.get("refund_id", ""))
        )
    return "The action has been completed."


@router.get("/api/audit")
async def read_audit(
    conversation_id: str = "",
    limit: int = 100,
    caller: ApiKeyRecord = Depends(require_human_agent),
) -> list[dict]:
    """
    The audit log. Support staff only, because it records what everyone did.
    """
    return get_database().read_audit(conversation_id, limit)
