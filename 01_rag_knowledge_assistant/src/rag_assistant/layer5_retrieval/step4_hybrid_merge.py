"""
LAYER 5 - RETRIEVAL, STEP 4: HYBRID MERGE (Reciprocal Rank Fusion)
==================================================================
We now have two result lists with scores that mean completely different things:

    vector search  -> cosine similarity, roughly 0.0 to 1.0
    keyword search -> BM25, unbounded, can be 0.4 or 14.0

Adding those numbers together is meaningless. Normalising them is fragile,
because one outlier changes every score.

Reciprocal Rank Fusion sidesteps the problem by throwing the scores away and
using only the POSITION in each list:

    contribution = 1 / (k + rank)

A passage ranked first in both lists beats a passage ranked first in one list
and missing from the other. k (60 by convention) softens the difference between
rank 1 and rank 2 so a single list cannot dominate.

It is simple, needs no tuning, and is what most production hybrid search uses.
"""

from rag_assistant.layer2_models.schemas import ScoredChunk

RRF_K = 60.0


def reciprocal_rank_fusion(
    vector_results: list[ScoredChunk],
    keyword_results: list[ScoredChunk],
    vector_weight: float = 1.0,
    keyword_weight: float = 1.0,
) -> list[ScoredChunk]:
    """
    Combine two ranked lists into one.

    The weights let you say "trust meaning more than words" (or the reverse) for
    a particular corpus. Both default to 1.0, which is the plain algorithm.
    """
    fused_score_by_chunk: dict[str, float] = {}
    merged_by_chunk: dict[str, ScoredChunk] = {}

    rank = 0
    for item in vector_results:
        rank = rank + 1
        chunk_id = item.chunk.chunk_id
        contribution = vector_weight * (1.0 / (RRF_K + rank))

        fused_score_by_chunk[chunk_id] = fused_score_by_chunk.get(chunk_id, 0.0) + contribution
        merged_by_chunk[chunk_id] = ScoredChunk(
            chunk=item.chunk,
            score=0.0,
            vector_rank=rank,
            vector_score=item.vector_score,
            reason="found by meaning",
        )

    rank = 0
    for item in keyword_results:
        rank = rank + 1
        chunk_id = item.chunk.chunk_id
        contribution = keyword_weight * (1.0 / (RRF_K + rank))

        fused_score_by_chunk[chunk_id] = fused_score_by_chunk.get(chunk_id, 0.0) + contribution

        if chunk_id in merged_by_chunk:
            existing = merged_by_chunk[chunk_id]
            existing.keyword_rank = rank
            existing.keyword_score = item.keyword_score
            existing.reason = "found by meaning and by words"
        else:
            merged_by_chunk[chunk_id] = ScoredChunk(
                chunk=item.chunk,
                score=0.0,
                keyword_rank=rank,
                keyword_score=item.keyword_score,
                reason="found by words",
            )

    ordered_ids = sorted(
        fused_score_by_chunk.keys(),
        key=lambda chunk_id: fused_score_by_chunk[chunk_id],
        reverse=True,
    )

    fused: list[ScoredChunk] = []
    for chunk_id in ordered_ids:
        item = merged_by_chunk[chunk_id]
        item.score = round(fused_score_by_chunk[chunk_id], 6)
        fused.append(item)

    return fused
