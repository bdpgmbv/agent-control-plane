"""
TESTS FOR LAYER 4 - the tools, and the executor that guards them.

This is the most important test file in the project. Everything here is about
the agent being unable to do something, which is harder to get right and easier
to break than making it work.
"""

from tests.conftest import all_tool_names

from support_agent.layer2_models.schemas import ToolCall
from support_agent.layer4_tools.base import ToolContext, clean_arguments, validate_arguments
from support_agent.layer4_tools.executor import build_idempotency_key, canonical_arguments


def call_tool(executor, context, name, arguments, allowed=None):
    if allowed is None:
        allowed = all_tool_names()
    return executor.execute(
        ToolCall(call_id="c1", tool_name=name, arguments=arguments), context, allowed
    )


# ---------------- argument validation ----------------

def test_a_missing_required_argument_is_reported_clearly():
    ok, message = validate_arguments({}, {"properties": {"order_id": {"type": "string"}},
                                          "required": ["order_id"]})
    assert ok is False
    assert "order_id" in message


def test_a_number_sent_as_text_is_accepted():
    """Models do this constantly. Rejecting it helps nobody."""
    schema = {"properties": {"amount": {"type": "number"}}, "required": ["amount"]}
    ok, _message = validate_arguments({"amount": "34.50"}, schema)
    assert ok is True
    assert clean_arguments({"amount": "34.50"}, schema)["amount"] == 34.50


def test_an_invented_field_is_dropped_rather_than_crashing():
    schema = {"properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}
    cleaned = clean_arguments({"order_id": "ORD-1", "colour": "blue"}, schema)
    assert cleaned == {"order_id": "ORD-1"}


# ---------------- authorization ----------------

def test_a_customer_can_read_their_own_order(executor, alice_context):
    result, trace = call_tool(executor, alice_context, "get_order", {"order_id": "ORD-10023"})
    assert result.ok is True
    assert result.data["status"] == "in_transit"


def test_a_customer_cannot_read_another_customers_order(executor, alice_context):
    """
    THE ATTACK THIS PROJECT EXISTS TO STOP.

    ORD-20001 belongs to Bob. Alice asking for it must get exactly the same
    answer as asking for an order that does not exist - otherwise the tool
    becomes a way to test which order numbers are real.
    """
    result, trace = call_tool(executor, alice_context, "get_order", {"order_id": "ORD-20001"})

    assert result.ok is False
    assert result.error_code == "not_found"
    assert "not your" not in result.error_message.lower()
    assert "belongs" not in result.error_message.lower()
    # The internal marker must not travel back to the caller or the model.
    assert "__audit_note__" not in result.data


def test_the_refused_attempt_is_still_recorded(executor, alice_context, database):
    """Hidden from the customer, visible to whoever reads the audit log."""
    call_tool(executor, alice_context, "get_order", {"order_id": "ORD-20001"})

    found = False
    for record in database.read_audit("conv_test"):
        if "cross_customer_access_attempt" in record["detail"]:
            found = True
    assert found is True


def test_support_staff_can_read_any_customers_order(executor, database, staff):
    context = ToolContext(caller=staff, customer_id="CUST-1002",
                          conversation_id="conv_staff", database=database)
    result, trace = call_tool(executor, context, "get_order", {"order_id": "ORD-20001"})
    assert result.ok is True


# ---------------- tool permissions ----------------

def test_a_tool_outside_the_allowed_list_is_refused(executor, alice_context):
    result, trace = call_tool(
        executor, alice_context, "issue_refund",
        {"order_id": "ORD-10024", "reason": "x"},
        allowed=["get_order"],
    )
    assert result.ok is False
    assert result.error_code == "not_allowed"
    assert trace.allowed is False


def test_an_invented_tool_name_is_handled_not_crashed(executor, alice_context):
    result, trace = call_tool(executor, alice_context, "delete_everything", {})
    assert result.ok is False
    assert result.error_code == "unknown_tool"
    # The message lists the real tools, so the model can correct itself.
    assert "get_order" in result.error_message


# ---------------- the refund gates ----------------

def test_a_small_refund_is_issued(executor, alice_context, database):
    result, trace = call_tool(
        executor, alice_context, "issue_refund",
        {"order_id": "ORD-10024", "amount": 34.50, "reason": "faulty"},
    )
    assert result.ok is True
    assert result.data["amount"] == 34.50
    assert database.get_refund_for_order("ORD-10024") is not None


def test_a_large_refund_is_held_for_a_human(executor, alice_context, database):
    result, trace = call_tool(
        executor, alice_context, "issue_refund",
        {"order_id": "ORD-10025", "amount": 899.00, "reason": "broken"},
    )
    assert result.ok is False
    assert result.needs_approval is True
    assert trace.outcome == "needs_approval"
    # Nothing was written.
    assert database.get_refund_for_order("ORD-10025") is None


def test_the_held_refund_records_the_real_amount(executor, alice_context):
    """
    amount 0 means "the whole order". A human approving must see 899.00, not
    0.00, or they are approving a number that is not the one being paid.
    """
    result, trace = call_tool(
        executor, alice_context, "issue_refund",
        {"order_id": "ORD-10025", "amount": 0, "reason": "broken"},
    )
    assert result.needs_approval is True
    assert trace.arguments["amount"] == 899.00


def test_an_undelivered_order_cannot_be_refunded(executor, alice_context):
    result, trace = call_tool(
        executor, alice_context, "issue_refund",
        {"order_id": "ORD-10023", "amount": 10.0, "reason": "changed mind"},
    )
    assert result.error_code == "not_delivered"


def test_an_order_outside_the_window_cannot_be_refunded(executor, alice_context):
    result, trace = call_tool(
        executor, alice_context, "issue_refund",
        {"order_id": "ORD-10028", "amount": 26.0, "reason": "late"},
    )
    assert result.error_code == "outside_refund_window"


def test_an_already_refunded_order_is_not_refunded_again(executor, alice_context, database):
    """
    A SECOND LINE OF DEFENCE, SEPARATE FROM IDEMPOTENCY.

    Idempotency catches the identical request being repeated. It does NOT catch a
    second refund on the same order with a DIFFERENT amount - that is a new
    request with a new key, and it would sail straight through.

    So the tool checks the database as well. This test deliberately changes the
    amount, which defeats idempotency and leaves gate 4 as the only thing
    standing between the customer and being paid twice.
    """
    first, _trace = call_tool(
        executor, alice_context, "issue_refund",
        {"order_id": "ORD-10024", "amount": 34.50, "reason": "faulty"},
    )
    assert first.ok is True
    before = database.count_refunds()

    second, _trace = call_tool(
        executor, alice_context, "issue_refund",
        {"order_id": "ORD-10024", "amount": 20.00, "reason": "faulty again"},
    )

    assert second.ok is False
    assert second.error_code == "already_refunded"
    assert database.count_refunds() == before


def test_an_order_that_was_returned_cannot_be_refunded_again(executor, alice_context):
    """ORD-10026 was returned and already has a refund running."""
    result, _trace = call_tool(
        executor, alice_context, "issue_refund",
        {"order_id": "ORD-10026", "amount": 48.0, "reason": "again"},
    )
    assert result.ok is False
    # It is stopped by the delivery gate before it even reaches the refund check,
    # which is fine: what matters is that it is stopped.
    assert result.error_code in ("not_delivered", "already_refunded")


def test_a_refund_larger_than_the_order_is_refused(executor, alice_context):
    result, trace = call_tool(
        executor, alice_context, "issue_refund",
        {"order_id": "ORD-10024", "amount": 5000.0, "reason": "greedy"},
    )
    assert result.error_code in ("amount_too_large", "above_hard_limit")


def test_a_refund_for_someone_elses_order_is_refused(executor, alice_context, database):
    result, trace = call_tool(
        executor, alice_context, "issue_refund",
        {"order_id": "ORD-20001", "amount": 10.0, "reason": "not mine"},
    )
    assert result.ok is False
    assert result.error_code == "not_found"
    assert database.get_refund_for_order("ORD-20001") is None


# ---------------- idempotency ----------------

def test_the_same_refund_twice_pays_once(executor, alice_context, database):
    """
    THE MOST EXPENSIVE BUG AN AGENT CAN HAVE.

    A retried loop, a customer pressing send twice, or a timeout after the write
    succeeded all produce a second identical call. It must replay, not repeat.
    """
    before = database.count_refunds()

    arguments = {"order_id": "ORD-10024", "amount": 34.50, "reason": "faulty"}
    first, first_trace = call_tool(executor, alice_context, "issue_refund", dict(arguments))
    second, second_trace = call_tool(executor, alice_context, "issue_refund", dict(arguments))

    assert first.ok is True
    assert second.ok is True
    assert first.data["refund_id"] == second.data["refund_id"]
    assert second_trace.idempotent_replay is True
    assert first_trace.idempotent_replay is False
    assert database.count_refunds() == before + 1


def test_the_idempotency_key_ignores_argument_order():
    """
    Unsorted keys would give the same request two different keys, and the
    protection would silently do nothing.
    """
    first = build_idempotency_key("conv", "issue_refund", {"a": 1, "b": 2})
    second = build_idempotency_key("conv", "issue_refund", {"b": 2, "a": 1})
    assert first == second
    assert canonical_arguments({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_different_conversations_do_not_share_an_idempotency_key():
    first = build_idempotency_key("conv-1", "issue_refund", {"order_id": "ORD-1"})
    second = build_idempotency_key("conv-2", "issue_refund", {"order_id": "ORD-1"})
    assert first != second


def test_a_failed_write_is_not_cached(executor, alice_context, database):
    """
    Storing a failure would make every retry replay the failure forever, and the
    thing that was supposed to fix the problem becomes the thing preventing it.
    """
    call_tool(executor, alice_context, "issue_refund",
              {"order_id": "ORD-10023", "amount": 10.0, "reason": "too early"})

    rows = database.connection.execute("SELECT COUNT(*) AS n FROM idempotency_keys").fetchone()
    assert rows["n"] == 0


# ---------------- retries ----------------

def test_a_permanent_failure_is_not_retried(executor, alice_context):
    """Retrying "no such order" three times does not find the order."""
    result, trace = call_tool(executor, alice_context, "get_order", {"order_id": "ORD-99999"})
    assert result.ok is False
    assert trace.attempts == 1


def test_a_transient_failure_is_retried(database, alice):
    from support_agent.layer2_models.schemas import ToolResult, ToolRisk
    from support_agent.layer4_tools.base import Tool
    from support_agent.layer4_tools.executor import ToolExecutor

    class FlakyTool(Tool):
        name = "flaky"
        description = "fails twice, then works"
        risk = ToolRisk.READ
        parameters = {"type": "object", "properties": {}, "required": []}

        def __init__(self):
            self.calls = 0

        def run(self, arguments, context):
            self.calls = self.calls + 1
            if self.calls < 3:
                return ToolResult(ok=False, error_code="timeout",
                                  error_message="upstream slow", retryable=True)
            return ToolResult(ok=True, data={"calls": self.calls})

    flaky = FlakyTool()

    import support_agent.layer4_tools.registry as registry
    registry.ALL_TOOLS.append(flaky)
    try:
        context = ToolContext(caller=alice, customer_id="CUST-1001",
                              conversation_id="conv_retry", database=database)
        executor = ToolExecutor(database)
        result, trace = executor.execute(
            ToolCall(call_id="c", tool_name="flaky", arguments={}), context, ["flaky"]
        )
        assert result.ok is True
        assert trace.attempts == 3
    finally:
        registry.ALL_TOOLS.remove(flaky)


# ---------------- tickets ----------------

def test_a_ticket_summary_is_stripped_of_personal_data(executor, alice_context, database):
    """The summary is written by the model from the customer's words."""
    result, trace = call_tool(
        executor, alice_context, "create_ticket",
        {"category": "billing",
         "summary": "Customer alice@example.com says card 4532015112830366 was charged twice"},
    )
    assert result.ok is True

    tickets = database.list_tickets("CUST-1001")
    assert "alice@example.com" not in tickets[0]["summary"]
    assert "4532015112830366" not in tickets[0]["summary"]


def test_an_unknown_ticket_category_becomes_other(executor, alice_context, database):
    call_tool(executor, alice_context, "create_ticket",
              {"category": "interpretive-dance", "summary": "something"})
    assert database.list_tickets("CUST-1001")[0]["category"] == "other"
