"""
LAYER 4 - TOOLS: THE EXECUTOR
=============================
Nothing calls a tool directly. Everything goes through here, because the same
five things must happen around every single call:

    1. PERMISSION   is this tool even allowed for this conversation's intent?
    2. VALIDATION   are the model's arguments usable?
    3. IDEMPOTENCY  have we already done exactly this? Then replay, do not repeat.
    4. RETRIES      transient failure, try again. Permanent failure, stop.
    5. AUDIT        write down what happened, including refusals.

Putting this in one place is the whole point. If retries lived in each tool,
some tool would eventually forget, and it would be the expensive one.

------------------------------------------------------------------------------
THE IDEMPOTENCY RULE, SPELLED OUT
------------------------------------------------------------------------------
A write tool gets a key derived from the conversation, the tool name and the
arguments. Before running, we look the key up:

    not seen before        -> run it, store the result under the key
    seen, same arguments   -> return the STORED result, do not run it again
    seen, different args   -> refuse. Same key with a different payload means a
                              bug somewhere, and guessing which one is right is
                              how you refund the wrong amount.

Without this, three ordinary events each pay the customer twice: the agent loop
retrying after a timeout that actually succeeded, the customer pressing send
twice, and a network error on the way back from a write that already happened.
"""

import hashlib
import json
import time

from support_agent.layer0_shared.logging_setup import get_logger, log_event
from support_agent.layer0_shared.metrics import metrics
from support_agent.layer0_shared.pii import redact
from support_agent.layer1_config.settings import settings
from support_agent.layer2_models.schemas import (
    AuditRecord,
    ToolCall,
    ToolCallTrace,
    ToolResult,
    now_utc_text,
)
from support_agent.layer4_tools.base import ToolContext, clean_arguments, validate_arguments
from support_agent.layer4_tools.registry import find_tool

log = get_logger(__name__)


def canonical_arguments(arguments: dict) -> str:
    """
    A stable text form of the arguments.

    Sorted keys, so {"a":1,"b":2} and {"b":2,"a":1} produce the same text and
    therefore the same idempotency key. Without sorting, the key would depend on
    whatever order the model happened to emit the fields in.
    """
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"))


def build_idempotency_key(conversation_id: str, tool_name: str, arguments: dict) -> str:
    raw = conversation_id + "|" + tool_name + "|" + canonical_arguments(arguments)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def hash_request(tool_name: str, arguments: dict) -> str:
    raw = tool_name + "|" + canonical_arguments(arguments)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class ToolExecutor:
    """Runs tools safely. The only way a tool ever gets called."""

    def __init__(self, database) -> None:
        self.database = database

    def execute(
        self,
        call: ToolCall,
        context: ToolContext,
        allowed_tool_names: list[str],
    ) -> tuple[ToolResult, ToolCallTrace]:
        started = time.perf_counter()
        metrics.increment("tool_calls_total")

        trace = ToolCallTrace(
            tool_name=call.tool_name,
            risk="unknown",
            arguments=dict(call.arguments),
        )

        # ---------- does the tool exist? ----------
        tool = find_tool(call.tool_name)
        if tool is None:
            # The model invented a tool name. Tell it so, clearly, and let it
            # try again rather than failing the whole conversation.
            result = ToolResult(
                ok=False,
                error_code="unknown_tool",
                error_message=(
                    "There is no tool called '%s'. Available tools: %s"
                    % (call.tool_name, ", ".join(allowed_tool_names))
                ),
                retryable=False,
            )
            self.record(context, call, result, trace, allowed=False, outcome="unknown_tool")
            return self.finish(result, trace, started, outcome="unknown_tool")

        trace.risk = tool.risk.value

        # ---------- 1. permission ----------
        if call.tool_name not in allowed_tool_names:
            metrics.increment("tool_calls_denied_total")
            result = ToolResult(
                ok=False,
                error_code="not_allowed",
                error_message=(
                    "The tool '%s' is not available for this kind of request."
                    % call.tool_name
                ),
                retryable=False,
            )
            trace.allowed = False
            self.record(context, call, result, trace, allowed=False, outcome="denied")
            return self.finish(result, trace, started, outcome="denied")

        # ---------- 2. validation ----------
        ok, message = validate_arguments(call.arguments, tool.parameters)
        if not ok:
            metrics.increment("tool_calls_invalid_args_total")
            result = ToolResult(
                ok=False,
                error_code="invalid_arguments",
                error_message=message,
                retryable=False,
            )
            self.record(context, call, result, trace, allowed=True, outcome="invalid_arguments")
            return self.finish(result, trace, started, outcome="invalid_arguments")

        arguments = clean_arguments(call.arguments, tool.parameters)
        trace.arguments = dict(arguments)

        # ---------- 3. idempotency ----------
        idempotency_key = ""
        if tool.needs_idempotency_key:
            idempotency_key = build_idempotency_key(context.conversation_id, tool.name, arguments)
            request_fingerprint = hash_request(tool.name, arguments)

            stored = self.database.read_idempotent_response(idempotency_key)
            if stored is not None:
                if stored["request_hash"] != request_fingerprint:
                    result = ToolResult(
                        ok=False,
                        error_code="idempotency_conflict",
                        error_message=(
                            "This request reuses an idempotency key with different "
                            "arguments. Refusing rather than guessing which is correct."
                        ),
                        retryable=False,
                    )
                    self.record(context, call, result, trace, allowed=True, outcome="idempotency_conflict")
                    return self.finish(result, trace, started, outcome="idempotency_conflict")

                metrics.increment("tool_calls_replayed_total")
                replayed = ToolResult(**json.loads(stored["response_json"]))
                trace.idempotent_replay = True

                log_event(
                    log,
                    "tool.idempotent_replay",
                    tool=tool.name,
                    conversation_id=context.conversation_id,
                )
                self.record(context, call, replayed, trace, allowed=True, outcome="replayed")
                return self.finish(replayed, trace, started, outcome="replayed")

        # ---------- 4. run, with retries ----------
        result = self.run_with_retries(tool, arguments, context, trace)

        # ---------- store the result for idempotency ----------
        # Only successful writes are stored. Storing a failure would make the
        # retry that was supposed to fix it replay the failure forever.
        if idempotency_key != "" and result.ok:
            self.database.store_idempotent_response(
                key=idempotency_key,
                tool_name=tool.name,
                request_hash=hash_request(tool.name, arguments),
                response_json=result.model_dump_json(),
                when=now_utc_text(),
            )

        # ---------- 5. audit ----------
        if result.ok:
            outcome = "ok"
            metrics.increment("tool_calls_ok_total")
        elif result.needs_approval:
            outcome = "needs_approval"
            metrics.increment("tool_calls_need_approval_total")

            # Record what will ACTUALLY happen if a human approves, not what the
            # model typed. issue_refund treats amount 0 as "the whole order", so
            # the raw arguments say 0.00 while the real figure is 899.00. A human
            # approving a refund must see the number they are approving.
            for key in result.data:
                if key.startswith("__"):
                    continue
                trace.arguments[key] = result.data[key]
        else:
            outcome = "failed"
            metrics.increment("tool_calls_failed_total")

        self.record(context, call, result, trace, allowed=True, outcome=outcome)
        return self.finish(result, trace, started, outcome=outcome)

    # ------------------------------------------------------------------

    def run_with_retries(self, tool, arguments: dict, context: ToolContext, trace: ToolCallTrace) -> ToolResult:
        """
        Try the tool, retrying only failures that could plausibly be different
        next time.

        Retrying "no such order" three times does not find the order. It just
        makes the customer wait three times as long for the same answer.
        """
        attempt = 0
        last_result = ToolResult(ok=False, error_code="never_ran", error_message="tool never ran")

        while attempt < settings.tool_max_attempts:
            attempt = attempt + 1
            trace.attempts = attempt

            try:
                last_result = tool.run(arguments, context)
            except Exception as error:
                # An exception is always worth one more try: it is usually a
                # connection or a lock, not a logic error.
                last_result = ToolResult(
                    ok=False,
                    error_code="tool_exception",
                    error_message=str(error),
                    retryable=True,
                )
                log.exception("tool %s raised", tool.name)

            if last_result.ok or not last_result.retryable:
                return last_result

            if attempt < settings.tool_max_attempts:
                metrics.increment("tool_retries_total")
                delay = settings.tool_retry_base_seconds * (2 ** (attempt - 1))
                log_event(
                    log,
                    "tool.retrying",
                    tool=tool.name,
                    attempt=attempt,
                    delay_seconds=round(delay, 3),
                    reason=last_result.error_code,
                )
                time.sleep(delay)

        return last_result

    def record(
        self,
        context: ToolContext,
        call: ToolCall,
        result: ToolResult,
        trace: ToolCallTrace,
        allowed: bool,
        outcome: str,
    ) -> None:
        """Write the audit row. Never skipped, especially not for refusals."""
        detail_parts = [canonical_arguments(trace.arguments)]

        if not result.ok:
            detail_parts.append(result.short_summary())

        # A refusal caused by someone reading another customer's data is the row
        # you most want to be able to search for later.
        note = result.data.get("__audit_note__", "")
        if note != "":
            detail_parts.append("note=" + note)
            metrics.increment("cross_customer_attempts_total")

        detail = " | ".join(detail_parts)
        if settings.redact_pii_in_logs:
            detail, _kinds = redact(detail)

        record = AuditRecord(
            conversation_id=context.conversation_id,
            actor=context.caller.role + ":" + context.caller.customer_id,
            customer_id=context.customer_id,
            action="tool_call",
            tool_name=call.tool_name,
            risk=trace.risk,
            allowed=allowed,
            outcome=outcome,
            detail=detail,
        )
        self.database.append_audit(record)

    def finish(self, result: ToolResult, trace: ToolCallTrace, started: float, outcome: str):
        trace.outcome = outcome
        trace.summary = result.short_summary()
        trace.latency_ms = int((time.perf_counter() - started) * 1000)
        metrics.observe("tool_latency_ms", trace.latency_ms)

        # The audit note is internal. It must never reach the model or the
        # customer, or the refusal stops being indistinguishable from "not found".
        if "__audit_note__" in result.data:
            cleaned = dict(result.data)
            del cleaned["__audit_note__"]
            result = result.model_copy(update={"data": cleaned})

        return (result, trace)
