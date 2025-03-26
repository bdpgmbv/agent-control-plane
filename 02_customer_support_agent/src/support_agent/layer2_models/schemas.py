"""
LAYER 2 - DATA SHAPES
=====================
The objects every layer agrees on, and the JSON the API accepts and returns.

Three groups worth reading carefully:

  * Message / ToolCall  - the conversation format. Kept provider-neutral here
                          and converted to OpenAI's shape inside the client, so
                          swapping providers never touches the agent.
  * ToolResult          - note `retryable`. A tool that fails has to say whether
                          trying again could possibly help, or the agent will
                          hammer a permanently broken thing three times.
  * AuditRecord         - the answer to "what did the agent actually do, and on
                          whose authority?" Written for every tool call, always.
"""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


def now_utc_text() -> str:
    return datetime.now(UTC).isoformat()


# =============================================================
#  Conversation
# =============================================================

class ToolCall(BaseModel):
    """The agent asking for a tool to be run."""

    call_id: str
    tool_name: str
    arguments: dict = Field(default_factory=dict)


class Message(BaseModel):
    """One turn. Provider-neutral on purpose."""

    role: str                                   # system | user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str = ""                      # set on a tool result message
    tool_name: str = ""                         # set on a tool result message
    created_at: str = Field(default_factory=now_utc_text)


# =============================================================
#  Tools
# =============================================================

class ToolRisk(str, Enum):
    """
    How much damage a tool can do. This decides who may run it and whether a
    human has to approve first.

      READ        looks at data, changes nothing.
      WRITE_LOW   creates something reversible, like a support ticket.
      WRITE_HIGH  moves money or changes an order. Never automatic above a limit.
    """

    READ = "read"
    WRITE_LOW = "write_low"
    WRITE_HIGH = "write_high"


class ToolResult(BaseModel):
    """What a tool hands back."""

    ok: bool
    data: dict = Field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""

    # Could trying again possibly help? A timeout, yes. "No such order", never.
    # Retrying a permanent failure just wastes time and money and looks broken.
    retryable: bool = False

    # Set when the tool refuses because a human must approve first.
    needs_approval: bool = False
    approval_reason: str = ""

    def short_summary(self) -> str:
        """One line for the trace and the UI."""
        if self.ok:
            return "ok"
        if self.needs_approval:
            return "needs human approval: " + self.approval_reason
        return "failed (%s): %s" % (self.error_code, self.error_message)


# =============================================================
#  Routing and escalation
# =============================================================

class Intent(str, Enum):
    """What the customer is trying to do. Decides which tools are even offered."""

    ORDER_STATUS = "order_status"
    REFUND_REQUEST = "refund_request"
    RETURN_POLICY = "return_policy"
    DELIVERY_PROBLEM = "delivery_problem"
    BILLING = "billing"
    COMPLAINT = "complaint"
    TECHNICAL = "technical"
    SMALL_TALK = "small_talk"
    ABUSE = "abuse"
    UNKNOWN = "unknown"


class IntentDecision(BaseModel):
    """The router's verdict, with its reasoning kept for the trace."""

    intent: Intent = Intent.UNKNOWN
    confidence: float = 0.0
    reason: str = ""
    matched_signals: list[str] = Field(default_factory=list)


class EscalationReason(str, Enum):
    """Why a conversation was handed to a person."""

    CUSTOMER_ASKED = "customer_asked"
    REPEATED_TOOL_FAILURE = "repeated_tool_failure"
    NEEDS_APPROVAL = "needs_approval"
    ANGRY_CUSTOMER = "angry_customer"
    ABUSE = "abuse"
    TOO_MANY_TURNS = "too_many_turns"
    AGENT_UNCERTAIN = "agent_uncertain"
    POLICY_LIMIT = "policy_limit"


class Escalation(BaseModel):
    """A handover to a human."""

    escalated: bool = False
    reason: EscalationReason | None = None
    explanation: str = ""
    ticket_id: str = ""


# =============================================================
#  Audit
# =============================================================

class AuditRecord(BaseModel):
    """
    One line in the permanent record.

    Written for every tool call, every approval, every escalation - whether it
    succeeded or not. Refusals matter most: "the agent tried to read another
    customer's order and was stopped" is exactly what you need to see.
    """

    audit_id: str = ""
    conversation_id: str = ""
    actor: str = ""              # which API key / role acted
    customer_id: str = ""
    action: str = ""             # e.g. "tool_call", "approval_granted"
    tool_name: str = ""
    risk: str = ""
    allowed: bool = True
    outcome: str = ""            # ok | failed | denied | needs_approval
    detail: str = ""             # PII-redacted
    created_at: str = Field(default_factory=now_utc_text)


# =============================================================
#  API request and response shapes
# =============================================================

class ChatRequest(BaseModel):
    """A customer message."""

    message: str
    conversation_id: str = ""     # blank starts a new conversation


class ToolCallTrace(BaseModel):
    """What one tool call did, for the UI and the evaluation suite."""

    tool_name: str
    risk: str
    arguments: dict = Field(default_factory=dict)
    allowed: bool = True
    attempts: int = 1
    outcome: str = ""
    summary: str = ""
    latency_ms: int = 0
    idempotent_replay: bool = False


class UsageReport(BaseModel):
    """Cost and latency for one turn."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    latency_ms: int = 0
    model_calls: int = 0
    by_stage: dict = Field(default_factory=dict)


class ChatResponse(BaseModel):
    """The agent's reply to one customer message."""

    conversation_id: str
    reply: str
    intent: str = Intent.UNKNOWN.value
    intent_confidence: float = 0.0
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    escalation: Escalation = Field(default_factory=Escalation)
    resolved: bool = False
    pii_redacted: list[str] = Field(default_factory=list)
    usage: UsageReport = Field(default_factory=UsageReport)
    request_id: str = ""


class ApprovalRequest(BaseModel):
    """A human approving or rejecting a held action."""

    approval_id: str
    approve: bool
    note: str = ""
