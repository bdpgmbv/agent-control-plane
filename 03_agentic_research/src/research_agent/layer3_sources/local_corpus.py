"""
LAYER 3 - SOURCES: THE LOCAL CORPUS
===================================
A searchable set of documents held in one JSON file.

The search is BM25 over stemmed words - the same classic scoring used in project
01, written out again here rather than imported, because this project is meant to
be readable on its own.

WHAT THE CORPUS IS FOR
    It is not a toy. It is built so that the parts of the system that matter have
    something real to work on:

      * the same study is reported twice, in a journal and in a newspaper,
        so deduplication has a genuine duplicate to find
      * two peer-reviewed studies report different figures for the same question,
        so conflict detection has a genuine conflict to find
      * a company blog claims a far larger effect than any study, so credibility
        weighting has something to push down
      * several documents are about unrelated subjects, so precision is testable

    Every one of those was put there on purpose. A corpus where everything agrees
    tests nothing.
"""

import json
import math
from pathlib import Path

from research_agent.layer0_shared.text_tools import to_stems
from research_agent.layer2_models.schemas import SearchHit, SourceDocument
from research_agent.layer3_sources.base import SourceAdapter

CORPUS_PATH = Path(__file__).resolve().parent / "corpus" / "documents.json"

# Standard BM25 constants.
K1 = 1.5
B = 0.75

# HOW MANY QUERY WORDS A DOCUMENT MUST CONTAIN TO COUNT AS A MATCH AT ALL.
#
# THE FAILURE THIS PREVENTS. Asked "what is the economic impact of deep sea
# mining on coastal fisheries?" - a subject the corpus says nothing about - BM25
# returned a study about remote work, because it contains the word "deep" (in
# "deep concentration"). One word out of seven. Its rescaled relevance was 1.00,
# because rescaling makes the best hit 1.00 no matter how bad it is, and the
# system produced a ten-source report at 0.99 confidence about deep sea mining.
#
# A single matched word is a coincidence, not a match.
MINIMUM_MATCHED_TERMS_FOR_LONG_QUERY = 2
LONG_QUERY_TERM_COUNT = 3

# HOW MUCH OF THE QUERY'S INFORMATION A DOCUMENT MUST COVER.
#
# Requiring two matched words was not enough. "The economic impact of deep sea
# mining on coastal fisheries" matches "economic" and "impact" in half the
# corpus, because those words are everywhere and carry almost no information.
# The words that actually identify the question - deep, sea, mining, coastal,
# fisheries - appear nowhere, and the system still produced a six-source report.
#
# So matched words are weighted by how RARE they are. A word absent from the
# corpus entirely is the most informative word in the query, and failing to match
# it counts heavily against the document. This is the same idea as the term
# weighting in project 01, applied at retrieval rather than at ranking.
#
# THE VALUE IS MEASURED, NOT GUESSED. Running every sub-question the planner
# produces for questions the corpus does and does not answer:
#
#     queries the corpus CAN answer      coverage 0.32 to 0.49
#     queries the corpus CANNOT answer   coverage 0.08 to 0.12
#
# 0.22 sits in the gap with room on both sides. scripts/calibrate_coverage.py
# reproduces those numbers, and should be re-run whenever the corpus changes -
# the gap is a property of this corpus, not a universal constant.
MINIMUM_QUERY_COVERAGE = 0.22


class LocalCorpusSource(SourceAdapter):
    """Searches the bundled corpus."""

    name = "local-corpus"

    def __init__(self, corpus_path: Path | None = None) -> None:
        if corpus_path is None:
            corpus_path = CORPUS_PATH

        raw = json.loads(corpus_path.read_text(encoding="utf-8"))

        self.documents: list[SourceDocument] = []
        for entry in raw["documents"]:
            self.documents.append(SourceDocument(**entry))

        self.build_index()

    def build_index(self) -> None:
        """
        Build the inverted index once, at start-up.

        Searchable text is the title, the topics and the body. The title and
        topics are repeated, which weights them: a document titled "Solid-state
        battery timelines" is more about that subject than one that mentions it
        in passing halfway down.
        """
        self.term_counts_by_document: dict[str, dict[str, int]] = {}
        self.length_by_document: dict[str, int] = {}
        self.documents_containing_term: dict[str, int] = {}
        self.document_by_id: dict[str, SourceDocument] = {}

        total_length = 0

        for document in self.documents:
            self.document_by_id[document.document_id] = document

            searchable = (
                document.title + " " + document.title + " "
                + " ".join(document.topics) + " " + " ".join(document.topics) + " "
                + document.body
            )
            stems = to_stems(searchable)

            counts: dict[str, int] = {}
            for stem in stems:
                if stem not in counts:
                    counts[stem] = 0
                counts[stem] = counts[stem] + 1

            self.term_counts_by_document[document.document_id] = counts
            self.length_by_document[document.document_id] = len(stems)
            total_length = total_length + len(stems)

            for term in counts:
                if term not in self.documents_containing_term:
                    self.documents_containing_term[term] = 0
                self.documents_containing_term[term] = self.documents_containing_term[term] + 1

        if len(self.documents) > 0:
            self.average_length = total_length / len(self.documents)
        else:
            self.average_length = 1.0

    def inverse_document_frequency(self, term: str) -> float:
        """Rare words score higher. A word in every document tells you nothing."""
        containing = self.documents_containing_term.get(term, 0)
        if containing <= 0:
            return 0.0
        numerator = len(self.documents) - containing + 0.5
        denominator = containing + 0.5
        return math.log(1.0 + (numerator / denominator))

    def term_information(self, term: str) -> float:
        """
        How much a word narrows things down. Used for query coverage, not scoring.

        Different from the BM25 idf above in one way that matters: a word that
        appears in NO document scores highest here rather than zero. For scoring,
        a word nothing contains is irrelevant. For asking "did we actually find
        what was asked about?", it is the most important word in the query.
        """
        containing = self.documents_containing_term.get(term, 0)
        return math.log(1.0 + (len(self.documents) / (1.0 + containing)))

    def query_coverage(self, matched_terms: list[str], query_stems: list[str]) -> float:
        """What share of the query's information this document covers, 0.0 to 1.0."""
        distinct: list[str] = []
        for stem in query_stems:
            if stem not in distinct:
                distinct.append(stem)

        if len(distinct) == 0:
            return 0.0

        total_information = 0.0
        matched_information = 0.0

        for term in distinct:
            information = self.term_information(term)
            total_information = total_information + information
            if term in matched_terms:
                matched_information = matched_information + information

        if total_information == 0.0:
            return 0.0
        return matched_information / total_information

    def score_document(self, document_id: str, query_stems: list[str]) -> tuple[float, list[str]]:
        counts = self.term_counts_by_document.get(document_id, {})
        length = self.length_by_document.get(document_id, 1)

        score = 0.0
        matched: list[str] = []
        already_counted: set[str] = set()

        for term in query_stems:
            if term in already_counted:
                continue
            already_counted.add(term)

            frequency = counts.get(term, 0)
            if frequency == 0:
                continue

            matched.append(term)

            idf = self.inverse_document_frequency(term)
            normaliser = 1.0 - B + (B * (length / self.average_length))
            score = score + (idf * ((frequency * (K1 + 1.0)) / (frequency + (K1 * normaliser))))

        return (score, matched)

    def search(self, query: str, top_k: int) -> list[SearchHit]:
        query_stems = to_stems(query)
        if len(query_stems) == 0:
            return []

        # A long query has to match on more than one word. See the note above.
        distinct_query_terms = len(set(query_stems))
        if distinct_query_terms >= LONG_QUERY_TERM_COUNT:
            required_matches = MINIMUM_MATCHED_TERMS_FOR_LONG_QUERY
        else:
            required_matches = 1

        scored: list[tuple[float, SourceDocument, list[str]]] = []
        for document in self.documents:
            score, matched = self.score_document(document.document_id, query_stems)
            if score <= 0:
                continue
            if len(matched) < required_matches:
                continue

            # Did this document cover enough of what was actually asked about?
            if self.query_coverage(matched, query_stems) < MINIMUM_QUERY_COVERAGE:
                continue

            scored.append((score, document, matched))

        scored.sort(key=lambda entry: entry[0], reverse=True)

        # BM25 scores are unbounded, so they are rescaled against the best hit in
        # this result set. That makes "relevance" comparable between searches,
        # which matters because evidence from different sub-questions ends up
        # ranked against each other later.
        if len(scored) == 0:
            return []
        best_score = scored[0][0]

        hits: list[SearchHit] = []
        for score, document, matched in scored[:top_k]:
            if best_score > 0:
                relevance = score / best_score
            else:
                relevance = 0.0
            hits.append(
                SearchHit(
                    document=document,
                    relevance=round(relevance, 4),
                    absolute_score=round(score, 4),
                    matched_terms=matched,
                )
            )
        return hits

    def get_document(self, document_id: str) -> SourceDocument | None:
        return self.document_by_id.get(document_id)

    def describe(self) -> dict:
        return {
            "name": self.name,
            "documents": len(self.documents),
            "average_length_in_words": round(self.average_length, 1),
        }
