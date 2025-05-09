"""
LAYER 4, STEP 1 - TALKING TO THE MODEL
======================================
The same thin wrapper as the rest of the series: typed failures, and None when
there is no key.

What is different here is what a model failure means. In project 05 a failed
call meant one document went to a human. In a workflow it means a STEP failed -
and a failed step has a retry policy, a backoff, an attempt count and, if it
keeps failing, a compensation path. The model is not a special case in this
system; it is one more unreliable external service, and it gets treated like
the licence vendor.

That framing is why `ModelUnavailable` carries a `transient` flag. "No credit"
will still be true in thirty seconds, so retrying it wastes two more attempts
and arrives at the same place. "Rate limited" will probably have cleared.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from enterprise_workflow.layer1_config.settings import SETTINGS

if TYPE_CHECKING:
    # Only for type checking, so this module imports without the OpenAI SDK.
    from openai.types.chat import ChatCompletionMessageParam


class ModelUnavailable(Exception):
    def __init__(self, message: str, kind: str = "unknown") -> None:
        super().__init__(message)
        self.kind = kind

    def is_transient(self) -> bool:
        """Is there any point trying again?"""
        return self.kind in ("rate_limited", "unreachable", "unknown")

    def friendly_message(self) -> str:
        if self.kind == "no_credit":
            return "The OpenAI account has no credit."
        if self.kind == "rate_limited":
            return "The model is rate limited."
        if self.kind == "bad_key":
            return "The OPENAI_API_KEY in .env was rejected."
        if self.kind == "unreachable":
            return "Could not reach OpenAI."
        return "The model could not be reached: %s" % str(self)


@dataclass
class ModelReply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


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
                 max_tokens: int = 900) -> ModelReply:
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
    """None means offline. Every agent below has a deterministic path for that."""
    if SETTINGS.is_offline():
        return None
    return OpenAIChatClient(
        api_key=SETTINGS.openai_api_key,
        model=SETTINGS.llm_model,
        temperature=SETTINGS.llm_temperature,
        timeout_seconds=SETTINGS.request_timeout_seconds,
    )
