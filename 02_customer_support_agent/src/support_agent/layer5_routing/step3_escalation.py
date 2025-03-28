"""
LAYER 5 - ROUTING, STEP 3: WHEN TO FETCH A HUMAN
================================================
An agent that never escalates is worse than no agent. It keeps a frustrated
person in a loop, produces an apologetic non-answer, and the complaint that
follows is about the company, not the bot.

So escalation is a set of explicit rules, checked in order, not something the
model decides for itself when it feels stuck.

  BEFORE the agent runs
      1. the customer asked for a person - always honoured, immediately
      2. the message is abusive
      3. the conversation has gone on too long
      4. too many tools have already failed in this conversation

  AFTER the agent runs
      5. a tool needs human approval
      6. the agent produced no answer and used no tools

WHY "THE CUSTOMER ASKED" IS FIRST AND UNCONDITIONAL
    Because a bot that argues with "let me speak to a human" is the single most
    reliably infuriating thing in customer support. There is no business case for
    trying one more time first.
"""

from support_agent.layer0_shared.text_tools import contains_any
from support_agent.layer1_config.settings import settings
from support_agent.layer2_models.schemas import Escalation, EscalationReason, Intent

HUMAN_REQUEST_PHRASES = [
    "speak to a human", "talk to a human", "real person", "speak to someone",
    "human agent", "talk to a person", "get me a person", "customer service rep",
    "let me speak to", "put me through", "speak to a manager", "talk to a manager",
    "supervisor",
]

ANGER_PHRASES = [
    "this is ridiculous", "absolutely furious", "fed up", "sick of this",
    "waste of my time", "third time", "again and again", "never shopping",
    "cancel my account", "legal action", "trading standards",
]


def check_before_agent(
    message_text: str,
    intent: Intent,
    turn_count: int,
    tool_failures: int,
) -> Escalation:
    """Should we hand over before even trying? Checked in priority order."""

    # --- 1. the customer asked ---
    asked = contains_any(message_text, HUMAN_REQUEST_PHRASES)
    if len(asked) > 0:
        return Escalation(
            escalated=True,
            reason=EscalationReason.CUSTOMER_ASKED,
            explanation="The customer asked to speak to a person.",
        )

    # --- 2. abuse ---
    if intent == Intent.ABUSE:
        return Escalation(
            escalated=True,
            reason=EscalationReason.ABUSE,
            explanation="The message contained abusive language, so a person should take over.",
        )

    # --- 3. going round in circles ---
    if turn_count >= settings.escalate_after_turns:
        return Escalation(
            escalated=True,
            reason=EscalationReason.TOO_MANY_TURNS,
            explanation=(
                "This conversation has run for %d turns without being resolved."
                % turn_count
            ),
        )

    # --- 4. the tools keep failing ---
    if tool_failures >= settings.escalate_after_tool_failures:
        return Escalation(
            escalated=True,
            reason=EscalationReason.REPEATED_TOOL_FAILURE,
            explanation=(
                "%d tool calls have failed in this conversation, so the agent is "
                "not going to be able to finish it." % tool_failures
            ),
        )

    # --- anger is a signal, not a trigger on its own ---
    # One frustrated sentence is normal. Frustration plus a conversation that is
    # already struggling is when a person should step in.
    angry = contains_any(message_text, ANGER_PHRASES)
    if len(angry) > 0 and (turn_count >= 3 or tool_failures >= 1):
        return Escalation(
            escalated=True,
            reason=EscalationReason.ANGRY_CUSTOMER,
            explanation="The customer is clearly frustrated and the conversation is not going well.",
        )

    return Escalation(escalated=False)


def check_after_agent(
    tool_traces: list,
    reply_text: str,
    used_any_tool: bool,
) -> Escalation:
    """Should we hand over because of what just happened?"""

    # --- 5. something is waiting on a human decision ---
    for trace in tool_traces:
        if trace.outcome == "needs_approval":
            return Escalation(
                escalated=True,
                reason=EscalationReason.NEEDS_APPROVAL,
                explanation=(
                    "The action the customer asked for is above the automatic limit "
                    "and needs a colleague to approve it."
                ),
            )

    # --- 6. the agent has nothing useful to say ---
    if not used_any_tool and len(reply_text.strip()) < 25:
        return Escalation(
            escalated=True,
            reason=EscalationReason.AGENT_UNCERTAIN,
            explanation="The agent could not produce a useful answer.",
        )

    return Escalation(escalated=False)


def count_failed_tools(tool_traces: list) -> int:
    """How many tool calls failed this turn. Feeds rule 4 on the next turn."""
    failures = 0
    for trace in tool_traces:
        if trace.outcome in ("failed", "invalid_arguments", "unknown_tool", "denied"):
            failures = failures + 1
    return failures
