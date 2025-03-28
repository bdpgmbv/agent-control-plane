"""
LAYER 6 - THE AGENT: THE ORCHESTRATOR
=====================================
One customer message in, one verified reply out. The order of operations is the
design, so here it is in full:

     1. find or start the conversation
     2. redact personal data from the message
     3. classify the intent
     4. ESCALATION CHECK - should a human take this before we even try?
     5. work out which tools this turn may use
     6. save the customer's message
     7. build the context (recent turns + summary of older ones)
     8. run the tool loop
     9. ESCALATION CHECK - did what just happened need a human?
    10. save everything, update the counters, return

Steps 4 and 9 are two different questions. Step 4 is "is this the kind of
message a bot should not handle?". Step 9 is "did we get stuck, or hit a limit?".
Conflating them means either escalating too early or discovering too late.
"""

import time
import uuid
from datetime import UTC, datetime

from support_agent.layer0_shared.logging_setup import (
    current_conversation_id,
    current_request_id,
    get_logger,
    log_event,
    new_conversation_id,
)
from support_agent.layer0_shared.metrics import metrics
from support_agent.layer0_shared.pii import redact
from support_agent.layer0_shared.usage_tracker import UsageAccumulator
from support_agent.layer1_config.settings import ApiKeyRecord, settings
from support_agent.layer2_models.schemas import (
    AuditRecord,
    ChatResponse,
    Escalation,
    EscalationReason,
    Intent,
    Message,
    ToolCallTrace,
)
from support_agent.layer4_tools.base import ToolContext
from support_agent.layer4_tools.executor import ToolExecutor
from support_agent.layer5_routing.step1_classify_intent import classify
from support_agent.layer5_routing.step2_tool_policy import allowed_tools
from support_agent.layer5_routing.step3_escalation import (
    check_after_agent,
    check_before_agent,
    count_failed_tools,
)
from support_agent.layer6_agent.memory import ConversationMemory
from support_agent.layer6_agent.prompts import build_system_prompt
from support_agent.layer6_agent.tool_loop import ToolLoop

log = get_logger(__name__)


def now_text() -> str:
    return datetime.now(UTC).isoformat()


class SupportAgent:
    """The whole support agent. Used by the API, the tests and the eval suite."""

    def __init__(self, database, chat_client) -> None:
        self.database = database
        self.chat_client = chat_client
        self.executor = ToolExecutor(database)
        self.memory = ConversationMemory(database, chat_client)
        self.loop = ToolLoop(chat_client, self.executor)

    # ------------------------------------------------------------------

    def handle(self, message_text: str, conversation_id: str, caller: ApiKeyRecord, customer_id: str) -> ChatResponse:
        started = time.perf_counter()
        usage = UsageAccumulator()
        metrics.increment("turns_total")

        # ---------- 1. the conversation ----------
        conversation_id = self.ensure_conversation(conversation_id, customer_id)
        current_conversation_id.set(conversation_id)
        conversation = self.database.get_conversation(conversation_id)

        # ---------- 2. personal data out of the message ----------
        safe_text, redacted_kinds = redact(message_text)
        if len(redacted_kinds) > 0:
            metrics.increment("pii_redactions_total", len(redacted_kinds))

        if settings.redact_pii_before_model:
            text_for_model = safe_text
        else:
            text_for_model = message_text

        # ---------- 3. what are they asking? ----------
        decision = classify(safe_text, self.chat_client, usage)
        metrics.increment("intent_" + decision.intent.value + "_total")

        log_event(
            log,
            "turn.started",
            intent=decision.intent.value,
            confidence=decision.confidence,
            turn=conversation["turn_count"] + 1,
            pii_kinds=redacted_kinds,
        )

        # ---------- 4. escalate before trying? ----------
        early = check_before_agent(
            message_text=safe_text,
            intent=decision.intent,
            turn_count=conversation["turn_count"],
            tool_failures=conversation["tool_failures"],
        )
        if early.escalated:
            return self.hand_over(
                conversation_id=conversation_id,
                customer_id=customer_id,
                caller=caller,
                escalation=early,
                decision=decision,
                user_text=text_for_model,
                redacted_kinds=redacted_kinds,
                usage=usage,
                started=started,
            )

        # ---------- 5. which tools ----------
        tools = allowed_tools(decision.intent, caller)

        # ---------- 6. save what the customer said ----------
        user_message = Message(role="user", content=text_for_model)
        self.database.append_message(conversation_id, user_message)

        # ---------- 7. build the context ----------
        recent, summary = self.memory.build_context(conversation_id, usage)
        context_messages = self.memory.context_messages(recent, summary)

        customer = self.database.get_customer(customer_id)
        customer_name = "the customer"
        if customer is not None:
            customer_name = customer["name"]

        system_prompt = build_system_prompt(decision.intent, customer_name, customer_id)

        tool_context = ToolContext(
            caller=caller,
            customer_id=customer_id,
            conversation_id=conversation_id,
            database=self.database,
        )

        # ---------- 8. run the loop ----------
        outcome = self.loop.run(
            system_prompt=system_prompt,
            context_messages=context_messages,
            tools=tools,
            tool_context=tool_context,
            usage=usage,
        )

        for message in outcome.new_messages:
            self.database.append_message(conversation_id, message)

        # ---------- 9. escalate after? ----------
        late = check_after_agent(
            tool_traces=outcome.tool_traces,
            reply_text=outcome.reply_text,
            used_any_tool=outcome.used_any_tool(),
        )

        reply_text = outcome.reply_text
        escalation = late

        if late.escalated:
            escalation = self.record_escalation(
                conversation_id=conversation_id,
                customer_id=customer_id,
                caller=caller,
                escalation=late,
                decision=decision,
                tool_traces=outcome.tool_traces,
            )
            reply_text = self.add_handover_note(reply_text, escalation)

        # ---------- 10. bookkeeping ----------
        failures_this_turn = count_failed_tools(outcome.tool_traces)
        self.database.update_conversation(
            conversation_id=conversation_id,
            when=now_text(),
            turn_count=conversation["turn_count"] + 1,
            tool_failures=conversation["tool_failures"] + failures_this_turn,
            status=self.next_status(escalation),
            summary=summary,
        )

        resolved = self.looks_resolved(outcome, escalation)
        if resolved:
            metrics.increment("resolved_total")

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        metrics.observe("turn_latency_ms", elapsed_ms)
        metrics.observe("tokens_per_turn", usage.total_tokens())
        metrics.observe("cost_usd_per_turn", usage.cost_usd)

        log_event(
            log,
            "turn.finished",
            intent=decision.intent.value,
            tools_called=len(outcome.tool_traces),
            steps_used=outcome.steps_used,
            hit_step_limit=outcome.hit_step_limit,
            escalated=escalation.escalated,
            resolved=resolved,
            latency_ms=elapsed_ms,
            cost_usd=round(usage.cost_usd, 8),
        )

        return ChatResponse(
            conversation_id=conversation_id,
            reply=reply_text,
            intent=decision.intent.value,
            intent_confidence=decision.confidence,
            tool_calls=outcome.tool_traces,
            escalation=escalation,
            resolved=resolved,
            pii_redacted=redacted_kinds,
            usage=usage.to_report(elapsed_ms),
            request_id=current_request_id.get(),
        )

    # ------------------------------------------------------------------
    #  Pieces used above
    # ------------------------------------------------------------------

    def ensure_conversation(self, conversation_id: str, customer_id: str) -> str:
        """Find the conversation, or start one."""
        if conversation_id != "":
            existing = self.database.get_conversation(conversation_id)
            if existing is not None:
                return conversation_id

        new_id = new_conversation_id()
        self.database.create_conversation(new_id, customer_id, now_text())
        metrics.increment("conversations_total")
        return new_id

    def hand_over(
        self,
        conversation_id: str,
        customer_id: str,
        caller: ApiKeyRecord,
        escalation: Escalation,
        decision,
        user_text: str,
        redacted_kinds: list[str],
        usage,
        started: float,
    ) -> ChatResponse:
        """Escalate without running the agent at all."""
        # The customer's message is still recorded: the human taking over needs
        # to read what was actually said.
        self.database.append_message(conversation_id, Message(role="user", content=user_text))

        recorded = self.record_escalation(
            conversation_id=conversation_id,
            customer_id=customer_id,
            caller=caller,
            escalation=escalation,
            decision=decision,
            tool_traces=[],
        )

        reply = self.handover_message(recorded)
        self.database.append_message(conversation_id, Message(role="assistant", content=reply))

        conversation = self.database.get_conversation(conversation_id)
        self.database.update_conversation(
            conversation_id=conversation_id,
            when=now_text(),
            turn_count=conversation["turn_count"] + 1,
            status="escalated",
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        metrics.observe("turn_latency_ms", elapsed_ms)

        log_event(
            log,
            "turn.escalated_early",
            reason=recorded.reason.value if recorded.reason is not None else "unspecified",
            intent=decision.intent.value,
        )

        return ChatResponse(
            conversation_id=conversation_id,
            reply=reply,
            intent=decision.intent.value,
            intent_confidence=decision.confidence,
            tool_calls=[],
            escalation=recorded,
            resolved=False,
            pii_redacted=redacted_kinds,
            usage=usage.to_report(elapsed_ms),
            request_id=current_request_id.get(),
        )

    def record_escalation(
        self,
        conversation_id: str,
        customer_id: str,
        caller: ApiKeyRecord,
        escalation: Escalation,
        decision,
        tool_traces: list[ToolCallTrace],
    ) -> Escalation:
        """
        Create the ticket, and the approval record if one is needed.

        An escalation that produces no ticket is not an escalation. It is the
        agent saying "a human will help you" to nobody.
        """
        metrics.increment("escalations_total")
        reason_name = escalation.reason.value if escalation.reason is not None else "unspecified"
        metrics.increment("escalation_" + reason_name + "_total")

        ticket_id = "TICK-" + uuid.uuid4().hex[:8].upper()
        priority = "normal"
        if escalation.reason in (EscalationReason.ABUSE, EscalationReason.ANGRY_CUSTOMER):
            priority = "high"
        if escalation.reason == EscalationReason.NEEDS_APPROVAL:
            priority = "high"

        summary, _kinds = redact(escalation.explanation)

        self.database.insert_ticket(
            {
                "ticket_id": ticket_id,
                "conversation_id": conversation_id,
                "customer_id": customer_id,
                "category": self.category_for(decision.intent),
                "priority": priority,
                "summary": summary,
                "status": "open",
                "assigned_to": "unassigned",
                "created_at": now_text(),
            }
        )

        # A held action becomes a pending approval a human can act on.
        if escalation.reason == EscalationReason.NEEDS_APPROVAL:
            for trace in tool_traces:
                if trace.outcome != "needs_approval":
                    continue

                self.database.insert_approval(
                    {
                        "approval_id": "APR-" + uuid.uuid4().hex[:8].upper(),
                        "conversation_id": conversation_id,
                        "customer_id": customer_id,
                        "tool_name": trace.tool_name,
                        "arguments": trace.arguments,
                        "reason": trace.summary,
                        "requested_at": now_text(),
                    }
                )

        self.database.append_audit(
            AuditRecord(
                conversation_id=conversation_id,
                actor=caller.role + ":" + caller.customer_id,
                customer_id=customer_id,
                action="escalation",
                tool_name="",
                risk="",
                allowed=True,
                outcome=escalation.reason.value if escalation.reason is not None else "unspecified",
                detail=summary + " | ticket=" + ticket_id,
            )
        )

        return Escalation(
            escalated=True,
            reason=escalation.reason,
            explanation=escalation.explanation,
            ticket_id=ticket_id,
        )

    def category_for(self, intent: Intent) -> str:
        mapping = {
            Intent.REFUND_REQUEST: "refund",
            Intent.ORDER_STATUS: "delivery",
            Intent.DELIVERY_PROBLEM: "delivery",
            Intent.BILLING: "billing",
            Intent.COMPLAINT: "complaint",
            Intent.TECHNICAL: "product",
        }
        return mapping.get(intent, "other")

    def handover_message(self, escalation: Escalation) -> str:
        """What the customer is told. Honest about why, never blaming them."""
        if escalation.reason == EscalationReason.CUSTOMER_ASKED:
            return (
                "Of course. I have passed this to a colleague and created ticket %s "
                "so they have the full conversation. They will pick it up shortly."
                % escalation.ticket_id
            )
        if escalation.reason == EscalationReason.ABUSE:
            return (
                "I am going to hand this to a member of the team. I have opened "
                "ticket %s and they will be in touch." % escalation.ticket_id
            )
        if escalation.reason == EscalationReason.NEEDS_APPROVAL:
            return (
                "This needs a colleague to approve before it can go ahead. I have "
                "opened ticket %s and someone will review it shortly."
                % escalation.ticket_id
            )
        if escalation.reason == EscalationReason.REPEATED_TOOL_FAILURE:
            return (
                "I am not able to look this up reliably right now, so I have passed "
                "it to a colleague under ticket %s rather than keep you waiting."
                % escalation.ticket_id
            )
        return (
            "I have passed this to a colleague under ticket %s so they can help "
            "you properly." % escalation.ticket_id
        )

    def add_handover_note(self, reply_text: str, escalation: Escalation) -> str:
        """Append the handover sentence to an answer the agent already produced."""
        note = self.handover_message(escalation)
        if reply_text.strip() == "":
            return note
        return reply_text.strip() + "\n\n" + note

    def next_status(self, escalation: Escalation) -> str:
        if escalation.escalated:
            return "escalated"
        return "open"

    def looks_resolved(self, outcome, escalation: Escalation) -> bool:
        """
        A turn counts as resolved when the agent answered from real data and
        nobody needed to be fetched.

        Deliberately conservative. Counting an unverified answer as resolved
        makes the resolution rate go up and the complaints go up with it.
        """
        if escalation.escalated:
            return False
        if outcome.hit_step_limit:
            return False
        if len(outcome.reply_text.strip()) < 15:
            return False

        if not outcome.used_any_tool():
            # Small talk is legitimately resolved with no tools.
            return True

        for trace in outcome.tool_traces:
            if trace.outcome in ("ok", "replayed"):
                return True
        return False
