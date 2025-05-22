"""
LAYER 4, STEP 4 - THE REAL PROVIDER
===================================
OpenAI, behind the same interface as the offline ones.

The whole job of this file is turning whatever the SDK raises into a
`FailureKind`, because that is what the layer above needs in order to decide
between trying again, trying somebody else, and giving up. Everything else here
is plumbing.

Getting that classification wrong is expensive in both directions. Treat a
"no credit" as retryable and every request waits through two pointless attempts
before failing. Treat a "rate limited" as permanent and you fall back to a model
that costs sixteen times more, for a condition that would have cleared in a
second.
"""

from typing import TYPE_CHECKING

from llm_gateway.layer2_models.schemas import FailureKind
from llm_gateway.layer4_providers.step2_base import (
    EmbeddingUnavailable,
    Provider,
    ProviderError,
    ProviderReply,
)

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam

EMBEDDING_MODEL = "text-embedding-3-small"


def classify(error: Exception) -> FailureKind:
    description = ("%s %s" % (type(error).__name__, str(error))).lower()

    if "insufficient_quota" in description or "exceeded your current quota" in description:
        return FailureKind.NO_CREDIT
    if "rate_limit" in description or "429" in description:
        return FailureKind.RATE_LIMITED
    if "authentication" in description or "invalid_api_key" in description \
            or "401" in description:
        return FailureKind.BAD_KEY
    if "timeout" in description or "timed out" in description:
        return FailureKind.TIMEOUT
    if "connection" in description or "unreachable" in description:
        return FailureKind.UNREACHABLE
    if "invalid_request" in description or "400" in description \
            or "does not exist" in description:
        # The next provider will dislike it just as much.
        return FailureKind.BAD_REQUEST
    return FailureKind.UNKNOWN


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, api_key: str, timeout_seconds: float = 40.0) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)
        # max_retries=0 on purpose: the SDK's own retry loop is invisible to this
        # gateway, so it would hide latency the traces are supposed to show and
        # retry things the routing layer has already decided not to.

    def models(self) -> list[str]:
        """
        Only gpt-4o-mini, deliberately.

        gpt-4o costs sixteen times more per token, and nothing in this series
        is built on work that needs it. Leaving it here would mean one mistyped
        route or one stray request could spend real money at sixteen times the
        rate, silently, because it would simply answer. The price table still
        knows gpt-4o so the cost arithmetic can be read and tested; the gateway
        just will not route to it.
        """
        return ["gpt-4o-mini"]

    def complete(self, system: str, prompt: str, model: str,
                 max_tokens: int = 500, temperature: float = 0.0) -> ProviderReply:
        messages: list[ChatCompletionMessageParam] = []
        if system.strip() != "":
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = self.client.chat.completions.create(
                model=model, messages=messages,
                max_tokens=max_tokens, temperature=temperature)
        except Exception as error:
            raise ProviderError(str(error), classify(error)) from error

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

        return ProviderReply(text=text, model=model, input_tokens=input_tokens,
                             output_tokens=output_tokens)

    def embed(self, text: str) -> list[float]:
        """
        A real embedding for the semantic cache.

        This used to catch everything and return `hashing_embedding(text)`
        instead, reasoning that a cache which cannot be written is a missed
        saving while a request that fails because of it is an outage. The first
        half is right and the conclusion is wrong, because a hashed vector is
        not a worse embedding - it is a vector in a different space.

        Stored entries were embedded by the real model. Comparing a hashed
        vector against them by cosine gives a number that means nothing, and
        that number is then checked against the threshold like any other. It
        can clear it. So the substitution was not a graceful degradation of the
        cache, it was a way for the gateway to serve an answer to a question
        nobody asked - the exact failure this project's cache scoping exists to
        prevent, arriving through the other door.

        It also quietly corrupted measurement: `tune_threshold.py --live`
        measured the hashing embedder while reporting real embeddings, so the
        LIVE threshold would have been set from OFFLINE data.

        Raising lets the caller skip the semantic step - still not an outage,
        because the exact-match cache and the request itself are unaffected.
        """
        try:
            response = self.client.embeddings.create(model=EMBEDDING_MODEL, input=text)
        except Exception as error:
            raise EmbeddingUnavailable(str(error)) from error
        return list(response.data[0].embedding)
