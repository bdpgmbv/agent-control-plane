"""
LAYER 0 - SHARED: EMBEDDINGS
============================
Used for one job here: deciding whether two findings say the same thing.

There is no offline embedder in this project, and that is deliberate. In project
01 a hashing embedder was useful because it still found documents by shared
vocabulary. Here the question is subtler - "productivity rose 12%" versus
"productivity increased 12.4%" share almost no words - and a word-matching
embedder would answer it no better than the word comparison already in
similarity.py, while pretending to be something more.

So with no API key, similarity.py compares words and says so. With a key it
compares meaning. The report records which, because it changes how much the
deduplication can be trusted.
"""

from research_agent.layer0_shared.cost import embedding_cost_usd


class EmbeddingResult:
    def __init__(self, vectors: list[list[float]], tokens: int, cost_usd: float, model: str) -> None:
        self.vectors = vectors
        self.tokens = tokens
        self.cost_usd = cost_usd
        self.model = model


def normalise(vector: list[float]) -> list[float]:
    total = 0.0
    for value in vector:
        total = total + (value * value)

    length = total ** 0.5
    if length == 0.0:
        return vector

    scaled: list[float] = []
    for value in vector:
        scaled.append(value / length)
    return scaled


class OpenAiEmbedder:
    """Batches every claim into as few calls as possible."""

    def __init__(self, api_key: str, model: str = "text-embedding-3-small") -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, timeout=60.0, max_retries=2)
        self.model = model
        self.name = model

    def embed(self, texts: list[str]) -> EmbeddingResult:
        if len(texts) == 0:
            return EmbeddingResult([], 0, 0.0, self.model)

        vectors: list[list[float]] = []
        tokens = 0
        batch_size = 64
        start = 0

        while start < len(texts):
            batch = texts[start : start + batch_size]
            response = self.client.embeddings.create(model=self.model, input=batch)

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


def build_embedder():
    """An embedder when a key is configured, otherwise None."""
    from research_agent.layer1_config.settings import settings

    if settings.using_real_llm():
        return OpenAiEmbedder(api_key=settings.openai_api_key)
    return None
