"""TESTS FOR LAYER 5 - query rewriting, hybrid search, fusion, reranking."""

from rag_assistant.layer2_models.schemas import Chunk, ScoredChunk
from rag_assistant.layer5_retrieval.step1_rewrite_query import (
    build_query_variants,
    expand_with_synonyms,
    keywords_only,
)
from rag_assistant.layer5_retrieval.step4_hybrid_merge import reciprocal_rank_fusion
from rag_assistant.layer5_retrieval.step5_rerank import heuristic_rerank
from rag_assistant.layer5_retrieval.step6_pipeline import RetrievalPipeline, resolve_allowed_tags
from rag_assistant.layer5_retrieval.term_weights import CorpusTermWeights


def flat_weights():
    """Term weights for a tiny corpus where every word is equally rare."""
    return CorpusTermWeights(total_chunks=10, frequencies={})


def make_scored(chunk_id, text, vector_rank=None, keyword_rank=None, vector_score=None, heading=""):
    chunk = Chunk(
        chunk_id=chunk_id,
        document_id="d",
        document_title="T",
        source="t.md",
        access_tag="public",
        chunk_index=0,
        text=text,
        metadata={"heading": heading},
    )
    return ScoredChunk(
        chunk=chunk,
        vector_rank=vector_rank,
        keyword_rank=keyword_rank,
        vector_score=vector_score,
    )


# ---------------- query rewriting ----------------

def test_synonyms_bridge_everyday_words_to_document_words():
    expanded = expand_with_synonyms("can i get my money back?")
    assert "refund" in expanded
    # Expansion only ever adds; the original words must survive.
    assert "money" in expanded


def test_keywords_only_strips_filler():
    assert keywords_only("How much does express delivery cost?") == "express delivery cost"


def test_the_original_question_is_always_the_first_query(chat_client):
    variants = build_query_variants("can i get my money back?", chat_client)
    assert variants[0] == "can i get my money back?"
    assert len(variants) > 1


# ---------------- access control ----------------

def test_a_requested_filter_can_only_narrow_permissions():
    """
    The single most important function for security in this project. A caller
    must never be able to widen what they can see by asking for it.
    """
    assert resolve_allowed_tags(["public"], ["secret"]) == []
    assert resolve_allowed_tags(["public", "secret"], ["secret"]) == ["secret"]
    assert resolve_allowed_tags(["public", "internal"], None) == ["public", "internal"]
    assert resolve_allowed_tags(["public"], ["public", "secret"]) == ["public"]


# ---------------- fusion ----------------

def test_fusion_rewards_a_passage_both_searches_found():
    found_by_both = make_scored("both", "text", vector_rank=2, keyword_rank=2)
    vector_only = make_scored("vector", "text", vector_rank=1)
    keyword_only = make_scored("keyword", "text", keyword_rank=1)

    fused = reciprocal_rank_fusion(
        vector_results=[vector_only, found_by_both],
        keyword_results=[keyword_only, found_by_both],
    )

    assert fused[0].chunk.chunk_id == "both"
    assert fused[0].reason == "found by meaning and by words"


def test_fusion_keeps_results_that_only_one_search_found():
    fused = reciprocal_rank_fusion(
        vector_results=[make_scored("a", "text", vector_rank=1)],
        keyword_results=[make_scored("b", "text", keyword_rank=1)],
    )
    identifiers = []
    for item in fused:
        identifiers.append(item.chunk.chunk_id)

    assert "a" in identifiers
    assert "b" in identifiers


def test_fusion_carries_the_raw_scores_through():
    """The absolute scores must survive fusion, or honest refusal breaks."""
    item = make_scored("a", "text", vector_rank=1, vector_score=0.42)
    fused = reciprocal_rank_fusion([item], [])
    assert fused[0].vector_score == 0.42


# ---------------- reranking ----------------

def test_reranking_scores_a_matching_passage_above_a_random_one():
    candidates = [
        make_scored("noise", "The office dog is named Biscuit.", vector_rank=1, vector_score=0.09),
        make_scored("good", "Customers may request a refund within 30 days.", vector_rank=2, vector_score=0.30),
    ]
    reranked = heuristic_rerank(
        question="how long do I have to request a refund",
        candidates=candidates,
        relevance_floor=0.08,
        relevance_ceiling=0.42,
        term_weights=flat_weights(),
    )
    assert reranked[0].chunk.chunk_id == "good"


def test_a_passage_with_no_evidence_scores_zero():
    """
    Bonuses must never rescue an irrelevant passage. If they could, the relevance
    threshold would let noise through and the system would stop refusing.
    """
    candidates = [
        make_scored("noise", "The office dog is named Biscuit.",
                    vector_rank=1, keyword_rank=1, vector_score=0.01),
    ]
    reranked = heuristic_rerank(
        question="what is the parental leave policy",
        candidates=candidates,
        relevance_floor=0.08,
        relevance_ceiling=0.42,
        term_weights=flat_weights(),
    )
    assert reranked[0].rerank_score == 0.0


def test_reranking_bridges_everyday_words_to_document_words():
    """
    The user says "money back"; the document says "refund". Synonym-aware
    matching is what connects them, so the user's own wording is enough.
    """
    scored = heuristic_rerank(
        "can i get my money back",
        [make_scored("good", "Customers may request a refund within 30 days.", vector_score=0.0)],
        0.08,
        0.42,
        flat_weights(),
    )
    assert scored[0].rerank_score > 0.0


def test_opposite_directions_are_not_treated_as_synonyms():
    """
    A customer PAYS a fee; an employee EARNS a salary. Treating those as the same
    made a question about engineer pay match a shipping-fee passage.
    """
    scored = heuristic_rerank(
        "what does a level three engineer earn",
        [make_scored("wrong", "Orders below 50 dollars pay a flat standard shipping fee.", vector_score=0.0)],
        0.08,
        0.42,
        flat_weights(),
    )
    assert scored[0].rerank_score == 0.0


# ---------------- the whole pipeline ----------------

def test_the_right_document_is_retrieved_first(seeded_store, embedder, chat_client):
    pipeline = RetrievalPipeline(store=seeded_store, embedder=embedder, chat_client=chat_client)

    expectations = [
        ("How long do I have to ask for a refund?", "refund_policy.md"),
        ("What does express delivery cost?", "shipping_policy.md"),
        ("What encryption is used for stored customer data?", "security_faq.md"),
    ]
    for question, expected_source in expectations:
        outcome = pipeline.retrieve(question, caller_allowed_tags=["public", "internal"])
        assert len(outcome.passages) > 0, question
        assert outcome.passages[0].chunk.source == expected_source, question


def test_a_question_outside_the_knowledge_base_retrieves_nothing(seeded_store, embedder, chat_client):
    pipeline = RetrievalPipeline(store=seeded_store, embedder=embedder, chat_client=chat_client)
    outcome = pipeline.retrieve("Who won the football world cup in 1998?",
                                caller_allowed_tags=["public", "internal", "secret"])
    assert outcome.passages == []
    assert outcome.trace.dropped_below_threshold > 0


def test_a_secret_document_is_invisible_to_a_public_caller(seeded_store, embedder, chat_client):
    pipeline = RetrievalPipeline(store=seeded_store, embedder=embedder, chat_client=chat_client)

    as_user = pipeline.retrieve("What does a level three engineer earn?", caller_allowed_tags=["public"])
    for passage in as_user.passages:
        assert passage.chunk.source != "salary_bands.md"

    as_admin = pipeline.retrieve("What does a level three engineer earn?",
                                 caller_allowed_tags=["public", "internal", "secret"])
    sources = []
    for passage in as_admin.passages:
        sources.append(passage.chunk.source)
    assert "salary_bands.md" in sources


def test_the_trace_records_every_stage(seeded_store, embedder, chat_client):
    pipeline = RetrievalPipeline(store=seeded_store, embedder=embedder, chat_client=chat_client)
    outcome = pipeline.retrieve("refund window", caller_allowed_tags=["public"])

    trace = outcome.trace
    assert trace.vector_hits > 0
    assert trace.merged_hits > 0
    assert "vector_ms" in trace.stage_timings_ms
    assert "rerank_ms" in trace.stage_timings_ms
