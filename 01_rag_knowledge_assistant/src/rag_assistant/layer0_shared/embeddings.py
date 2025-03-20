"""
LAYER 0 - SHARED: EMBEDDINGS
============================
An embedding turns a piece of text into a list of numbers, so that two texts
about the same topic end up close together.

Two implementations, same interface:

    OpenAiEmbedder   - real quality, needs an API key, costs money.
    OfflineEmbedder  - no network at all. Hashes words into buckets.
                       Used by the tests and when no key is configured, so the
                       whole system can be run and demonstrated for free.

Both return unit-length vectors, which means "cosine similarity" is just the
dot product. That keeps the search code simple.
"""

import hashlib
import math

from rag_assistant.layer0_shared.cost import embedding_cost_usd, rough_token_count
from rag_assistant.layer0_shared.text_tools import to_stems
from rag_assistant.layer1_config.settings import settings


class EmbeddingResult:
    """The vectors plus what they cost to produce."""

    def __init__(self, vectors: list[list[float]], tokens: int, cost_usd: float, model: str) -> None:
        self.vectors = vectors
        self.tokens = tokens
        self.cost_usd = cost_usd
        self.model = model


def normalise(vector: list[float]) -> list[float]:
    """Scale a vector so its length is exactly 1.0."""
    total = 0.0
    for value in vector:
        total = total + (value * value)

    length = math.sqrt(total)
    if length == 0.0:
        return vector

    scaled: list[float] = []
    for value in vector:
        scaled.append(value / length)
    return scaled


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """
    How similar two vectors are, from -1.0 to 1.0.
    Because our vectors are unit length, this is a plain dot product.
    """
    if len(left) != len(right):
        return 0.0

    total = 0.0
    position = 0
    while position < len(left):
        total = total + (left[position] * right[position])
        position = position + 1
    return total


class OfflineEmbedder:
    """
    A real, working embedder that needs no network.

    How it works:
        1. Split the text into stemmed words.
        2. Hash each word to a bucket number.
        3. Add weight to that bucket.
        4. Normalise the result.

    Two texts that share vocabulary land in the same buckets, so their vectors
    are similar. It cannot understand synonyms - that is what the real model is
    for - but it is deterministic, instant, and free, which makes it perfect for
    tests and for a first run with no API key.
    """

    name = "offline-hashing-embedder"

    # CALIBRATION. A cosine score is only meaningful relative to the embedder
    # that produced it. For this hashing embedder, an irrelevant passage scores
    # around 0.05 and a good match around 0.40. Those two numbers let the
    # reranker turn a raw cosine into an honest "is this actually relevant?"
    # judgement. Every embedder needs its own pair - you find them by running
    # the evaluation suite in layer 8, not by guessing.
    relevance_floor = 0.08
    relevance_ceiling = 0.42

    # HOW MUCH ITS COSINE CAN BE TRUSTED AS INDEPENDENT EVIDENCE.
    # This embedder matches words, not meaning, so its similarity score is a
    # weaker, unweighted copy of what keyword search already tells us. Treating
    # it as a second independent opinion double-counts one signal - that is how
    # "what is the parental leave policy?" ended up being answered from the bonus
    # policy, purely because both contain the word "policy".
    semantic_trust = 0.5

    def __init__(self, dimensions: int = 512) -> None:
        self.dimensions = dimensions

    def bucket_for(self, word: str) -> int:
        digest = hashlib.sha1(word.encode("utf-8")).hexdigest()
        return int(digest[:8], 16) % self.dimensions

    def embed_one(self, text: str) -> list[float]:
        vector: list[float] = []
        position = 0
        while position < self.dimensions:
            vector.append(0.0)
            position = position + 1

        stems = to_stems(text)
        if len(stems) == 0:
            return vector

        # Count how often each word appears.
        counts: dict[str, int] = {}
        for stem in stems:
            if stem not in counts:
                counts[stem] = 0
            counts[stem] = counts[stem] + 1

        # Rare words matter more than repeated ones, so we use log weighting.
        for word in counts:
            weight = 1.0 + math.log(counts[word])
            vector[self.bucket_for(word)] = vector[self.bucket_for(word)] + weight

        return normalise(vector)

    def embed(self, texts: list[str]) -> EmbeddingResult:
        vectors: list[list[float]] = []
        tokens = 0
        for text in texts:
            vectors.append(self.embed_one(text))
            tokens = tokens + rough_token_count(text)
        return EmbeddingResult(vectors=vectors, tokens=tokens, cost_usd=0.0, model=self.name)


class OpenAiEmbedder:
    """Calls the OpenAI embeddings endpoint. Batches texts to save requests."""

    # CALIBRATION - see the note on OfflineEmbedder. Real embedding models put
    # even unrelated text around 0.15 to 0.25, so the floor is much higher here.
    relevance_floor = 0.26
    relevance_ceiling = 0.62

    # A real embedding model does understand paraphrases, so its similarity is
    # genuine evidence in its own right and is trusted fully.
    semantic_trust = 1.0

    def __init__(self, api_key: str, model: str, dimensions: int) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.dimensions = dimensions
        self.name = model

    def embed(self, texts: list[str]) -> EmbeddingResult:
        if len(texts) == 0:
            return EmbeddingResult(vectors=[], tokens=0, cost_usd=0.0, model=self.model)

        # The API is happy with up to a few hundred inputs per call.
        vectors: list[list[float]] = []
        tokens = 0
        batch_size = 64
        start = 0

        while start < len(texts):
            batch = texts[start : start + batch_size]
            response = self.client.embeddings.create(
                model=self.model,
                input=batch,
                dimensions=self.dimensions,
            )

            for item in response.data:
                vectors.append(normalise(list(item.embedding)))

            if response.usage is not None:
                tokens = tokens + response.usage.total_tokens

            start = start + batch_size

        return EmbeddingResult(
            vectors=vectors,
            tokens=tokens,
            cost_usd=embedding_cost_usd(self.model, tokens),
            model=self.model,
        )


def rescale_similarity(raw_cosine: float, floor: float, ceiling: float) -> float:
    """
    Turn a raw cosine score into a 0.0-1.0 "semantic evidence" score using the
    embedder's calibration pair.

        at or below the floor   -> 0.0  (no better than unrelated text)
        at or above the ceiling -> 1.0  (as good as this embedder gets)
    """
    if ceiling <= floor:
        return 0.0
    if raw_cosine <= floor:
        return 0.0
    if raw_cosine >= ceiling:
        return 1.0
    return (raw_cosine - floor) / (ceiling - floor)


def build_embedder():
    """
    Choose the embedder based on configuration.
    This is the only place in the codebase that makes that decision.
    """
    if settings.using_real_embeddings():
        return OpenAiEmbedder(
            api_key=settings.openai_api_key,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )
    return OfflineEmbedder(dimensions=settings.effective_embedding_dimensions())
