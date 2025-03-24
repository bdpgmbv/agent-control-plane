"""
LAYER 8 - EVALUATION: RETRIEVAL METRICS
=======================================
Retrieval is measured ON ITS OWN, before any answer is generated. This is the
single most useful habit in RAG work.

Why separate? Because "the answer was wrong" has two completely different causes
with two completely different fixes:

    retrieval failed  -> fix chunking, embeddings, hybrid weights, the reranker
    generation failed -> fix the prompt, the model, the citation rules

A single end-to-end score cannot tell them apart, so you end up changing the
prompt to fix a chunking problem.

The four numbers:

  recall@k     Of the documents that SHOULD have been found, what share were in
               the top k? The most important one. If the right passage was never
               retrieved, no prompt can save the answer.

  precision@k  Of what we retrieved, what share was relevant? Low precision
               means we are filling the prompt with noise, which costs money and
               distracts the model.

  MRR          How high up was the FIRST correct result? 1.0 means first place.
               Models pay most attention to the start of the context.

  nDCG@k       Like MRR but credits every correct result, with the ones nearer
               the top counted for more.
"""

import math


def recall_at_k(retrieved_sources: list[str], relevant_sources: list[str], k: int) -> float:
    """Share of the relevant documents that appear in the top k results."""
    if len(relevant_sources) == 0:
        return 1.0   # nothing to find, so nothing was missed

    top = retrieved_sources[:k]

    found = 0
    for source in relevant_sources:
        if source in top:
            found = found + 1

    return round(found / len(relevant_sources), 4)


def precision_at_k(retrieved_sources: list[str], relevant_sources: list[str], k: int) -> float:
    """Share of the top k results that are relevant."""
    top = retrieved_sources[:k]
    if len(top) == 0:
        return 0.0

    hits = 0
    for source in top:
        if source in relevant_sources:
            hits = hits + 1

    return round(hits / len(top), 4)


def reciprocal_rank(retrieved_sources: list[str], relevant_sources: list[str]) -> float:
    """1 divided by the position of the first relevant result. 0 if none."""
    if len(relevant_sources) == 0:
        return 1.0

    position = 0
    for source in retrieved_sources:
        position = position + 1
        if source in relevant_sources:
            return round(1.0 / position, 4)

    return 0.0


def normalised_discounted_cumulative_gain(
    retrieved_sources: list[str],
    relevant_sources: list[str],
    k: int,
) -> float:
    """
    nDCG@k.

    Each relevant result contributes 1 / log2(position + 1), so position 1 is
    worth 1.0, position 2 about 0.63, position 3 about 0.5. We then divide by the
    best score possible for this case, which puts the result on a 0 to 1 scale.
    """
    if len(relevant_sources) == 0:
        return 1.0

    top = retrieved_sources[:k]

    actual = 0.0
    position = 0
    for source in top:
        position = position + 1
        if source in relevant_sources:
            actual = actual + (1.0 / math.log2(position + 1))

    # The ideal ordering: every relevant document, right at the top.
    ideal_count = min(len(relevant_sources), k)
    ideal = 0.0
    position = 0
    while position < ideal_count:
        position = position + 1
        ideal = ideal + (1.0 / math.log2(position + 1))

    if ideal == 0.0:
        return 0.0
    return round(actual / ideal, 4)


def evaluate_one_retrieval(
    retrieved_sources: list[str],
    relevant_sources: list[str],
    k: int = 5,
) -> dict:
    """All four metrics for one question."""
    return {
        "recall_at_k": recall_at_k(retrieved_sources, relevant_sources, k),
        "precision_at_k": precision_at_k(retrieved_sources, relevant_sources, k),
        "reciprocal_rank": reciprocal_rank(retrieved_sources, relevant_sources),
        "ndcg_at_k": normalised_discounted_cumulative_gain(retrieved_sources, relevant_sources, k),
        "retrieved_count": len(retrieved_sources),
    }


def average_of(values: list[float]) -> float:
    """Mean, written out because it is used everywhere in the report."""
    if len(values) == 0:
        return 0.0

    total = 0.0
    for value in values:
        total = total + value
    return round(total / len(values), 4)
