"""
LAYER 0 - SHARED: THE MODEL CLIENT
==================================
One interface, two implementations, plus a wrapper that enforces the budget.

    OpenAiChatClient      real calls.
    OfflineResearchModel  no network. Does each of the four jobs by rule.
    BudgetedModel         the wrapper every layer actually uses. Refuses to
                          spend when the budget is out, and charges it when it does.

WHY CALLERS PASS A `task` NAME
    The offline model has to do four quite different jobs - plan, extract
    evidence, synthesise, explain a conflict - and guessing which from the prompt
    text is brittle. So the caller says. The real client ignores it; the offline
    one switches on it. Being explicit costs one argument and removes a whole
    class of confusing failure.

WHY BudgetedModel RETURNS None
    When the budget is spent, it returns None instead of raising. Every caller
    then has to decide what a missing answer means for its stage - the planner
    falls back to a simple plan, a worker gives up on its sub-question, synthesis
    writes from the evidence it has. Raising would make "out of budget" an
    accident; returning None makes it a case you had to handle.
"""

import json
import time
from typing import TYPE_CHECKING

from research_agent.layer0_shared.budget import Budget, BudgetLimit
from research_agent.layer0_shared.cost import chat_cost_usd, rough_token_count

if TYPE_CHECKING:
    # Only for type checking, so this module still imports
    # without the OpenAI SDK present.
    from openai.types.chat import ChatCompletionMessageParam

# The four jobs a model does in this system.
TASK_PLAN = "plan"
TASK_EXTRACT = "extract"
TASK_SYNTHESISE = "synthesise"
TASK_EXPLAIN_CONFLICT = "explain_conflict"


class LlmResult:
    def __init__(
        self,
        text: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: int = 0,
    ) -> None:
        self.text = text
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cost_usd = cost_usd
        self.latency_ms = latency_ms

    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class OpenAiChatClient:
    is_live = True

    def __init__(self, api_key: str, model: str, temperature: float) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, timeout=60.0, max_retries=2)
        self.model = model
        self.temperature = temperature

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        task: str = "",
        json_mode: bool = False,
        max_tokens: int = 1200,
    ) -> LlmResult:
        started = time.perf_counter()

        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Two explicit calls rather than one splatted dict. Splatting hides
        # every keyword from the type checker, which is the one thing it is
        # genuinely useful for on somebody else's SDK.
        if json_mode:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=max_tokens,
                messages=messages,
                response_format={"type": "json_object"},
            )
        else:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=max_tokens,
                messages=messages,
            )
        choice = response.choices[0]

        text = choice.message.content
        if text is None:
            text = ""

        prompt_tokens = 0
        completion_tokens = 0
        if response.usage is not None:
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens

        return LlmResult(
            text=text.strip(),
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=chat_cost_usd(self.model, prompt_tokens, completion_tokens),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


class OfflineResearchModel:
    """
    A working stand-in that makes no network calls.

    It is not a language model and does not pretend to be one. It follows rules,
    and those rules are written for this application. What it lets you do is run
    the entire system - planning, parallel workers, budgets, deduplication,
    conflict detection, citation checking, the evaluation suite - with no key and
    no bill. Add a key and the four jobs below are done by a model instead.

    Each job is implemented in its own method so you can read them side by side
    with the prompts in layers 4, 5 and 7 and see exactly what the model is being
    asked to replace.
    """

    is_live = False
    model = "offline-research-rules"

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        task: str = "",
        json_mode: bool = False,
        max_tokens: int = 1200,
    ) -> LlmResult:
        started = time.perf_counter()

        if task == TASK_PLAN:
            text = self.plan(user_prompt)
        elif task == TASK_EXTRACT:
            text = self.extract(user_prompt)
        elif task == TASK_SYNTHESISE:
            text = self.synthesise(user_prompt)
        elif task == TASK_EXPLAIN_CONFLICT:
            text = self.explain_conflict(user_prompt)
        else:
            text = ""

        return LlmResult(
            text=text,
            model=self.model,
            prompt_tokens=rough_token_count(system_prompt) + rough_token_count(user_prompt),
            completion_tokens=rough_token_count(text),
            cost_usd=0.0,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    # --- the four jobs are filled in by the layers that own them ---
    # They are set here rather than imported, so layer 0 does not depend on
    # layers 4 to 7. Each layer registers its rule-based version at import time.

    plan_rule = None
    extract_rule = None
    synthesise_rule = None
    explain_conflict_rule = None

    def plan(self, user_prompt: str) -> str:
        if OfflineResearchModel.plan_rule is None:
            return json.dumps({"sub_questions": []})
        return OfflineResearchModel.plan_rule(user_prompt)

    def extract(self, user_prompt: str) -> str:
        if OfflineResearchModel.extract_rule is None:
            return json.dumps({"findings": []})
        return OfflineResearchModel.extract_rule(user_prompt)

    def synthesise(self, user_prompt: str) -> str:
        if OfflineResearchModel.synthesise_rule is None:
            return ""
        return OfflineResearchModel.synthesise_rule(user_prompt)

    def explain_conflict(self, user_prompt: str) -> str:
        if OfflineResearchModel.explain_conflict_rule is None:
            return "These two sources report different figures for the same question."
        return OfflineResearchModel.explain_conflict_rule(user_prompt)


class BudgetedModel:
    """
    The only thing the rest of the system calls.

    Asks the budget before spending, charges it afterwards, and returns None when
    there is no room. Being the single gate means no layer can accidentally spend
    outside the budget, however it is written.
    """

    def __init__(self, client, budget: Budget) -> None:
        self.client = client
        self.budget = budget

    @property
    def is_live(self) -> bool:
        return self.client.is_live

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        task: str,
        json_mode: bool = False,
        max_tokens: int = 1200,
        for_synthesis: bool = False,
        describe: str = "",
    ) -> LlmResult | None:
        limit = self.budget.check(for_synthesis=for_synthesis)
        if limit != BudgetLimit.NONE:
            if describe == "":
                describe = task
            self.budget.record_refusal(describe, limit)
            return None

        result = self.client.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            task=task,
            json_mode=json_mode,
            max_tokens=max_tokens,
        )
        self.budget.charge_model_call(result.total_tokens(), result.cost_usd)
        return result


def build_chat_client():
    """Choose the client from configuration. The only place that decides."""
    from research_agent.layer1_config.settings import settings

    if settings.using_real_llm():
        return OpenAiChatClient(
            api_key=settings.openai_api_key,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
        )
    return OfflineResearchModel()
