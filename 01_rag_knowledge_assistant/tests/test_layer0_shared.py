"""TESTS FOR LAYER 0 - the shared foundations."""

from rag_assistant.layer0_shared.cache import AnswerCache, MemoryCacheBackend, build_cache_key
from rag_assistant.layer0_shared.context_format import (
    QUESTION_PREFIX,
    find_question,
    format_context_blocks,
    parse_context_blocks,
)
from rag_assistant.layer0_shared.cost import chat_cost_usd, embedding_cost_usd
from rag_assistant.layer0_shared.embeddings import cosine_similarity, rescale_similarity
from rag_assistant.layer0_shared.metrics import MetricsRegistry
from rag_assistant.layer0_shared.text_tools import stem_word, to_stems, word_overlap_score


def test_stemming_makes_related_words_match():
    assert stem_word("refunds") == "refund"
    assert stem_word("refunded") == "refund"
    assert stem_word("policies") == "policy"
    # Short words are left alone, so "days" does not become "da".
    assert stem_word("days") == "days"


def test_word_overlap_uses_stems():
    # "refund" must match "Refunds" even though the letters differ.
    score = word_overlap_score("refund window", "Refunds are issued within a window")
    assert score == 1.0


def test_stop_words_are_removed():
    stems = to_stems("How much does it cost to get a refund?")
    assert "refund" in stems
    assert "cost" in stems
    # These match every passage, so they must not count as evidence.
    assert "much" not in stems
    assert "get" not in stems


def test_context_blocks_round_trip():
    passages = [
        {"title": "Refunds", "source": "refunds.md", "text": "Thirty days."},
        {"title": "Shipping", "source": "ship.md", "text": "Five days."},
    ]
    prompt = format_context_blocks(passages) + "\n\n" + QUESTION_PREFIX + " how long?"

    blocks = parse_context_blocks(prompt)
    assert len(blocks) == 2
    assert blocks[0]["marker"] == 1
    assert blocks[0]["title"] == "Refunds"
    # The question must NOT leak into the last block's text.
    assert "how long" not in blocks[1]["text"]
    assert find_question(prompt) == "how long?"


def test_cosine_similarity_of_identical_vectors_is_one():
    vector = [0.6, 0.8]
    assert round(cosine_similarity(vector, vector), 6) == 1.0


def test_rescale_similarity_uses_the_calibration_pair():
    assert rescale_similarity(0.05, 0.10, 0.50) == 0.0     # below the floor
    assert rescale_similarity(0.90, 0.10, 0.50) == 1.0     # above the ceiling
    assert round(rescale_similarity(0.30, 0.10, 0.50), 3) == 0.5


def test_cache_key_changes_when_permissions_change():
    """
    The most important cache test. Two callers with different permissions must
    never share a cache entry, or one will be served the other's answer.
    """
    key_for_user = build_cache_key("how much leave?", {"allowed_tags": ["public"]})
    key_for_admin = build_cache_key("how much leave?", {"allowed_tags": ["public", "secret"]})
    assert key_for_user != key_for_admin


def test_cache_key_ignores_case_and_spacing():
    first = build_cache_key("How  Much   Leave?", {})
    second = build_cache_key("how much leave?", {})
    assert first == second


def test_semantic_cache_finds_a_similar_question():
    cache = AnswerCache(MemoryCacheBackend(), ttl_seconds=60, semantic_threshold=0.9)
    cache.store("key-1", {"answer": "30 days"}, [1.0, 0.0, 0.0])

    hit = cache.get_similar([1.0, 0.0, 0.0])
    assert hit is not None
    assert hit[0]["answer"] == "30 days"

    # A different question must not hit.
    assert cache.get_similar([0.0, 1.0, 0.0]) is None


def test_metrics_percentiles():
    registry = MetricsRegistry()
    for value in [10, 20, 30, 40, 50, 60, 70, 80, 90, 1000]:
        registry.observe("latency_ms", value)
    assert registry.percentile("latency_ms", 0.50) == 50
    assert registry.percentile("latency_ms", 0.95) == 1000


def test_cost_is_reported_in_dollars():
    assert chat_cost_usd("gpt-4o-mini", 1_000_000, 0) == 0.15
    assert chat_cost_usd("gpt-4o-mini", 0, 1_000_000) == 0.60
    assert embedding_cost_usd("text-embedding-3-small", 1_000_000) == 0.02
    # An unknown model still returns a number rather than crashing.
    assert chat_cost_usd("some-future-model", 1000, 1000) > 0


def test_the_semantic_cache_never_crosses_permission_scopes():
    """
    A REAL BUG THIS TEST EXISTS TO PREVENT.

    The exact cache key included the caller's permissions, so exact lookups were
    safe. The semantic index did not, so an identically worded question from a
    "public only" caller matched the admin's stored vector at similarity 1.0 and
    was served an answer built from secret documents.

    The fix was to scope the semantic index too. This test fails if anyone ever
    removes it.
    """
    from rag_assistant.layer0_shared.cache import build_scope_key

    cache = AnswerCache(MemoryCacheBackend(), ttl_seconds=60, semantic_threshold=0.9)

    admin_scope = build_scope_key({"allowed_tags": ["public", "secret"]})
    user_scope = build_scope_key({"allowed_tags": ["public"]})
    assert admin_scope != user_scope

    question_vector = [1.0, 0.0, 0.0]
    cache.store("admin-answer", {"answer": "level three earns 120000"}, question_vector, scope=admin_scope)

    # The admin sees their own cached answer.
    assert cache.get_similar(question_vector, scope=admin_scope) is not None

    # The user, asking the identical question, must not.
    assert cache.get_similar(question_vector, scope=user_scope) is None


def test_expired_semantic_vectors_are_dropped():
    cache = AnswerCache(MemoryCacheBackend(), ttl_seconds=0, semantic_threshold=0.9)
    cache.store("k", {"answer": "x"}, [1.0, 0.0], scope="s")
    assert cache.get_similar([1.0, 0.0], scope="s") is None
