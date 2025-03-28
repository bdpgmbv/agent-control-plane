"""
LAYER 4 - TOOL 2: LOOK UP AN ORDER
==================================
The tool a support agent reaches for most, and the first place a data leak
would happen.

Everything interesting is in load_order_if_permitted() in base.py: the ownership
check runs inside the tool, against the database, every time - not in the prompt,
where a customer could argue with it.
"""

from support_agent.layer2_models.schemas import ToolResult, ToolRisk
from support_agent.layer4_tools.base import Tool, ToolContext


class GetOrderTool(Tool):
    name = "get_order"
    description = (
        "Look up one of the customer's own orders by its id, for example ORD-10023. "
        "Returns the status, tracking number, expected delivery date and items. "
        "Use this whenever the customer asks where an order is."
    )
    risk = ToolRisk.READ
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {
                "type": "string",
                "description": "The order id, in the form ORD-12345.",
            }
        },
        "required": ["order_id"],
        "additionalProperties": False,
    }

    def run(self, arguments: dict, context: ToolContext) -> ToolResult:
        order_id = arguments["order_id"].upper()

        order, refusal = self.load_order_if_permitted(order_id, context)
        if refusal is not None:
            return refusal

        item_names: list[str] = []
        for item in order["items"]:
            item_names.append("%d x %s" % (item["quantity"], item["name"]))

        return ToolResult(
            ok=True,
            data={
                "order_id": order["order_id"],
                "status": order["status"],
                "items": item_names,
                "total_amount": order["total_amount"],
                "currency": order["currency"],
                "placed_at": order["placed_at"][:10],
                "expected_delivery": order["expected_delivery"],
                "delivered_at": order["delivered_at"],
                "tracking_number": order["tracking_number"],
                "carrier": order["carrier"],
            },
        )


class ListMyOrdersTool(Tool):
    """
    A companion to the one above.

    Without it, a customer who does not remember their order number is stuck, and
    the agent starts guessing order ids - which then fail the ownership check and
    fill the audit log with false alarms.
    """

    name = "list_my_orders"
    description = (
        "List the customer's recent orders with their ids and statuses. Use this "
        "when the customer does not give an order number."
    )
    risk = ToolRisk.READ
    parameters = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}

    def run(self, arguments: dict, context: ToolContext) -> ToolResult:
        orders = context.database.list_orders_for_customer(context.customer_id)

        summaries: list[dict] = []
        for order in orders:
            first_item = "items"
            if len(order["items"]) > 0:
                first_item = order["items"][0]["name"]

            summaries.append(
                {
                    "order_id": order["order_id"],
                    "status": order["status"],
                    "total_amount": order["total_amount"],
                    "placed_at": order["placed_at"][:10],
                    "summary": first_item,
                }
            )

        return ToolResult(ok=True, data={"orders": summaries, "count": len(summaries)})
