"""
LAYER 6 - THE CACHE
===================
Two kinds of hit, and one rule that governs both.

    EXACT      the same prompt, the same model, the same settings.
               Free, instant, and obviously safe.

    SEMANTIC   a DIFFERENT prompt that means the same thing. Also free and
               instant, and only safe if "means the same thing" is true.

THE RULE: THE SCOPE IS PART OF THE KEY
--------------------------------------
Project 01 in this series shipped a semantic cache that leaked. A question asked
by someone with admin access was answered, cached, and then served to a user
without it - because the similarity search found the nearest entry first and the
permission check came afterwards.

A check that comes after the match is a check somebody will eventually move,
refactor, or forget. So here the scope is baked into the exact key, and the
semantic candidates are fetched with the scope in the WHERE clause. There is no
code path that can find an entry from another scope and then decide what to do
about it, because there is no code path that can find one at all.

ON THE THRESHOLD
----------------
Similarity above SEMANTIC_THRESHOLD counts as the same question. Too high and
the cache never hits, which costs money. Too low and the gateway confidently
answers a question nobody asked, which costs trust and is far harder to notice -
the response is fluent and plausible and about something else.

The number is measured, not chosen: `scripts/tune_threshold.py` runs pairs that
SHOULD match and pairs that should NOT, and reports where the two groups
separate. It reports honestly when they do not separate at all.
"""

import hashlib
from dataclasses import dataclass

from llm_gateway.layer2_models.schemas import CacheStatus
from llm_gateway.layer4_providers.step2_base import EmbeddingUnavailable
from llm_gateway.layer4_providers.step3_offline import cosine_similarity


@dataclass
class CacheLookup:
    status: CacheStatus = CacheStatus.MISS
    text: str = ""
    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    similarity: float = 0.0
    matched_prompt: str = ""
    cache_key: str = ""


def build_scope_key(api_key_owner: str, explicit_scope: str) -> str:
    """
    Who is allowed to see this answer.

    An explicit scope lets several callers share a cache on purpose - a team
    key and a service key working on the same public documents. Without one, the
    owner IS the scope, which is the safe default: sharing has to be asked for,
    never assumed.
    """
    if explicit_scope.strip() != "":
        return "scope:" + explicit_scope.strip()
    return "owner:" + api_key_owner


def build_cache_key(scope: str, system: str, prompt: str, model: str,
                    temperature: float, max_tokens: int) -> str:
    """
    Everything that could change the answer goes in the key.

    Including temperature and max_tokens, which look like they do not matter
    until somebody caches a 100-token answer and it is served to a caller who
    asked for 2000.
    """
    material = "␟".join([
        scope, system, prompt, model, "%.3f" % temperature, str(max_tokens),
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ResponseCache:
    def __init__(self, store, embedder, enabled: bool = True,
                 semantic_enabled: bool = True, threshold: float = 0.93,
                 ttl_seconds: float = 3600.0) -> None:
        self.store = store
        self.embedder = embedder
        self.enabled = enabled
        self.semantic_enabled = semantic_enabled
        self.threshold = threshold
        self.ttl_seconds = ttl_seconds

    def look_up(self, scope: str, system: str, prompt: str, model: str,
                temperature: float, max_tokens: int) -> CacheLookup:
        if not self.enabled:
            return CacheLookup(status=CacheStatus.DISABLED)

        # A request with any randomness in it is not cacheable: the caller asked
        # for variety, and handing back a stored answer is refusing to give it.
        if temperature > 0.0:
            return CacheLookup(status=CacheStatus.NOT_CACHEABLE)

        cache_key = build_cache_key(scope, system, prompt, model, temperature, max_tokens)
        exact = self.store.get_cache(cache_key)
        if exact is not None:
            self.store.note_cache_hit(cache_key)
            return CacheLookup(
                status=CacheStatus.EXACT, text=exact["response_text"],
                model=exact["model"], provider=exact["provider"],
                input_tokens=exact["input_tokens"], output_tokens=exact["output_tokens"],
                similarity=1.0, matched_prompt=exact["prompt"], cache_key=cache_key)

        if not self.semantic_enabled:
            return CacheLookup(status=CacheStatus.MISS)

        return self.look_up_semantically(scope, prompt, model)

    def look_up_semantically(self, scope: str, prompt: str, model: str) -> CacheLookup:
        """
        The nearest entry IN THIS SCOPE, if it is near enough.

        `cache_candidates` takes the scope, so entries from other scopes are
        never loaded. See the note at the top of this file.
        """
        candidates = self.store.cache_candidates(scope)
        if len(candidates) == 0:
            return CacheLookup(status=CacheStatus.MISS)

        # If the embedder cannot answer, there is no semantic lookup. Not a
        # substitute vector and not an error to the caller: a miss, which costs
        # one model call. The alternative - embedding with something else and
        # comparing anyway - can clear the threshold and serve the wrong answer.
        try:
            wanted = self.embedder(prompt)
        except EmbeddingUnavailable:
            return CacheLookup(status=CacheStatus.MISS)

        best = None
        best_similarity = 0.0

        for candidate in candidates:
            if candidate["model"] != model:
                # A different model gives a different answer, however similar
                # the question. Serving one for the other would make an A/B test
                # compare a variant against itself.
                continue
            import json
            stored = json.loads(candidate["embedding_json"])
            similarity = cosine_similarity(wanted, stored)
            if similarity > best_similarity:
                best_similarity = similarity
                best = candidate

        if best is None or best_similarity < self.threshold:
            return CacheLookup(status=CacheStatus.MISS, similarity=best_similarity)

        self.store.note_cache_hit(best["cache_key"])
        return CacheLookup(
            status=CacheStatus.SEMANTIC, text=best["response_text"],
            model=best["model"], provider=best["provider"],
            input_tokens=best["input_tokens"], output_tokens=best["output_tokens"],
            similarity=round(best_similarity, 4), matched_prompt=best["prompt"],
            cache_key=best["cache_key"])

    def store_answer(self, scope: str, system: str, prompt: str, model: str,
                     temperature: float, max_tokens: int, provider: str,
                     text: str, input_tokens: int, output_tokens: int) -> None:
        if not self.enabled or temperature > 0.0:
            return
        cache_key = build_cache_key(scope, system, prompt, model, temperature, max_tokens)

        # An entry with no embedding is still worth storing: exact repeats of
        # this question will hit it. What must not happen is storing a vector
        # from a different embedder alongside the real ones, because then every
        # later comparison against it is meaningless in both directions.
        embedding: list[float] = []
        if self.semantic_enabled:
            try:
                embedding = self.embedder(prompt)
            except EmbeddingUnavailable:
                embedding = []

        self.store.put_cache(cache_key, scope, prompt, text, model, provider,
                             input_tokens, output_tokens, embedding, self.ttl_seconds)
