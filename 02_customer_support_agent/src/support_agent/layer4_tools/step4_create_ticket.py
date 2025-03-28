"""
LAYER 4 - TOOL 4: CREATE A SUPPORT TICKET
=========================================
The agent's way of saying "a person needs to look at this".

Risk level WRITE_LOW: it creates something, so it is audited, but a wrongly
created ticket costs a few minutes of somebody's time and can be closed. Compare
with issue_refund in the next file, which moves money.

Getting the risk levels right is most of the work in a safe agent. Treat
everything as high risk and you need a human for every step, which defeats the
point. Treat everything as low risk and the first bad day is expensive.
"""

import uuid
from datetime import UTC, datetime

from support_agent.layer0_shared.pii import redact
from support_agent.layer0_shared.text_tools import shorten
from support_agent.layer2_models.schemas import ToolResult, ToolRisk
from support_agent.layer4_tools.base import Tool, ToolContext

ALLOWED_CATEGORIES = ["delivery", "refund", "billing", "product", "complaint", "other"]
ALLOWED_PRIORITIES = ["low", "normal", "high", "urgent"]


class CreateTicketTool(Tool):
    name = "create_ticket"
    description = (
        "Create a support ticket for a human colleague to pick up. Use this when "
        "you cannot resolve the problem yourself, when the customer asks for a "
        "person, or when something needs manual approval."
    )
    risk = ToolRisk.WRITE_LOW
    parameters = {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "One of: delivery, refund, billing, product, complaint, other.",
            },
            "summary": {
                "type": "string",
                "description": "One or two sentences describing what the customer needs.",
            },
            "priority": {
                "type": "string",
                "description": "One of: low, normal, high, urgent. Defaults to normal.",
            },
        },
        "required": ["category", "summary"],
        "additionalProperties": False,
    }

    def run(self, arguments: dict, context: ToolContext) -> ToolResult:
        category = arguments["category"].lower().strip()
        if category not in ALLOWED_CATEGORIES:
            category = "other"

        priority = arguments.get("priority", "normal").lower().strip()
        if priority not in ALLOWED_PRIORITIES:
            priority = "normal"

        # The summary is written by the model from the customer's words, so it
        # can easily contain an email address or a card number. This row is read
        # by staff and exported to reporting tools, so it gets cleaned first.
        summary, _kinds = redact(arguments["summary"])
        summary = shorten(summary, 400)

        ticket_id = "TICK-" + uuid.uuid4().hex[:8].upper()
        created_at = datetime.now(UTC).isoformat()

        context.database.insert_ticket(
            {
                "ticket_id": ticket_id,
                "conversation_id": context.conversation_id,
                "customer_id": context.customer_id,
                "category": category,
                "priority": priority,
                "summary": summary,
                "status": "open",
                "assigned_to": "unassigned",
                "created_at": created_at,
            }
        )

        return ToolResult(
            ok=True,
            data={
                "ticket_id": ticket_id,
                "category": category,
                "priority": priority,
                "status": "open",
            },
        )
