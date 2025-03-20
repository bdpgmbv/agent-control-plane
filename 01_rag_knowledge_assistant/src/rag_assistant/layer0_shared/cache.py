"""
LAYER 0 - SHARED: CACHING
=========================
Two kinds of cache, because questions repeat in two different ways.

1. EXACT cache
   The same question, with the same filters, asked again. Keyed by a hash.
   Saves 100% of the cost.

2. SEMANTIC cache
   A differently worded but equivalent question:
       "how long do I have to return something?"
       "what is the return window?"
   We compare the question's embedding against embeddings of past questions.
   If one is close enough (see SEMANTIC_CACHE_THRESHOLD), we reuse that answer.

Storage backends:
    MemoryCacheBackend - a dict. Fine for one process.
    RedisCacheBackend  - shared between processes, survives a restart.

Note on the semantic index: the list of past question vectors is kept in this
process's memory even when Redis holds the payloads. Sharing vectors between
processes needs a vector database, which is out of scope for a cache.
"""

import hashlib
import json
import time
from typing import Any, Protocol

from rag_assistant.layer0_shared.embeddings import cosine_similarity
from rag_assistant.layer1_config.settings import settings


def build_scope_key(filters: dict) -> str:
    """
    A fingerprint of WHO is asking and WHAT they filtered to.

    Two callers with different permissions have different scopes, and entries in
    one scope are invisible to the other. This is the boundary that keeps the
    cache from leaking answers across permission levels.
    """
    filter_text = json.dumps(filters, sort_keys=True)
    return hashlib.sha256(filter_text.encode("utf-8")).hexdigest()[:16]


def build_cache_key(question: str, filters: dict) -> str:
    """
    One stable key for a question plus its filters.

    The filters matter: the same question asked by a user who may only see
    "public" documents must not reuse an answer built from "secret" documents.
    """
    normalised_question = " ".join(question.lower().split())
    raw = normalised_question + "||" + build_scope_key(filters)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class CacheBackend(Protocol):
    """
    What a cache backend has to provide.

    Both backends already satisfied this; it was simply never written down, so
    nothing checked that they stayed interchangeable. A Protocol is structural -
    neither class has to inherit from it - which keeps the swap in build_cache()
    honest without forcing an inheritance hierarchy on two small classes.
    """

    name: str

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, payload: str, ttl_seconds: int) -> None: ...

    def clear(self) -> None: ...

    def size(self) -> int: ...


class MemoryCacheBackend:
    """A dictionary with expiry times."""

    name = "memory"

    def __init__(self) -> None:
        self.entries: dict[str, tuple[float, str]] = {}

    def get(self, key: str) -> str | None:
        if key not in self.entries:
            return None

        expires_at, payload = self.entries[key]
        if time.time() > expires_at:
            del self.entries[key]
            return None
        return payload

    def set(self, key: str, payload: str, ttl_seconds: int) -> None:
        self.entries[key] = (time.time() + ttl_seconds, payload)

    def clear(self) -> None:
        self.entries.clear()

    def size(self) -> int:
        return len(self.entries)


class RedisCacheBackend:
    """Shared cache. Used when CACHE_BACKEND=redis."""

    name = "redis"

    def __init__(self, url: str) -> None:
        import redis  # type: ignore[import-untyped]  # optional dependency, no stubs

        # redis-py types every method for BOTH its sync and async clients, so
        # each one returns `Awaitable[Any] | Any` regardless of which client you
        # built. This is the sync client and its results are already values.
        # Saying so once here is clearer than casting at four call sites.
        self.client: Any = redis.Redis.from_url(url, decode_responses=True)
        self.prefix = "rag:answer:"

    def get(self, key: str) -> str | None:
        return self.client.get(self.prefix + key)

    def set(self, key: str, payload: str, ttl_seconds: int) -> None:
        self.client.setex(self.prefix + key, ttl_seconds, payload)

    def clear(self) -> None:
        cursor = 0
        while True:
            cursor, keys = self.client.scan(cursor=cursor, match=self.prefix + "*", count=200)
            if len(keys) > 0:
                self.client.delete(*keys)
            if cursor == 0:
                break

    def size(self) -> int:
        total = 0
        cursor = 0
        while True:
            cursor, keys = self.client.scan(cursor=cursor, match=self.prefix + "*", count=200)
            total = total + len(keys)
            if cursor == 0:
                break
        return total


class AnswerCache:
    """The cache the API layer actually uses. Wraps a backend."""

    def __init__(self, backend, ttl_seconds: int, semantic_threshold: float) -> None:
        self.backend = backend
        self.ttl_seconds = ttl_seconds
        self.semantic_threshold = semantic_threshold
        # Remembered question vectors: (vector, cache key, scope, expiry time).
        #
        # The SCOPE is not optional. Without it the semantic cache would happily
        # answer a "public only" caller with an answer built from secret
        # documents, because the two questions are worded identically. The exact
        # cache key already includes permissions; the semantic index must too.
        self.semantic_index: list[tuple[list[float], str, str, float]] = []

    # ---------- exact ----------

    def get_exact(self, key: str) -> dict | None:
        payload = self.backend.get(key)
        if payload is None:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    def store(
        self,
        key: str,
        value: dict,
        question_vector: list[float] | None = None,
        scope: str = "",
    ) -> None:
        self.backend.set(key, json.dumps(value), self.ttl_seconds)
        if question_vector is not None:
            self.remember_vector(question_vector, key, scope)

    # ---------- semantic ----------

    def remember_vector(self, vector: list[float], key: str, scope: str = "") -> None:
        self.drop_expired_vectors()
        self.semantic_index.append((vector, key, scope, time.time() + self.ttl_seconds))
        # Keep the index small so the scan stays fast.
        if len(self.semantic_index) > 500:
            del self.semantic_index[0 : len(self.semantic_index) - 500]

    def drop_expired_vectors(self) -> None:
        now = time.time()
        kept: list[tuple[list[float], str, str, float]] = []
        for vector, key, scope, expires_at in self.semantic_index:
            if expires_at > now:
                kept.append((vector, key, scope, expires_at))
        self.semantic_index = kept

    def get_similar(self, vector: list[float], scope: str = "") -> tuple[dict, float] | None:
        """
        Find a cached answer for a question that means the same thing AND was
        asked by someone with the same permissions.

        Returns (cached value, similarity) or None.
        """
        self.drop_expired_vectors()

        best_similarity = 0.0
        best_key = ""
        for stored_vector, key, stored_scope, _expires_at in self.semantic_index:
            # Entries from another permission scope are not candidates at all.
            if stored_scope != scope:
                continue
            similarity = cosine_similarity(vector, stored_vector)
            if similarity > best_similarity:
                best_similarity = similarity
                best_key = key

        if best_key == "" or best_similarity < self.semantic_threshold:
            return None

        value = self.get_exact(best_key)
        if value is None:
            return None
        return (value, round(best_similarity, 4))

    # ---------- admin ----------

    def clear(self) -> None:
        self.backend.clear()
        self.semantic_index.clear()

    def describe(self) -> dict:
        return {
            "backend": self.backend.name,
            "entries": self.backend.size(),
            "semantic_vectors": len(self.semantic_index),
            "ttl_seconds": self.ttl_seconds,
            "semantic_threshold": self.semantic_threshold,
        }


def build_cache() -> AnswerCache:
    """Choose the cache backend based on configuration."""
    if settings.cache_backend == "redis":
        backend: CacheBackend = RedisCacheBackend(settings.redis_url)
    else:
        backend = MemoryCacheBackend()

    return AnswerCache(
        backend=backend,
        ttl_seconds=settings.cache_ttl_seconds,
        semantic_threshold=settings.semantic_cache_threshold,
    )
