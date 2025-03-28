"""
LAYER 6 - THE AGENT: THE TOOL LOOP
==================================
The loop that makes this an agent rather than a chatbot:

    ask the model  ->  it asks for a tool  ->  run it  ->  give back the result
                   ->  ask the model again  ->  ... until it answers

THE STEP LIMIT IS NOT OPTIONAL
    A confused model will call get_order, get a "not found", call it again with
    the same wrong id, get "not found", and keep going. Without a cap that is an
    infinite loop billed by the token. With one, the worst case is bounded and
    the customer gets an honest "I could not work this out" instead of silence.

WHAT HAPPENS WHEN THE LIMIT IS HIT
    We do not just give up. We ask once more with the tools removed, which forces
    a final answer from whatever was learned. An agent that ran out of steps still
    usually knows something worth saying.

WHY TOOL RESULTS GO BACK AS JSON
    The model reads the same structured result the code does - including
    `ok: false` and the error message. That is what lets it recover: told
    "Missing required argument: order_id", it usually asks the customer for the
    order number instead of failing the turn.
"""

import time

from support_agent.layer0_shared.logging_setup import get_logger, log_event
from support_agent.layer0_shared.metrics import metrics
from support_agent.layer1_config.settings import settings
from support_agent.layer2_models.schemas import Message, ToolCallTrace
from support_agent.layer4_tools.registry import schemas_for
from support_agent.layer6_agent.prompts import FINAL_ANSWER_NUDGE

log = get_logger(__name__)


class ToolLoopResult:
    """Everything one pass of the loop produced."""

    def __init__(
        self,
        reply_text: str,
        tool_traces: list[ToolCallTrace],
        new_messages: list[Message],
        steps_used: int,
        hit_step_limit: bool,
    ) -> None:
        self.reply_text = reply_text
        self.tool_traces = tool_traces
        self.new_messages = new_messages
        self.steps_used = steps_used
        self.hit_step_limit = hit_step_limit

    def used_any_tool(self) -> bool:
        return len(self.tool_traces) > 0


class ToolLoop:
    """Runs the ask-model / run-tool cycle until the agent has an answer."""

    def __init__(self, chat_client, executor) -> None:
        self.chat_client = chat_client
        self.executor = executor

    def run(
        self,
        system_prompt: str,
        context_messages: list[Message],
        tools: list,
        tool_context,
        usage,
    ) -> ToolLoopResult:
        allowed_names: list[str] = []
        for tool in tools:
            allowed_names.append(tool.name)

        tool_schemas = schemas_for(tools)

        # `working` is what we send to the model. `new_messages` is what we will
        # save to the conversation afterwards - the context we were handed is
        # already stored.
        working: list[Message] = list(context_messages)
        new_messages: list[Message] = []
        traces: list[ToolCallTrace] = []

        step = 0
        while step < settings.max_tool_steps:
            step = step + 1
            started = time.perf_counter()

            reply = self.chat_client.respond(
                system_prompt=system_prompt,
                messages=working,
                tools=tool_schemas,
            )
            usage.add_model_call("agent_step", reply.prompt_tokens, reply.completion_tokens, reply.cost_usd)
            metrics.observe("model_call_latency_ms", int((time.perf_counter() - started) * 1000))

            # --- the agent has an answer ---
            if not reply.wants_tools():
                answer = Message(role="assistant", content=reply.text)
                new_messages.append(answer)

                log_event(
                    log,
                    "agent.answered",
                    steps_used=step,
                    tools_called=len(traces),
                )
                return ToolLoopResult(reply.text, traces, new_messages, step, hit_step_limit=False)

            # --- the agent wants tools ---
            assistant_turn = Message(
                role="assistant",
                content=reply.text,
                tool_calls=reply.tool_calls,
            )
            working.append(assistant_turn)
            new_messages.append(assistant_turn)

            for call in reply.tool_calls:
                log_event(log, "agent.calling_tool", tool=call.tool_name, step=step)

                result, trace = self.executor.execute(call, tool_context, allowed_names)
                traces.append(trace)

                tool_message = Message(
                    role="tool",
                    content=result.model_dump_json(),
                    tool_call_id=call.call_id,
                    tool_name=call.tool_name,
                )
                working.append(tool_message)
                new_messages.append(tool_message)

        # --- the step limit was reached ---
        metrics.increment("step_limit_reached_total")
        log_event(log, "agent.step_limit_reached", steps=settings.max_tool_steps)

        nudge = Message(role="user", content=FINAL_ANSWER_NUDGE)
        working.append(nudge)

        final = self.chat_client.respond(
            system_prompt=system_prompt,
            messages=working,
            tools=None,          # no tools, so it has to answer
        )
        usage.add_model_call("forced_answer", final.prompt_tokens, final.completion_tokens, final.cost_usd)

        text = final.text
        if text.strip() == "":
            text = (
                "I have not been able to get to the bottom of this. Let me pass "
                "you to a colleague who can look into it properly."
            )

        answer = Message(role="assistant", content=text)
        new_messages.append(answer)

        return ToolLoopResult(text, traces, new_messages, settings.max_tool_steps, hit_step_limit=True)
