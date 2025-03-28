"""TESTS FOR LAYER 5 - intent classification, tool policy and escalation."""

from support_agent.layer2_models.schemas import Intent, ToolCallTrace
from support_agent.layer5_routing.step1_classify_intent import classify, classify_with_rules
from support_agent.layer5_routing.step2_tool_policy import allowed_tool_names
from support_agent.layer5_routing.step3_escalation import (
    check_after_agent,
    check_before_agent,
    count_failed_tools,
)

# ---------------- classification ----------------

def test_common_messages_are_classified_correctly():
    expectations = [
        ("Where is my order ORD-10023?", Intent.ORDER_STATUS),
        ("I want a refund for the headphones", Intent.REFUND_REQUEST),
        ("How long do I have to return something?", Intent.RETURN_POLICY),
        ("My parcel never arrived", Intent.DELIVERY_PROBLEM),
        ("I was charged twice", Intent.BILLING),
        ("thanks, that's all", Intent.SMALL_TALK),
        ("you useless bot", Intent.ABUSE),
    ]
    for text, expected in expectations:
        assert classify_with_rules(text).intent == expected, text


def test_an_unclear_message_is_marked_unknown_not_guessed():
    decision = classify_with_rules("hmm ok")
    assert decision.intent == Intent.UNKNOWN
    assert decision.confidence == 0.0


def test_abuse_beats_every_other_signal():
    """Abuse must route to a human even when wrapped in a normal request."""
    decision = classify_with_rules("where is my order you stupid bot")
    assert decision.intent == Intent.ABUSE


def test_the_rules_are_used_when_confident_so_no_model_call_happens(planner):
    class ModelThatMustNotBeCalled:
        is_live = True
        model = "should-not-run"

        def respond(self, system_prompt, messages, tools=None):
            raise AssertionError("the model was called when the rules were confident")

    decision = classify("Where is my order ORD-10023?", ModelThatMustNotBeCalled())
    assert decision.intent == Intent.ORDER_STATUS


# ---------------- tool policy ----------------

def test_the_intent_narrows_which_tools_exist(alice):
    for_policy = allowed_tool_names(Intent.RETURN_POLICY, alice)
    for_refund = allowed_tool_names(Intent.REFUND_REQUEST, alice)

    # A policy question has no business touching the refund tool.
    assert "issue_refund" not in for_policy
    assert "issue_refund" in for_refund


def test_small_talk_gets_no_tools_at_all(alice):
    assert allowed_tool_names(Intent.SMALL_TALK, alice) == []


def test_abuse_gets_no_tools_at_all(alice):
    assert allowed_tool_names(Intent.ABUSE, alice) == []


def test_an_unknown_intent_gets_only_safe_tools(alice):
    names = allowed_tool_names(Intent.UNKNOWN, alice)
    assert "issue_refund" not in names
    assert "search_help_articles" in names


# ---------------- escalation ----------------

def test_asking_for_a_human_escalates_immediately():
    """Unconditional. Arguing with this request is the most infuriating thing a bot does."""
    escalation = check_before_agent("let me speak to a human", Intent.ORDER_STATUS, 1, 0)
    assert escalation.escalated is True
    assert escalation.reason.value == "customer_asked"


def test_abuse_escalates():
    escalation = check_before_agent("you idiot", Intent.ABUSE, 1, 0)
    assert escalation.escalated is True


def test_a_normal_message_does_not_escalate():
    assert check_before_agent("where is my order", Intent.ORDER_STATUS, 1, 0).escalated is False


def test_repeated_tool_failures_escalate():
    escalation = check_before_agent("where is my order", Intent.ORDER_STATUS, 2, 2)
    assert escalation.reason.value == "repeated_tool_failure"


def test_a_very_long_conversation_escalates():
    escalation = check_before_agent("where is my order", Intent.ORDER_STATUS, 15, 0)
    assert escalation.reason.value == "too_many_turns"


def test_one_frustrated_sentence_early_does_not_escalate():
    """Frustration is normal. Frustration in a conversation already going badly is not."""
    assert check_before_agent("this is ridiculous", Intent.COMPLAINT, 1, 0).escalated is False
    assert check_before_agent("this is ridiculous", Intent.COMPLAINT, 4, 0).escalated is True


def test_a_held_action_escalates_after_the_agent_runs():
    trace = ToolCallTrace(tool_name="issue_refund", risk="write_high", outcome="needs_approval")
    escalation = check_after_agent([trace], "some reply", True)
    assert escalation.reason.value == "needs_approval"


def test_an_empty_answer_with_no_tools_escalates():
    assert check_after_agent([], "hmm", False).reason.value == "agent_uncertain"


def test_a_good_answer_does_not_escalate():
    reply = "Your order is in transit and is expected on Friday."
    assert check_after_agent([], reply, True).escalated is False


def test_counting_failures_ignores_successes():
    traces = [
        ToolCallTrace(tool_name="a", risk="read", outcome="ok"),
        ToolCallTrace(tool_name="b", risk="read", outcome="failed"),
        ToolCallTrace(tool_name="c", risk="read", outcome="denied"),
        ToolCallTrace(tool_name="d", risk="read", outcome="replayed"),
    ]
    assert count_failed_tools(traces) == 2
