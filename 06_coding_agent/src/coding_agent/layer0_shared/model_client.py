"""
LAYER 0 - TALKING TO THE MODEL
==============================
Same shape as project 05: a thin wrapper, typed failures, and `None` for offline.

`build_chat_client()` returns None when there is no key, and every caller has a
visible `if client is None:` branch. In this project that branch does something
different from project 05, and it is worth being precise about what.

A document pipeline can extract fields without a model. A coding agent cannot
write a patch without one. So offline mode here does not "fix the bug by other
means" - it replays a recorded plan for each benchmark task. That exercises the
whole harness (the sandbox, the edit applier, the test runner, the verifier)
without exercising the model's reasoning, which is exactly the split this project
needs: the harness is the part that has to be right every time, and it should be
testable by anyone who clones the repository without a key.

The README says this plainly. Offline numbers measure the harness. Live numbers
measure the agent.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from coding_agent.layer1_config.settings import SETTINGS

if TYPE_CHECKING:
    # Only needed for type checking, so importing this module never
    # requires the OpenAI SDK to be installed - which is what keeps
    # offline mode genuinely dependency-free.
    from openai.types.chat import ChatCompletionMessageParam


class ModelUnavailable(Exception):
    def __init__(self, message: str, kind: str = "unknown") -> None:
        super().__init__(message)
        self.kind = kind

    def friendly_message(self) -> str:
        if self.kind == "no_credit":
            return ("The OpenAI account has no credit, so the agent could not "
                    "call the model.")
        if self.kind == "rate_limited":
            return "The model is rate limited. Try again in a moment."
        if self.kind == "bad_key":
            return "The OPENAI_API_KEY in .env was rejected."
        if self.kind == "unreachable":
            return "Could not reach OpenAI. Check the network connection."
        return "The model could not be reached: %s" % str(self)


@dataclass
class ModelReply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def classify_openai_error(error: Exception) -> str:
    description = ("%s %s" % (type(error).__name__, str(error))).lower()
    if "insufficient_quota" in description or "exceeded your current quota" in description:
        return "no_credit"
    if "rate_limit" in description or "429" in description:
        return "rate_limited"
    if "authentication" in description or "invalid_api_key" in description or "401" in description:
        return "bad_key"
    if "connection" in description or "timeout" in description or "timed out" in description:
        return "unreachable"
    return "unknown"


class OpenAIChatClient:
    def __init__(self, api_key: str, model: str, temperature: float,
                 timeout_seconds: float) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, timeout=timeout_seconds)
        self.model = model
        self.temperature = temperature

    def complete(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 2000) -> ModelReply:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=max_tokens,
            )
        except Exception as error:
            raise ModelUnavailable(str(error), classify_openai_error(error)) from error

        text = ""
        if len(response.choices) > 0:
            content = response.choices[0].message.content
            if content is not None:
                text = content

        input_tokens = 0
        output_tokens = 0
        if response.usage is not None:
            input_tokens = response.usage.prompt_tokens
            output_tokens = response.usage.completion_tokens

        return ModelReply(text=text, input_tokens=input_tokens,
                          output_tokens=output_tokens, model=self.model)


def build_chat_client() -> OpenAIChatClient | None:
    if SETTINGS.is_offline():
        return None
    return OpenAIChatClient(
        api_key=SETTINGS.openai_api_key,
        model=SETTINGS.llm_model,
        temperature=SETTINGS.llm_temperature,
        timeout_seconds=SETTINGS.request_timeout_seconds,
    )
