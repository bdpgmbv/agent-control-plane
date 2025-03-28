"""
LAYER 6 - THE AGENT: CONVERSATION MEMORY
========================================
A support conversation grows. Sending all of it on every turn costs more each
time and eventually stops fitting.

The approach here is the usual one, and it is usual because it works:

    keep the last N turns exactly as they were
    compress everything older into a short summary
    send: summary + recent turns

WHAT MUST SURVIVE COMPRESSION
    Order numbers, refund references, ticket ids, and what is still outstanding.
    A summary that loses "we already refunded ORD-10024" will cheerfully refund
    it again. The summary prompt says so explicitly, and the fallback used when
    there is no model keeps any line containing an identifier.

TOOL RESULTS ARE DROPPED FIRST
    They are the bulkiest thing in the history and the least useful later: a
    delivery date looked up six turns ago may well be stale. Only tool results
    from the current turn are kept in full.
"""

import re

from support_agent.layer0_shared.logging_setup import get_logger
from support_agent.layer1_config.settings import settings
from support_agent.layer2_models.schemas import Message
from support_agent.layer6_agent.prompts import SUMMARY_SYSTEM_PROMPT

log = get_logger(__name__)

# Anything matching these must survive summarisation.
IDENTIFIER_PATTERN = re.compile(r"\b(?:ORD|REF|TICK|CUST)-[A-Z0-9]+\b", re.IGNORECASE)


def split_recent_and_old(messages: list[Message], recent_turns: int) -> tuple[list[Message], list[Message]]:
    """
    Split the history into the part to keep verbatim and the part to compress.

    A "turn" is one user message and everything the agent did in response, so we
    count user messages backwards and cut there.
    """
    if len(messages) == 0:
        return ([], [])

    user_positions: list[int] = []
    position = 0
    while position < len(messages):
        if messages[position].role == "user":
            user_positions.append(position)
        position = position + 1

    if len(user_positions) <= recent_turns:
        return (messages, [])

    cut_at = user_positions[len(user_positions) - recent_turns]
    return (messages[cut_at:], messages[:cut_at])


def summarise_without_model(old_messages: list[Message], previous_summary: str) -> str:
    """
    Compress with no model call.

    Keeps every line that mentions an identifier, plus the customer's own words,
    and throws away the rest. Crude, but it never loses an order number - which
    is the failure that actually costs money.
    """
    kept: list[str] = []

    if previous_summary != "":
        kept.append(previous_summary)

    for message in old_messages:
        if message.content.strip() == "":
            continue

        if message.role == "user":
            kept.append("Customer asked: " + message.content.strip()[:160])
            continue

        if message.role == "assistant" and IDENTIFIER_PATTERN.search(message.content):
            kept.append("Agent said: " + message.content.strip()[:160])
            continue

        if message.role == "tool" and IDENTIFIER_PATTERN.search(message.content):
            found = IDENTIFIER_PATTERN.findall(message.content)
            unique: list[str] = []
            for identifier in found:
                if identifier.upper() not in unique:
                    unique.append(identifier.upper())
            kept.append("Looked up: " + ", ".join(unique))

    if len(kept) == 0:
        return previous_summary

    # Keep the summary itself from growing without bound.
    if len(kept) > 12:
        kept = kept[-12:]

    return "\n".join(kept)


def summarise_with_model(old_messages: list[Message], previous_summary: str, chat_client, usage=None) -> str:
    """Ask the model to compress. Falls back to the rule-based version."""
    transcript_lines: list[str] = []
    if previous_summary != "":
        transcript_lines.append("Earlier summary: " + previous_summary)

    for message in old_messages:
        if message.content.strip() == "":
            continue
        transcript_lines.append(message.role + ": " + message.content.strip()[:300])

    if len(transcript_lines) == 0:
        return previous_summary

    try:
        reply = chat_client.respond(
            system_prompt=SUMMARY_SYSTEM_PROMPT,
            messages=[Message(role="user", content="\n".join(transcript_lines))],
            tools=None,
        )
        if usage is not None:
            usage.add_model_call(
                "memory_summary", reply.prompt_tokens, reply.completion_tokens, reply.cost_usd
            )

        summary = reply.text.strip()
        if summary == "":
            return summarise_without_model(old_messages, previous_summary)

        # Safety net: if the model dropped an identifier we had, put it back.
        # Losing an order number is the one failure we cannot accept here.
        missing = find_missing_identifiers(old_messages, summary)
        if len(missing) > 0:
            summary = summary + "\nReferences mentioned earlier: " + ", ".join(missing)

        return summary

    except Exception as error:
        log.warning("summarising with the model failed, using rules: %s", error)
        return summarise_without_model(old_messages, previous_summary)


def find_missing_identifiers(old_messages: list[Message], summary: str) -> list[str]:
    """Identifiers that were in the history but are absent from the summary."""
    in_history: list[str] = []
    for message in old_messages:
        for identifier in IDENTIFIER_PATTERN.findall(message.content):
            upper = identifier.upper()
            if upper not in in_history:
                in_history.append(upper)

    summary_upper = summary.upper()

    missing: list[str] = []
    for identifier in in_history:
        if identifier not in summary_upper:
            missing.append(identifier)
    return missing


class ConversationMemory:
    """Builds the message list sent to the model for one turn."""

    def __init__(self, database, chat_client) -> None:
        self.database = database
        self.chat_client = chat_client

    def build_context(self, conversation_id: str, usage=None) -> tuple[list[Message], str]:
        """
        Returns (messages to send, summary of what was dropped).

        The summary is stored back on the conversation so it does not have to be
        rebuilt from scratch every turn.
        """
        stored = self.database.read_messages(conversation_id)
        conversation = self.database.get_conversation(conversation_id)

        previous_summary = ""
        if conversation is not None:
            previous_summary = conversation["summary"]

        recent, old = split_recent_and_old(stored, settings.memory_recent_turns)

        if len(old) == 0:
            return (recent, previous_summary)

        if self.chat_client.is_live:
            summary = summarise_with_model(old, previous_summary, self.chat_client, usage)
        else:
            summary = summarise_without_model(old, previous_summary)

        return (recent, summary)

    def context_messages(self, recent: list[Message], summary: str) -> list[Message]:
        """Put the summary at the front, as a user-visible note to the model."""
        if summary.strip() == "":
            return recent

        note = Message(
            role="user",
            content="[Earlier in this conversation]\n" + summary.strip(),
        )
        return [note] + recent
