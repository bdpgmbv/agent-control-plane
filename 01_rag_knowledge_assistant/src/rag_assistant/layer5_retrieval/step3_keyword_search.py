"""
LAYER 5 - RETRIEVAL, STEP 3: KEYWORD SEARCH
===========================================
Finds passages that use the SAME WORDS as the query, scored with BM25.

Strong at: exact identifiers, names, numbers, rare technical terms.
Weak at:   paraphrases. If the question and the document share no vocabulary,
           keyword search returns nothing at all.

Vector search and keyword search fail in opposite directions. Running both and
combining them is what "hybrid search" means, and it is the single biggest
retrieval quality win in a real system.
"""

from rag_assistant.layer0_shared.text_tools import to_stems
from rag_assistant.layer2_models.schemas import Chunk, ScoredChunk
from rag_assistant.layer3_storage.base import DocumentStore


def run_keyword_search(
    store: DocumentStore,
    queries: list[str],
    top_k: int,
    allowed_tags: list[str],
    sources: list[str] | None = None,
) -> list[ScoredChunk]:
    """Search once per query variant, keeping the best BM25 score per chunk."""
    if len(queries) == 0:
        return []

    best_score_by_chunk: dict[str, float] = {}
    chunk_by_id: dict[str, Chunk] = {}

    for query in queries:
        stems = to_stems(query)
        hits = store.keyword_search(
            query_stems=stems,
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
                keyword_score=best_score_by_chunk[chunk_id],
                keyword_rank=rank,
                reason="keyword match (BM25)",
            )
        )
    return results
