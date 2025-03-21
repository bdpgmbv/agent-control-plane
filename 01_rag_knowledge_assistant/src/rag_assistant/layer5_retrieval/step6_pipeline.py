"""
LAYER 5 - RETRIEVAL, STEP 6: THE PIPELINE
=========================================
Runs steps 1 to 5 in order and records what happened at every stage.

    rewrite -> vector search -> keyword search -> fuse -> rerank -> threshold

The RetrievalTrace it returns is not a nice-to-have. When someone reports a bad
answer, the first question is always "what did it retrieve?" - and without a
trace you cannot answer that, so you end up guessing.

The relevance threshold at the end is what makes "I don't know" possible. If the
best passage still scores badly, we hand back nothing and let layer 6 abstain,
rather than handing the model weak context and hoping.
"""

import time

from rag_assistant.layer0_shared.logging_setup import get_logger, log_event
from rag_assistant.layer0_shared.usage_tracker import UsageAccumulator
from rag_assistant.layer1_config.settings import settings
from rag_assistant.layer2_models.schemas import RetrievalTrace, ScoredChunk
from rag_assistant.layer3_storage.base import DocumentStore
from rag_assistant.layer5_retrieval.step1_rewrite_query import build_query_variants
from rag_assistant.layer5_retrieval.step2_vector_search import run_vector_search
from rag_assistant.layer5_retrieval.step3_keyword_search import run_keyword_search
from rag_assistant.layer5_retrieval.step4_hybrid_merge import reciprocal_rank_fusion
from rag_assistant.layer5_retrieval.step5_rerank import rerank
from rag_assistant.layer5_retrieval.term_weights import build_term_weights

log = get_logger(__name__)

# How many fused candidates to send to the reranker. More costs more.
RERANK_CANDIDATE_LIMIT = 25


class RetrievalOutcome:
    """Everything retrieval produced."""

    def __init__(
        self,
        passages: list[ScoredChunk],
        trace: RetrievalTrace,
        usage: UsageAccumulator,
    ) -> None:
        self.passages = passages
        self.trace = trace
        self.usage = usage


def resolve_allowed_tags(caller_allowed_tags: list[str], requested_tags: list[str] | None) -> list[str]:
    """
    Work out which access tags this search may touch.

    The caller's permissions are the ceiling. A requested filter can only narrow
    that set, never widen it - which is why we intersect instead of replacing.
    This one function is the reason a "user" key cannot read "secret" documents
    by passing a clever filter.
    """
    if requested_tags is None or len(requested_tags) == 0:
        return list(caller_allowed_tags)

    allowed: list[str] = []
    for tag in requested_tags:
        if tag in caller_allowed_tags:
            allowed.append(tag)
    return allowed


class RetrievalPipeline:
    """Finds the passages most likely to answer a question."""

    def __init__(self, store: DocumentStore, embedder, chat_client) -> None:
        self.store = store
        self.embedder = embedder
        self.chat_client = chat_client

    def retrieve(
        self,
        question: str,
        caller_allowed_tags: list[str],
        requested_tags: list[str] | None = None,
        sources: list[str] | None = None,
        top_k: int | None = None,
    ) -> RetrievalOutcome:
        if top_k is None:
            top_k = settings.rerank_top_k

        allowed_tags = resolve_allowed_tags(caller_allowed_tags, requested_tags)
        usage = UsageAccumulator()

        trace = RetrievalTrace(
            original_query=question,
            filters_applied={
                "allowed_tags": allowed_tags,
                "sources": sources,
            },
        )
        timings: dict[str, int] = {}

        # --- step 1: rewrite ---
        started = time.perf_counter()
        queries = build_query_variants(question, self.chat_client, usage)
        timings["rewrite_ms"] = int((time.perf_counter() - started) * 1000)
        trace.rewritten_queries = queries[1:]

        if len(allowed_tags) == 0:
            # The caller may see nothing at all. Stop here rather than querying.
            trace.stage_timings_ms = timings
            return RetrievalOutcome(passages=[], trace=trace, usage=usage)

        # --- step 2: vector search ---
        started = time.perf_counter()
        embedding_result = self.embedder.embed(queries)
        usage.add_embedding_call("query_embedding", embedding_result.tokens, embedding_result.cost_usd)
        vector_results = run_vector_search(
            store=self.store,
            embedder=PreEmbedded(embedding_result),
            queries=queries,
            top_k=settings.vector_top_k,
            allowed_tags=allowed_tags,
            sources=sources,
        )
        timings["vector_ms"] = int((time.perf_counter() - started) * 1000)
        trace.vector_hits = len(vector_results)

        # --- step 3: keyword search ---
        started = time.perf_counter()
        keyword_results = run_keyword_search(
            store=self.store,
            queries=queries,
            top_k=settings.keyword_top_k,
            allowed_tags=allowed_tags,
            sources=sources,
        )
        timings["keyword_ms"] = int((time.perf_counter() - started) * 1000)
        trace.keyword_hits = len(keyword_results)

        # --- step 4: fuse ---
        started = time.perf_counter()
        fused = reciprocal_rank_fusion(vector_results, keyword_results)
        timings["fusion_ms"] = int((time.perf_counter() - started) * 1000)
        trace.merged_hits = len(fused)

        # --- step 5: rerank the shortlist ---
        started = time.perf_counter()
        shortlist = fused[:RERANK_CANDIDATE_LIMIT]
        reranked = rerank(
            question=question,
            candidates=shortlist,
            chat_client=self.chat_client,
            use_model=settings.enable_rerank,
            relevance_floor=getattr(self.embedder, "relevance_floor", 0.10),
            relevance_ceiling=getattr(self.embedder, "relevance_ceiling", 0.60),
            term_weights=build_term_weights(self.store, queries),
            semantic_trust=getattr(self.embedder, "semantic_trust", 1.0),
            usage=usage,
        )
        timings["rerank_ms"] = int((time.perf_counter() - started) * 1000)
        trace.reranked_hits = len(reranked)

        # --- step 6: keep the best, and only if they are good enough ---
        best = reranked[:top_k]

        kept: list[ScoredChunk] = []
        dropped = 0
        for candidate in best:
            effective_score = candidate.rerank_score
            if effective_score is None:
                effective_score = candidate.score
            if effective_score >= settings.min_relevance_score:
                kept.append(candidate)
            else:
                dropped = dropped + 1

        trace.dropped_below_threshold = dropped
        trace.stage_timings_ms = timings

        log_event(
            log,
            "retrieval.finished",
            question=question,
            queries_used=len(queries),
            vector_hits=trace.vector_hits,
            keyword_hits=trace.keyword_hits,
            kept=len(kept),
            dropped_below_threshold=dropped,
            timings_ms=timings,
        )

        return RetrievalOutcome(passages=kept, trace=trace, usage=usage)


class PreEmbedded:
    """
    A tiny adapter so the vector search step can reuse vectors we already paid
    for, instead of embedding the same queries a second time.

    Without this the pipeline would call the embedding API twice per question -
    a silent doubling of cost that is easy to miss.
    """

    def __init__(self, embedding_result) -> None:
        self.embedding_result = embedding_result

    def embed(self, texts: list[str]):
        return self.embedding_result
