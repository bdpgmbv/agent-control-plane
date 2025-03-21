"""
LAYER 3 - STORAGE: BM25 KEYWORD SCORING
=======================================
BM25 is the scoring formula classic search engines use. It answers:

    "How well does this passage match these words?"

Three ideas, and that is all:

    1. A word that appears in the passage more often is a stronger signal ...
    2. ... but with diminishing returns (10 mentions is not 10x better than 1).
    3. A word that appears in very FEW passages is more informative than a word
       that appears everywhere ("the" tells you nothing).

Written out longhand here so the formula is readable rather than magic.
"""

import math

# Standard BM25 constants. k1 controls diminishing returns on repeats,
# b controls how much passage length is penalised.
K1 = 1.5
B = 0.75


def inverse_document_frequency(total_chunks: int, chunks_containing_term: int) -> float:
    """Rare words score higher than common words."""
    if chunks_containing_term <= 0:
        return 0.0
    numerator = total_chunks - chunks_containing_term + 0.5
    denominator = chunks_containing_term + 0.5
    return math.log(1.0 + (numerator / denominator))


def bm25_score(
    term_counts_in_chunk: dict[str, int],
    chunk_length: int,
    average_chunk_length: float,
    total_chunks: int,
    chunks_containing_term: dict[str, int],
) -> float:
    """Add up one contribution per matching query term."""
    if chunk_length <= 0 or average_chunk_length <= 0:
        return 0.0

    score = 0.0
    for term in term_counts_in_chunk:
        frequency = term_counts_in_chunk[term]
        document_frequency = chunks_containing_term.get(term, 0)

        idf = inverse_document_frequency(total_chunks, document_frequency)

        length_normaliser = 1.0 - B + (B * (chunk_length / average_chunk_length))
        top = frequency * (K1 + 1.0)
        bottom = frequency + (K1 * length_normaliser)

        score = score + (idf * (top / bottom))

    return score
