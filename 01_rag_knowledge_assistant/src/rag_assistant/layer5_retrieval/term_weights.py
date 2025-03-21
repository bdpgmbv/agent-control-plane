"""
LAYER 5 - RETRIEVAL: HOW MUCH IS EACH WORD WORTH?
=================================================
This file exists because of a real bug.

The question "What is the parental leave policy?" was being answered from the
refund document. The knowledge base has nothing about parental leave, so it
should have refused. What went wrong:

    the relevance score counted every matched word equally,
    the passage matched the single word "policy",
    one word out of three looked like 33% relevance,
    and that was enough to clear the threshold.

But "policy" appears in almost every document in a knowledge base full of
policies. It carries no information. "Parental" carries a lot.

The fix is the oldest idea in search: weight each word by how RARE it is.
A word in 1 chunk out of 500 is a strong signal. A word in 400 out of 500 is
nearly worthless. BM25 already does this for keyword search - this file brings
the same idea to the reranker's relevance judgement.
"""

import math

from rag_assistant.layer0_shared.vocabulary import equivalent_words

# How much to reduce the score when the question's most informative word is
# missing from the passage entirely. Not zero, because a paraphrase can still be
# a real match; not one, because missing the key word usually means wrong passage.
MISSING_KEY_WORD_PENALTY = 0.5


class CorpusTermWeights:
    """Knows how informative each word is, for one particular knowledge base."""

    def __init__(self, total_chunks: int, frequencies: dict[str, int]) -> None:
        self.total_chunks = total_chunks
        self.frequencies = frequencies

    def weight_of(self, term: str) -> float:
        """
        How much this word is worth as evidence.

        A word in every chunk tends towards 0. A word in one chunk is worth a lot.
        """
        if self.total_chunks <= 0:
            return 1.0

        how_many_contain_it = self.frequencies.get(term, 0)
        return math.log(1.0 + (self.total_chunks / (1.0 + how_many_contain_it)))

    def is_matched(self, term: str, passage_terms: set[str]) -> bool:
        """
        Does the passage contain this word, or anything equivalent to it?

        The synonym step is what lets "can I get my money back" match a passage
        that only ever says "refund". Without it, a correctly rewritten query
        still scores zero, and the rewrite was wasted.
        """
        if term in passage_terms:
            return True

        for alternative in equivalent_words(term):
            if alternative in passage_terms:
                return True
        return False

    def weighted_coverage(self, query_terms: list[str], passage_terms: list[str]) -> float:
        """
        What share of the question's INFORMATION the passage covers, from 0 to 1.

        Three things happen here, and each one fixed a real failure:

        1. WORDS ARE WEIGHTED BY RARITY.
           Matching "policy" out of ["parental", "leave", "policy"] is 33% by
           word count but only about 19% by information, because "policy" is
           everywhere in a policy knowledge base and the other two are not.

        2. SYNONYMS COUNT AS MATCHES.
           "money back" matches a passage that says "refund".

        3. MISSING THE KEY WORD IS PENALISED.
           If the single most informative word of the question does not appear at
           all - not even as a synonym - the passage is probably about something
           else. "What is the parental leave policy?" matched a bonus policy on
           the strength of the word "policy" alone until this rule was added.
        """
        if len(query_terms) == 0:
            return 0.0

        passage_set = set(passage_terms)

        total_weight = 0.0
        matched_weight = 0.0
        already_counted: set[str] = set()

        heaviest_term = ""
        heaviest_weight = -1.0

        for term in query_terms:
            if term in already_counted:
                continue
            already_counted.add(term)

            weight = self.weight_of(term)
            total_weight = total_weight + weight

            if weight > heaviest_weight:
                heaviest_weight = weight
                heaviest_term = term

            if self.is_matched(term, passage_set):
                matched_weight = matched_weight + weight

        if total_weight == 0.0:
            return 0.0

        coverage = matched_weight / total_weight

        if heaviest_term != "" and not self.is_matched(heaviest_term, passage_set):
            coverage = coverage * MISSING_KEY_WORD_PENALTY

        return coverage


def build_term_weights(store, query_variants: list[str]) -> CorpusTermWeights:
    """Look up the statistics for exactly the words in this question."""
    from rag_assistant.layer0_shared.text_tools import to_stems

    terms: list[str] = []
    for variant in query_variants:
        for stem in to_stems(variant):
            if stem not in terms:
                terms.append(stem)

    if len(terms) == 0:
        return CorpusTermWeights(total_chunks=0, frequencies={})

    return CorpusTermWeights(
        total_chunks=store.count_chunks(),
        frequencies=store.document_frequencies(terms),
    )
