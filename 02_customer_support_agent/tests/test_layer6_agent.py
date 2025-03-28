"""TESTS FOR LAYER 6 - memory and the whole agent turn."""

from support_agent.layer2_models.schemas import Message
from support_agent.layer6_agent.memory import (
    find_missing_identifiers,
    split_recent_and_old,
    summarise_without_model,
)

# ---------------- memory ----------------

def test_recent_turns_are_kept_and_older_ones_split_off():
    messages = []
    for number in range(1, 7):
        messages.append(Message(role="user", content="question %d" % number))
        messages.append(Message(role="assistant", content="answer %d" % number))

    recent, old = split_recent_and_old(messages, recent_turns=2)

    assert len(recent) == 4        # two user messages and their answers
    assert len(old) == 8
    assert recent[0].content == "question 5"


def test_a_short_conversation_is_not_split_at_all():
    messages = [Message(role="user", content="hello"), Message(role="assistant", content="hi")]
    recent, old = split_recent_and_old(messages, recent_turns=8)
    assert old == []
    assert len(recent) == 2


def test_summarising_never_loses_an_order_number():
    """
    THE FAILURE THIS PREVENTS.

    A summary that drops "we already refunded ORD-10024" leads the agent to
    refund it again. Identifiers are the one thing compression must not lose.
    """
    old = [
        Message(role="user", content="I want a refund for ORD-10024"),
        Message(role="assistant", content="I have issued refund REF-5001 for you."),
        Message(role="user", content="thanks"),
    ]
    summary = summarise_without_model(old, "")

    assert "ORD-10024" in summary
    assert "REF-5001" in summary


def test_missing_identifiers_are_detected():
    old = [Message(role="assistant", content="refund REF-5001 for ORD-10024 is processing")]
    missing = find_missing_identifiers(old, "We talked about a refund.")
    assert "REF-5001" in missing
    assert "ORD-10024" in missing


# ---------------- a whole turn ----------------

def test_a_simple_order_question_is_answered_with_a_tool(agent, alice):
    response = agent.handle("Where is my order ORD-10023?", "", alice, "CUST-1001")

    assert response.intent == "order_status"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].tool_name == "get_order"
    assert response.tool_calls[0].outcome == "ok"
    assert "ORD-10023" in response.reply
    assert response.resolved is True


def test_a_policy_question_uses_the_help_pages(agent, alice):
    response = agent.handle("What is your refund policy?", "", alice, "CUST-1001")
    assert response.tool_calls[0].tool_name == "search_help_articles"
    assert "30 days" in response.reply


def test_asking_for_a_human_escalates_and_creates_a_ticket(agent, alice, database):
    response = agent.handle("let me speak to a human", "", alice, "CUST-1001")

    assert response.escalation.escalated is True
    assert response.escalation.reason.value == "customer_asked"
    assert response.escalation.ticket_id != ""
    assert response.tool_calls == []          # no tools were run, so it was cheap
    assert database.count_tickets() == 1


def test_an_escalation_always_produces_a_ticket(agent, alice, database):
    """An escalation with no ticket is the agent promising help to nobody."""
    agent.handle("you stupid bot", "", alice, "CUST-1001")
    assert database.count_tickets() == 1


def test_a_large_refund_escalates_and_creates_an_approval(agent, alice, database):
    response = agent.handle(
        "I want a refund for ORD-10025, the espresso machine is broken", "", alice, "CUST-1001"
    )

    assert response.escalation.reason.value == "needs_approval"
    pending = database.list_approvals("pending")
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "issue_refund"
    assert pending[0]["arguments"]["amount"] == 899.00
    # Nothing was paid.
    assert database.get_refund_for_order("ORD-10025") is None


def test_a_customer_cannot_reach_another_customers_order_through_the_agent(agent, alice, database):
    response = agent.handle("Where is my order ORD-20001?", "", alice, "CUST-1001")

    assert "standing desk" not in response.reply.lower()
    assert "1Z999AA10199999999" not in response.reply


def test_personal_data_is_reported_as_redacted(agent, alice):
    response = agent.handle(
        "my card 4532015112830366 was charged twice", "", alice, "CUST-1001"
    )
    assert "card" in response.pii_redacted


def test_a_conversation_keeps_its_id_across_turns(agent, alice):
    first = agent.handle("Where is my order ORD-10023?", "", alice, "CUST-1001")
    second = agent.handle("and what about ORD-10024?", first.conversation_id, alice, "CUST-1001")
    assert second.conversation_id == first.conversation_id


def test_turn_counts_and_usage_are_recorded(agent, alice, database):
    response = agent.handle("Where is my order ORD-10023?", "", alice, "CUST-1001")

    conversation = database.get_conversation(response.conversation_id)
    assert conversation["turn_count"] == 1
    assert response.usage.model_calls >= 1
    assert response.usage.total_tokens > 0


def test_every_tool_call_reaches_the_audit_log(agent, alice, database):
    agent.handle("Where is my order ORD-10023?", "", alice, "CUST-1001")
    records = database.read_audit()

    tool_rows = 0
    for record in records:
        if record["action"] == "tool_call":
            tool_rows = tool_rows + 1
    assert tool_rows >= 1


def test_the_step_limit_stops_a_runaway_agent(database, alice):
    """
    A model that keeps asking for tools would loop forever and bill forever.
    The cap turns that into a bounded worst case and an honest answer.
    """
    from support_agent.layer0_shared.llm_client import LlmReply, new_call_id
    from support_agent.layer2_models.schemas import ToolCall
    from support_agent.layer6_agent.agent import SupportAgent

    class ModelThatNeverStops:
        is_live = False
        model = "never-stops"

        def respond(self, system_prompt, messages, tools=None):
            if tools is None or len(tools) == 0:
                return LlmReply(text="I could not work this out.", model=self.model)
            return LlmReply(
                tool_calls=[ToolCall(call_id=new_call_id(), tool_name="list_my_orders", arguments={})],
                model=self.model,
            )

    agent = SupportAgent(database=database, chat_client=ModelThatNeverStops())
    response = agent.handle("Where is my order?", "", alice, "CUST-1001")

    assert len(response.tool_calls) == 5          # MAX_TOOL_STEPS from conftest
    assert response.reply != ""
    assert response.resolved is False
