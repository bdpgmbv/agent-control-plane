"""
LAYER 4 - TOOLS: THE REGISTRY
=============================
The list of tools that exist, and which ones a given intent is allowed to use.

WHY THE TOOL LIST CHANGES WITH THE INTENT
    A model offered every tool will sometimes reach for the wrong one. The
    cheapest fix is not to offer it. A customer asking "how long does delivery
    take?" has no business triggering issue_refund, so during that conversation
    turn the refund tool is not in the list the model can even see.

    This is defence in depth, not the main defence - the tool still checks
    permissions itself. But it removes a whole class of mistake before it starts.
"""

from support_agent.layer2_models.schemas import Intent
from support_agent.layer4_tools.base import Tool
from support_agent.layer4_tools.step1_search_help_articles import SearchHelpArticlesTool
from support_agent.layer4_tools.step2_get_order import GetOrderTool, ListMyOrdersTool
from support_agent.layer4_tools.step3_get_refund_status import GetRefundStatusTool
from support_agent.layer4_tools.step4_create_ticket import CreateTicketTool
from support_agent.layer4_tools.step5_issue_refund import IssueRefundTool

ALL_TOOLS: list[Tool] = [
    SearchHelpArticlesTool(),
    GetOrderTool(),
    ListMyOrdersTool(),
    GetRefundStatusTool(),
    CreateTicketTool(),
    IssueRefundTool(),
]

# Which tools each intent may use. Anything not listed is not offered.
TOOLS_BY_INTENT: dict[Intent, list[str]] = {
    Intent.ORDER_STATUS: ["get_order", "list_my_orders", "search_help_articles", "create_ticket"],
    Intent.DELIVERY_PROBLEM: ["get_order", "list_my_orders", "search_help_articles", "create_ticket"],
    Intent.REFUND_REQUEST: [
        "get_order", "list_my_orders", "get_refund_status",
        "search_help_articles", "issue_refund", "create_ticket",
    ],
    Intent.RETURN_POLICY: ["search_help_articles", "get_order", "create_ticket"],
    Intent.BILLING: ["get_order", "list_my_orders", "get_refund_status", "search_help_articles", "create_ticket"],
    Intent.COMPLAINT: ["get_order", "list_my_orders", "search_help_articles", "create_ticket"],
    Intent.TECHNICAL: ["search_help_articles", "get_order", "create_ticket"],
    Intent.SMALL_TALK: [],
    Intent.ABUSE: [],
    Intent.UNKNOWN: ["search_help_articles", "list_my_orders", "create_ticket"],
}


def find_tool(name: str) -> Tool | None:
    for tool in ALL_TOOLS:
        if tool.name == name:
            return tool
    return None


def tools_for_intent(intent: Intent) -> list[Tool]:
    """The tools this intent is allowed to use, in registry order."""
    allowed_names = TOOLS_BY_INTENT.get(intent, [])

    tools: list[Tool] = []
    for tool in ALL_TOOLS:
        if tool.name in allowed_names:
            tools.append(tool)
    return tools


def schemas_for(tools: list[Tool]) -> list[dict]:
    """The JSON the model reads."""
    schemas: list[dict] = []
    for tool in tools:
        schemas.append(tool.openai_schema())
    return schemas


def describe_all() -> list[dict]:
    """A plain listing for the UI and the /api/tools endpoint."""
    described: list[dict] = []
    for tool in ALL_TOOLS:
        described.append(
            {
                "name": tool.name,
                "description": tool.description,
                "risk": tool.risk.value,
                "needs_idempotency_key": tool.needs_idempotency_key,
                "parameters": list(tool.parameters.get("properties", {}).keys()),
            }
        )
    return described
