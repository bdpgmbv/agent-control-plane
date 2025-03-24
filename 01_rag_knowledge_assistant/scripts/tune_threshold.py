"""
Sweep the relevance threshold and show what it costs you.

    python scripts/tune_threshold.py

WHY THIS SCRIPT EXISTS
    MIN_RELEVANCE_SCORE decides when the assistant refuses to answer. It is the
    most consequential number in the system, and there is no correct value in the
    abstract - only a tradeoff:

        threshold too low  -> it answers questions it has no source for
                              (confident nonsense)
        threshold too high -> it refuses questions it could have answered
                              (a useless assistant)

    So you do not guess it. You measure both failure kinds at several values and
    choose deliberately. That is what this table is for - and it is the same
    method for every threshold in every LLM system you will build.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from rag_assistant.layer1_config.settings import settings  # noqa: E402
from rag_assistant.layer8_evaluation.run_eval import run_evaluation  # noqa: E402

THRESHOLDS_TO_TRY = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.60]


def main() -> None:
    original = settings.min_relevance_score

    print("")
    print("Sweeping MIN_RELEVANCE_SCORE. Current value: %.2f" % original)
    print("")
    print(" threshold | recall@5 | answer acc | refused ok | hallucinated | wrongly refused")
    print("-----------+----------+------------+------------+--------------+----------------")

    rows: list[tuple] = []
    for threshold in THRESHOLDS_TO_TRY:
        settings.min_relevance_score = threshold
        report = run_evaluation(use_judge=False)
        summary = report["summary"]

        recall = summary["retrieval"]["recall_at_5"]
        accuracy = summary["answers"]["answer_accuracy"]
        refused_ok = summary["honesty"]["abstain_accuracy"]
        hallucinated = summary["honesty"]["hallucinated_answers"]
        wrongly_refused = summary["answers"]["wrongly_refused"]

        print(
            "   %.2f    |  %.3f   |   %.3f    |   %.3f    |      %d       |       %d"
            % (threshold, recall, accuracy, refused_ok, hallucinated, wrongly_refused)
        )
        rows.append((threshold, accuracy, refused_ok, hallucinated, wrongly_refused))

    settings.min_relevance_score = original

    # The best threshold answers the most questions correctly while never
    # answering one it should have refused.
    best_threshold = None
    best_score = -1.0
    for threshold, accuracy, refused_ok, hallucinated, _wrongly_refused in rows:
        if hallucinated > 0:
            continue
        combined = accuracy + refused_ok
        if combined > best_score:
            best_score = combined
            best_threshold = threshold

    print("")
    if best_threshold is None:
        print("No threshold avoided every hallucination. Improve retrieval before tuning.")
    else:
        print("Best value with zero hallucinations: MIN_RELEVANCE_SCORE=%.2f" % best_threshold)
        print("Set it in your .env file.")
    print("")


if __name__ == "__main__":
    main()
