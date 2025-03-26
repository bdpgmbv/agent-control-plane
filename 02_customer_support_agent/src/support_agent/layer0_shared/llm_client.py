"""
LAYER 0 - SHARED: THE MODEL CLIENT (with tool calling)
======================================================
One interface, two implementations:

    OpenAiChatClient    real function calling with gpt-4o-mini.
    OfflineRulePlanner  no network. Decides which tool to call from keyword
                        rules, and writes the reply from the tool's data.

WHY THE OFFLINE ONE IS NOT A TOY
    It drives the *same* agent loop: it emits real tool calls, gets real results
    back, and produces a real reply. So permissions, retries, idempotency, the
    step limit, the audit log and escalation are all genuinely exercised by the
    test suite, with no key and no bill.

    What it cannot do is generalise. It knows this application's five tools by
    name, because somebody wrote those rules by hand. That is exactly the
    difference a model buys you: hand-written rules per application, versus a
    model reading the tool schemas and working it out. Swapping in the real
    client is how you see the difference.

MESSAGE FORMAT
    The rest of the codebase uses our own Message objects (layer 2). Converting
    to OpenAI's shape happens here and only here, so changing provider never
    touches the agent.
"""

import json
import re
import time
import uuid
from typing import Any

from support_agent.layer0_shared.cost import chat_cost_usd, rough_token_count
from support_agent.layer2_models.schemas import Message, ToolCall


class LlmReply:
    """What the model (or the planner) decided to do."""

    def __init__(
        self,
        text: str = "",
        tool_calls: list[ToolCall] | None = None,
        model: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: int = 0,
        finish_reason: str = "stop",
    ) -> None:
        self.text = text
        if tool_calls is None:
            tool_calls = []
        self.tool_calls = tool_calls
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cost_usd = cost_usd
        self.latency_ms = latency_ms
        self.finish_reason = finish_reason

    def wants_tools(self) -> bool:
        return len(self.tool_calls) > 0


def new_call_id() -> str:
    return "call_" + uuid.uuid4().hex[:10]


# ---------------------------------------------------------------------------
#  Converting our messages to the provider's shape
# ---------------------------------------------------------------------------

def to_openai_messages(system_prompt: str, messages: list[Message]) -> list[dict]:
    """Translate our Message objects into the dicts the OpenAI SDK expects."""
    result: list[dict] = []

    if system_prompt != "":
        result.append({"role": "system", "content": system_prompt})

    for message in messages:
        if message.role == "tool":
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": message.content,
                }
            )
            continue

        if message.role == "assistant" and len(message.tool_calls) > 0:
            calls: list[dict] = []
            for call in message.tool_calls:
                calls.append(
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.tool_name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                )

            entry: dict[str, Any] = {"role": "assistant", "tool_calls": calls}
            if message.content != "":
                entry["content"] = message.content
            else:
                entry["content"] = None
            result.append(entry)
            continue

        result.append({"role": message.role, "content": message.content})

    return result


# ---------------------------------------------------------------------------
#  The real client
# ---------------------------------------------------------------------------

class OpenAiChatClient:
    """Real function calling against an OpenAI chat model."""

    is_live = True

    def __init__(self, api_key: str, model: str, temperature: float, max_output_tokens: int) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, timeout=60.0, max_retries=2)
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def respond(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> LlmReply:
        started = time.perf_counter()

        arguments: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "messages": to_openai_messages(system_prompt, messages),
        }
        if tools is not None and len(tools) > 0:
            arguments["tools"] = tools
            arguments["tool_choice"] = "auto"

        response = self.client.chat.completions.create(**arguments)
        choice = response.choices[0]

        text = choice.message.content
        if text is None:
            text = ""

        tool_calls: list[ToolCall] = []
        raw_calls = getattr(choice.message, "tool_calls", None)
        if raw_calls is not None:
            for raw in raw_calls:
                # The model writes the arguments as a JSON string. It is usually
                # valid; when it is not, we must not crash the whole turn.
                try:
                    parsed = json.loads(raw.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    parsed = {"__invalid_json__": str(raw.function.arguments)}

                if not isinstance(parsed, dict):
                    parsed = {"__invalid_json__": str(raw.function.arguments)}

                tool_calls.append(
                    ToolCall(call_id=raw.id, tool_name=raw.function.name, arguments=parsed)
                )

        prompt_tokens = 0
        completion_tokens = 0
        if response.usage is not None:
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens

        return LlmReply(
            text=text.strip(),
            tool_calls=tool_calls,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=chat_cost_usd(self.model, prompt_tokens, completion_tokens),
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason=str(choice.finish_reason),
        )


# ---------------------------------------------------------------------------
#  The offline planner
# ---------------------------------------------------------------------------

ORDER_ID_PATTERN = re.compile(r"\bORD-\d+\b", re.IGNORECASE)

# Any of our own identifiers. Removed from the text before looking for money,
# because "ORD-10025" contains the digits 10025 and a naive money pattern will
# happily read that as a ten-thousand-dollar refund. It did, on the first run.
IDENTIFIER_PATTERN = re.compile(r"\b(?:ORD|REF|TICK|CUST)-[A-Z0-9]+\b", re.IGNORECASE)

# A money amount must look like money: either a currency marker, or pence.
# A bare integer in a sentence is far more often a quantity or a date.
MONEY_PATTERN = re.compile(
    r"(?:[$£€]\s*(\d+(?:\.\d{1,2})?))"
    r"|(?:\b(\d+\.\d{2})\b)"
    r"|(?:\b(\d+(?:\.\d{1,2})?)\s*(?:dollars|usd|pounds|gbp|euros|eur)\b)",
    re.IGNORECASE,
)

# Keyword rules, each phrase carrying a weight.
#
# SCORED, NOT FIRST-MATCH. The first version stopped at the first rule that
# matched anything, and "I want a refund for ORD-10024, it arrived faulty"
# matched the order-lookup rule on the word "arrived" before ever reaching the
# refund rule. It looked up the order and told the customer it had been
# delivered, which is true, useless, and not what they asked for.
#
# Weights: 3 is a decisive phrase, 1 is a hint that needs company.
# Written by hand for this application only - see the note at the top of the file.
TOOL_KEYWORDS = [
    (
        "issue_refund",
        [("want a refund", 3), ("refund me", 3), ("give me my money back", 3),
         ("request a refund", 3), ("like a refund", 3), ("refund please", 3),
         ("money back", 3), ("refund for", 3), ("get a refund", 3)],
    ),
    (
        "get_refund_status",
        [("refund status", 3), ("where is my refund", 3), ("refund yet", 3),
         ("processed my refund", 3), ("refund been", 3), ("my refund for", 3)],
    ),
    (
        "get_order",
        [("where is my order", 3), ("order status", 3), ("has my order", 2),
         ("tracking", 2), ("dispatch", 2), ("delivered", 1), ("shipped", 1),
         ("arrive", 1)],
    ),
    (
        "search_help_articles",
        [("refund policy", 3), ("return policy", 3), ("what is your policy", 3),
         ("can i return", 3), ("how long do i have", 3), ("warranty", 2),
         ("policy", 1), ("how do i", 1), ("rules", 1)],
    ),
]


class OfflineRulePlanner:
    """A rule-based stand-in that drives the real agent loop."""

    is_live = False
    model = "offline-rule-planner"

    def respond(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> LlmReply:
        started = time.perf_counter()

        available_names: list[str] = []
        if tools is not None:
            for tool in tools:
                available_names.append(tool["function"]["name"])

        tool_results = self.collect_tool_results(messages)
        last_user_text = self.last_user_message(messages)

        # If tools have already run this turn, write the reply from their data.
        if len(tool_results) > 0:
            text = self.compose_reply(last_user_text, tool_results)
            return self.finish(started, system_prompt, messages, text=text)

        chosen = self.choose_tool(last_user_text, available_names)
        if chosen is None:
            return self.finish(started, system_prompt, messages, text=self.small_talk_reply(last_user_text))

        tool_name, arguments = chosen
        call = ToolCall(call_id=new_call_id(), tool_name=tool_name, arguments=arguments)
        return self.finish(started, system_prompt, messages, tool_calls=[call])

    # ---------- reading the conversation ----------

    def last_user_message(self, messages: list[Message]) -> str:
        text = ""
        for message in messages:
            if message.role == "user":
                text = message.content
        return text

    def collect_tool_results(self, messages: list[Message]) -> list[tuple[str, dict]]:
        """Every tool result since the last user message, as (tool name, payload)."""
        results: list[tuple[str, dict]] = []

        seen_last_user = False
        position = len(messages) - 1
        boundary = 0
        while position >= 0:
            if messages[position].role == "user":
                boundary = position
                seen_last_user = True
                break
            position = position - 1

        if not seen_last_user:
            boundary = 0

        position = boundary
        while position < len(messages):
            message = messages[position]
            if message.role == "tool":
                try:
                    payload = json.loads(message.content)
                except json.JSONDecodeError:
                    payload = {"raw": message.content}
                results.append((message.tool_name, payload))
            position = position + 1

        return results

    # ---------- deciding ----------

    def choose_tool(self, user_text: str, available_names: list[str]) -> tuple[str, dict] | None:
        lowered = user_text.lower()

        order_match = ORDER_ID_PATTERN.search(user_text)
        order_id = ""
        if order_match is not None:
            order_id = order_match.group().upper()

        # Score every rule and take the strongest, rather than stopping at the
        # first one that matches at all.
        best_tool = ""
        best_score = 0

        for tool_name, weighted_phrases in TOOL_KEYWORDS:
            if tool_name not in available_names:
                continue

            score = 0
            for phrase, weight in weighted_phrases:
                if phrase in lowered:
                    score = score + weight

            if score > best_score:
                best_score = score
                best_tool = tool_name

        if best_tool == "":
            # An order number and nothing else almost always means "where is it?"
            if order_id != "" and "get_order" in available_names:
                return ("get_order", {"order_id": order_id})
            if "search_help_articles" in available_names and len(lowered.split()) > 3:
                return ("search_help_articles", {"query": user_text})
            return None

        # A lookup that needs an order id, with no order id in the message. Ask
        # the account instead of calling the tool with nothing and getting an
        # error back - that wastes a step and tells the customer nothing.
        if best_tool in ("get_order", "get_refund_status") and order_id == "":
            if "list_my_orders" in available_names:
                return ("list_my_orders", {})

        if best_tool == "get_order":
            return ("get_order", {"order_id": order_id})
        if best_tool == "get_refund_status":
            return ("get_refund_status", {"order_id": order_id})
        if best_tool == "issue_refund":
            if order_id == "" and "list_my_orders" in available_names:
                return ("list_my_orders", {})
            return (
                "issue_refund",
                {
                    "order_id": order_id,
                    "amount": self.guess_amount(user_text),
                    "reason": "customer requested a refund",
                },
            )
        return ("search_help_articles", {"query": user_text})

    def guess_amount(self, text: str) -> float:
        """
        Pull a money amount out of the message, or 0 meaning "the whole order".

        Identifiers are stripped first. Returning 0 when unsure is the safe
        default: the refund tool then refunds the order total, which is a number
        we looked up rather than one we guessed from the customer's sentence.
        """
        without_identifiers = IDENTIFIER_PATTERN.sub(" ", text)

        found = MONEY_PATTERN.search(without_identifiers)
        if found is None:
            return 0.0

        for captured in found.groups():
            if captured is None:
                continue
            try:
                return float(captured)
            except ValueError:
                continue
        return 0.0

    # ---------- writing the reply ----------

    def compose_reply(self, user_text: str, tool_results: list[tuple[str, dict]]) -> str:
        parts: list[str] = []

        for tool_name, payload in tool_results:
            if payload.get("ok") is False:
                parts.append(self.describe_failure(tool_name, payload))
                continue

            data = payload.get("data", {})
            if tool_name == "get_order":
                parts.append(self.describe_order(data))
            elif tool_name == "get_refund_status":
                parts.append(self.describe_refund(data))
            elif tool_name == "search_help_articles":
                parts.append(self.describe_article(data))
            elif tool_name == "list_my_orders":
                parts.append(self.describe_order_list(data))
            elif tool_name == "create_ticket":
                parts.append(
                    "I have created ticket %s and a member of the team will follow up."
                    % data.get("ticket_id", "")
                )
            elif tool_name == "issue_refund":
                parts.append(
                    "Your refund of %.2f has been issued (reference %s). It takes 5 to 10 "
                    "business days to reach your account."
                    % (data.get("amount", 0.0), data.get("refund_id", ""))
                )
            else:
                parts.append(json.dumps(data))

        if len(parts) == 0:
            return "I could not find anything useful for that. Let me pass you to a colleague."
        return " ".join(parts)

    def describe_failure(self, tool_name: str, payload: dict) -> str:
        """
        Turn a tool failure into something a customer can act on.

        Every one of these was a real failure mode that first came out as
        "I ran into a problem looking that up: <internal message>", which tells
        the customer nothing and reads like a broken system.
        """
        if payload.get("needs_approval") is True:
            return (
                "I can start that, but a refund of this size needs a colleague to "
                "approve it first. %s" % payload.get("approval_reason", "")
            )

        code = payload.get("error_code", "")
        data = payload.get("data", {})

        if code == "not_found":
            return "I could not find that order on your account. Could you double-check the number?"
        if code == "already_refunded":
            return (
                "There is already a refund on that order (%s), currently %s. "
                "A second one would pay you twice, so I have not started it."
                % (data.get("refund_id", ""), data.get("status", "in progress"))
            )
        if code == "not_delivered":
            return (
                "That order has not been delivered yet, so it cannot be refunded. "
                "While it is still on its way I can look into cancelling it instead."
            )
        if code == "outside_refund_window":
            return (
                "That order is outside our 30 day refund window, so I cannot "
                "refund it myself. A colleague can still look at making an exception."
            )
        if code == "amount_too_large":
            return "That is more than the order was worth, so I have not processed it."
        if code == "above_hard_limit":
            return "That amount is beyond what can be handled here. A manager needs to deal with it."
        if code == "invalid_arguments":
            return "I need a bit more detail before I can look that up. Which order is it about?"

        return (
            "I was not able to complete that just now. Let me get a colleague to "
            "pick it up rather than leave you waiting."
        )

    def describe_order(self, data: dict) -> str:
        status = data.get("status", "unknown")
        order_id = data.get("order_id", "")

        sentence = "Order %s is currently %s." % (order_id, status.replace("_", " "))

        tracking = data.get("tracking_number", "")
        carrier = data.get("carrier", "")
        if tracking != "":
            sentence = sentence + " It is with %s under tracking number %s." % (carrier, tracking)

        expected = data.get("expected_delivery", "")
        if expected != "" and status != "delivered":
            sentence = sentence + " It is expected on %s." % expected

        delivered = data.get("delivered_at", "")
        if delivered != "":
            sentence = sentence + " It was delivered on %s." % delivered[:10]

        return sentence

    def describe_refund(self, data: dict) -> str:
        if data.get("has_refund") is False:
            return "There is no refund on that order yet."
        return "Your refund of %.2f for order %s is %s (reference %s)." % (
            data.get("amount", 0.0),
            data.get("order_id", ""),
            data.get("status", "unknown"),
            data.get("refund_id", ""),
        )

    def describe_order_list(self, data: dict) -> str:
        orders = data.get("orders", [])
        if len(orders) == 0:
            return "I cannot see any orders on your account."

        lines: list[str] = []
        for order in orders[:4]:
            lines.append("%s (%s, %s)" % (order["order_id"], order["summary"], order["status"]))
        return "Here are your recent orders: " + "; ".join(lines) + ". Which one is it about?"

    def describe_article(self, data: dict) -> str:
        articles = data.get("articles", [])
        if len(articles) == 0:
            return "I could not find anything in our help pages about that."
        return articles[0].get("body", "")

    def small_talk_reply(self, user_text: str) -> str:
        lowered = user_text.lower()
        if "thank" in lowered:
            return "You are very welcome. Is there anything else I can help with?"
        if "hello" in lowered or "hi " in lowered or lowered.strip() in ("hi", "hey"):
            return "Hello. I can help with orders, deliveries, refunds and returns. What do you need?"
        return (
            "I can help with order status, deliveries, refunds and our returns policy. "
            "Could you tell me a little more about what you need?"
        )

    # ---------- bookkeeping ----------

    def finish(self, started, system_prompt, messages, text="", tool_calls=None) -> LlmReply:
        prompt_tokens = rough_token_count(system_prompt)
        for message in messages:
            prompt_tokens = prompt_tokens + rough_token_count(message.content)

        return LlmReply(
            text=text,
            tool_calls=tool_calls,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=rough_token_count(text),
            cost_usd=0.0,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


def build_chat_client():
    """Choose the client from configuration. The only place that decides."""
    from support_agent.layer1_config.settings import settings

    if settings.using_real_llm():
        return OpenAiChatClient(
            api_key=settings.openai_api_key,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            max_output_tokens=settings.llm_max_output_tokens,
        )
    return OfflineRulePlanner()
