"""
LAYER 0 - TALKING TO THE MODEL
==============================
One thin wrapper, and one deliberate design choice.

`build_chat_client()` returns None when there is no key. It does not return a
fake model that pretends to answer.

That looks unhelpful until you remember what this project is about. Every stage
here has to work without a model anyway - rules classify, patterns extract,
arithmetic validates - and hiding that behind a fake would hide the very thing
worth learning. An explicit

    if client is None:
        ... deterministic path ...

at each call site makes the deterministic path visible in the code instead of
buried in a test double. The offline path is not a stub for the model; the model
is a fallback for the offline path.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from doc_intelligence.layer1_config.settings import SETTINGS

if TYPE_CHECKING:
    # Only needed for type checking, so importing this module never
    # requires the OpenAI SDK to be installed - which is what keeps
    # offline mode genuinely dependency-free.
    from openai.types.chat import (
        ChatCompletionContentPartParam,
        ChatCompletionMessageParam,
    )


class ModelUnavailable(Exception):
    """
    The model could not answer, with a machine-readable reason.

    Project 04 turned a "429 insufficient_quota" into a stack trace in the user
    interface. Distinguishing the kinds means each one gets wording a person can
    act on: top up the account, wait, fix the key, check the network.
    """

    def __init__(self, message: str, kind: str = "unknown") -> None:
        super().__init__(message)
        self.kind = kind

    def friendly_message(self) -> str:
        if self.kind == "no_credit":
            return ("The OpenAI account has no credit, so the model could not be "
                    "called. Everything below came from the deterministic path.")
        if self.kind == "rate_limited":
            return "The model is rate limited. Try again in a moment."
        if self.kind == "bad_key":
            return "The OPENAI_API_KEY in .env was rejected. Check it and restart."
        if self.kind == "unreachable":
            return "Could not reach OpenAI. Check the network connection."
        return "The model could not be reached: %s" % str(self)


@dataclass
class ModelReply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


def classify_openai_error(error: Exception) -> str:
    """Turn whatever the SDK raised into one of our four kinds."""
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
    """The real client. Text in, text out, plus the token counts it reports."""

    def __init__(self, api_key: str, model: str, vision_model: str,
                 temperature: float, timeout_seconds: float) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, timeout=timeout_seconds)
        self.model = model
        self.vision_model = vision_model
        self.temperature = temperature

    def complete(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 1200) -> ModelReply:
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

        return self.reply_from(response, self.model)

    def read_image(self, system_prompt: str, user_prompt: str,
                   image_base64: str, media_type: str,
                   max_tokens: int = 1500) -> ModelReply:
        """
        Read a scanned document. This is the one job here that genuinely needs a
        model - there is no text layer to parse, so there is nothing for code to
        work on until the pixels have been turned into characters.
        """
        image_url = "data:%s;base64,%s" % (media_type, image_base64)

        # A multimodal user message: its content is a list of parts rather than
        # a string. The SDK models each part as its own TypedDict, so the parts
        # are named explicitly here instead of being left as plain dicts - which
        # is also the clearest statement of what is actually being sent.
        parts: list[ChatCompletionContentPartParam] = [
            {"type": "text", "text": user_prompt},
            {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
        ]
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": parts},
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.vision_model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=max_tokens,
            )
        except Exception as error:
            raise ModelUnavailable(str(error), classify_openai_error(error)) from error

        return self.reply_from(response, self.vision_model)

    def reply_from(self, response, model_name: str) -> ModelReply:
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

        return ModelReply(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model_name,
        )


def build_chat_client() -> OpenAIChatClient | None:
    """None means "run deterministically". See the note at the top of this file."""
    if SETTINGS.is_offline():
        return None
    return OpenAIChatClient(
        api_key=SETTINGS.openai_api_key,
        model=SETTINGS.llm_model,
        vision_model=SETTINGS.vision_model,
        temperature=SETTINGS.llm_temperature,
        timeout_seconds=SETTINGS.request_timeout_seconds,
    )
