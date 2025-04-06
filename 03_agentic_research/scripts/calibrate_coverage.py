"""
Measure where to put the retrieval coverage threshold.

    python scripts/calibrate_coverage.py

WHY THIS SCRIPT EXISTS
    MINIMUM_QUERY_COVERAGE decides whether a document counts as matching a query
    at all, and therefore whether the system answers a question it has no sources
    for. Too low and it invents a report about deep sea mining from a study about
    remote work. Too high and it finds nothing for questions it could answer.

    There is no correct value in the abstract - only a gap between two
    distributions. This prints both so you can see the gap and choose. Re-run it
    whenever the corpus changes, because the gap is a property of the corpus.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from research_agent.layer0_shared.text_tools import to_stems  # noqa: E402
from research_agent.layer3_sources.local_corpus import (  # noqa: E402
    MINIMUM_QUERY_COVERAGE,
    LocalCorpusSource,
)
from research_agent.layer4_planner.step1_decompose import decompose_with_rules  # noqa: E402

ANSWERABLE = [
    "Is the four-day work week actually good for productivity?",
    "When will solid-state batteries actually be available in cars?",
    "Does remote work increase or decrease productivity?",
    "What limits the cycle life of solid-state batteries?",
    "How did the shipping container change global trade?",
]

UNANSWERABLE = [
    "What is the economic impact of deep sea mining on coastal fisheries?",
    "How effective are vertical farms at reducing food transport emissions?",
    "What are the health effects of volcanic ash on livestock?",
]


def best_coverage(source: LocalCorpusSource, query: str) -> float:
    stems = to_stems(query)
    best = 0.0

    for document in source.documents:
        score, matched = source.score_document(document.document_id, stems)
        if score <= 0:
            continue
        coverage = source.query_coverage(matched, stems)
        if coverage > best:
            best = coverage
    return best


def measure(source: LocalCorpusSource, questions: list[str]) -> list[float]:
    """Every sub-question the planner would produce, scored."""
    values: list[float] = []

    for question in questions:
        plan = decompose_with_rules(question, max_subquestions=6)
        for sub_question in plan["sub_questions"]:
            values.append(best_coverage(source, sub_question))
    return values


def main() -> None:
    source = LocalCorpusSource()

    answerable = measure(source, ANSWERABLE)
    unanswerable = measure(source, UNANSWERABLE)

    answerable.sort()
    unanswerable.sort()

    print("")
    print("Coverage of the query's information by the best matching document.")
    print("")
    print("  questions the corpus CAN answer      (%d sub-questions)" % len(answerable))
    print("     lowest  %.3f" % answerable[0])
    print("     highest %.3f" % answerable[-1])
    print("")
    print("  questions the corpus CANNOT answer   (%d sub-questions)" % len(unanswerable))
    print("     lowest  %.3f" % unanswerable[0])
    print("     highest %.3f" % unanswerable[-1])
    print("")

    gap_bottom = unanswerable[-1]
    gap_top = answerable[0]

    if gap_top > gap_bottom:
        suggested = round((gap_top + gap_bottom) / 2.0, 2)
        print("  There is a clear gap between %.3f and %.3f." % (gap_bottom, gap_top))
        print("  Suggested MINIMUM_QUERY_COVERAGE: %.2f" % suggested)
        print("  Currently set to: %.2f" % MINIMUM_QUERY_COVERAGE)
    else:
        print("  NO CLEAN GAP: the worst answerable query (%.3f) scores below the best" % gap_top)
        print("  unanswerable one (%.3f). No single threshold separates them, so" % gap_bottom)
        print("  retrieval needs improving rather than the threshold retuning.")
    print("")


if __name__ == "__main__":
    main()
