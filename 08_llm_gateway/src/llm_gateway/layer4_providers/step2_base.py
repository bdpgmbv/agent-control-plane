"""
LAYER 4, STEP 2 - WHAT A PROVIDER IS
====================================
One method, and a failure type that says what should happen next.

`ProviderError` carries a `FailureKind` rather than just a message, because the
routing layer above has to decide between three different things - try again,
try somebody else, or give up - and it cannot decide that from prose. A string
saying "rate limit exceeded" requires the caller to pattern-match on wording
that the provider is free to change.
"""

from dataclasses import dataclass

from llm_gateway.layer2_models.schemas import FailureKind


class ProviderError(Exception):
    def __init__(self, message: str, kind: FailureKind = FailureKind.UNKNOWN) -> None:
        super().__init__(message)
        self.kind = kind


class EmbeddingUnavailable(Exception):
    """
    No embedding could be produced for this text.

    Deliberately NOT a ProviderReply-style failure, and deliberately not
    something the caller can paper over with a substitute vector. Two
    embeddings are only comparable if they came from the same embedder:
    cosine similarity between a real model's vector and a hashed one is not a
    small error, it is a number with no meaning that will still be compared
    against a threshold. Whoever catches this must skip the semantic step, not
    find another way to fill the gap.
    """


@dataclass
class ProviderReply:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class Provider:
    """A thing that can answer a prompt."""

    name = "unnamed"

    def models(self) -> list[str]:
        return []

    def complete(self, system: str, prompt: str, model: str,
                 max_tokens: int = 500, temperature: float = 0.0) -> ProviderReply:
        raise NotImplementedError

    def embed(self, text: str) -> list[float]:
        """For the semantic cache. Not every provider needs to do this well."""
        raise NotImplementedError
