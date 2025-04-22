"""
Run the evaluation suite.

    .venv/bin/python scripts/evaluate.py            offline, free
    .venv/bin/python scripts/evaluate.py --live     uses the key in .env

Exits non-zero if any document with a validation error was processed
automatically, so this can be wired into CI as a safety gate.
"""

import sys
from datetime import date

from doc_intelligence.layer1_config.settings import SETTINGS
from doc_intelligence.layer8_review.step1_store import DocumentStore
from doc_intelligence.layer9_api.pipeline import DocumentPipeline
from doc_intelligence.layer10_evaluation.step1_evaluate import evaluate, render

# The corpus contains a document dated 2027, and one check asks whether a
# document is dated in the future. Pinning "today" keeps the measurement the
# same tomorrow as it is now.
FIXED_TODAY = date(2026, 9, 25)


def main() -> int:
    live = "--live" in sys.argv

    if live and SETTINGS.is_offline():
        print("--live was asked for, but there is no OPENAI_API_KEY in .env.")
        return 2

    pipeline = DocumentPipeline(
        store=DocumentStore(":memory:"),
        offline=not live,
        today=FIXED_TODAY,
    )

    report = evaluate(pipeline, SETTINGS.samples_directory)
    print(render(report))

    if not report.is_safe():
        print("FAILED: a document carrying an error was processed automatically.")
        return 1

    print("PASSED: nothing with an error was processed automatically.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
