"""
LAYER 5 - RETRIEVAL, STEP 2: VECTOR SEARCH
==========================================
Finds passages that MEAN the same thing as the query, even with no shared words.

Strong at: paraphrases, synonyms, "how do I get my money back" -> refund policy.
Weak at:   exact strings - order numbers, error codes, product SKUs. A vector
           does not care that you typed ERR-4521 and not ERR-4522.

That weakness is exactly why step 3 exists.
"""

from rag_assistant.layer2_models.schemas import Chunk, ScoredChunk
from rag_assistant.layer3_storage.base import DocumentStore


def run_vector_search(
    store: DocumentStore,
    embedder,
    queries: list[str],
    top_k: int,
    allowed_tags: list[str],
    sources: list[str] | None = None,
) -> list[ScoredChunk]:
    """
    Search once per query variant, then keep the best score for each chunk.

    Returned in rank order, best first, which is what the fusion step needs.
    """
    if len(queries) == 0:
        return []

    embedding_result = embedder.embed(queries)

    best_score_by_chunk: dict[str, float] = {}
    chunk_by_id: dict[str, Chunk] = {}

    for query_vector in embedding_result.vectors:
        hits = store.vector_search(
            query_vector=query_vector,
            top_k=top_k,
            allowed_tags=allowed_tags,
            sources=sources,
        )
        for chunk, score in hits:
            known = best_score_by_chunk.get(chunk.chunk_id)
            if known is None or score > known:
                best_score_by_chunk[chunk.chunk_id] = score
                chunk_by_id[chunk.chunk_id] = chunk

    ordered_ids = sorted(
        best_score_by_chunk.keys(),
        key=lambda chunk_id: best_score_by_chunk[chunk_id],
        reverse=True,
    )

    results: list[ScoredChunk] = []
    rank = 0
    for chunk_id in ordered_ids[:top_k]:
        rank = rank + 1
        results.append(
            ScoredChunk(
                chunk=chunk_by_id[chunk_id],
                score=best_score_by_chunk[chunk_id],
                vector_score=best_score_by_chunk[chunk_id],
                vector_rank=rank,
                reason="vector similarity",
            )
        )
    return results
