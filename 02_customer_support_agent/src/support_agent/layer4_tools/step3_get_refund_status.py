"""
LAYER 4 - TOOL 3: CHECK A REFUND
================================
"Where is my refund?" is one of the most common support questions, and one of
the easiest to get wrong by guessing.

Note that "no refund exists" is a SUCCESS, not a failure. It is a true and
useful answer. Returning it as an error would make the agent retry, then
apologise, then escalate - all over a question it answered correctly the first
time. Distinguishing "I could not find out" from "I found out, and the answer is
nothing" is worth being careful about.
"""

from support_agent.layer2_models.schemas import ToolResult, ToolRisk
from support_agent.layer4_tools.base import Tool, ToolContext


class GetRefundStatusTool(Tool):
    name = "get_refund_status"
    description = (
        "Check whether a refund exists for one of the customer's orders, and what "
        "stage it is at. Use this when the customer asks about a refund they have "
        "already requested."
    )
    risk = ToolRisk.READ
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "The order id, in the form ORD-12345."}
        },
        "required": ["order_id"],
        "additionalProperties": False,
    }

    def run(self, arguments: dict, context: ToolContext) -> ToolResult:
        order_id = arguments["order_id"].upper()

        order, refusal = self.load_order_if_permitted(order_id, context)
        if refusal is not None:
            return refusal

        refund = context.database.get_refund_for_order(order_id)

        if refund is None:
            return ToolResult(
                ok=True,
                data={
                    "has_refund": False,
                    "order_id": order_id,
                    "note": "No refund has been started for this order.",
                },
            )

        return ToolResult(
            ok=True,
            data={
                "has_refund": True,
                "order_id": order_id,
                "refund_id": refund["refund_id"],
                "amount": refund["amount"],
                "status": refund["status"],
                "reason": refund["reason"],
                "requested_on": refund["created_at"][:10],
                "completed_on": refund["completed_at"][:10],
            },
        )
