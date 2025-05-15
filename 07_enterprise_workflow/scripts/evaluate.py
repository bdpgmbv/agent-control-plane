"""
Run the scenario suite.

    python scripts/evaluate.py            offline, free
    python scripts/evaluate.py --live     uses the key in .env

Exits non-zero if any irreversible effect happened twice, or if a failed run
left something behind - so this can be wired into CI as a safety gate.
"""

import sys

from enterprise_workflow.layer1_config.settings import SETTINGS
from enterprise_workflow.layer4_agents.step1_client import build_chat_client
from enterprise_workflow.layer10_evaluation.step1_evaluate import evaluate, render


def main() -> int:
    live = "--live" in sys.argv

    if live and SETTINGS.is_offline():
        print("--live was asked for, but there is no OPENAI_API_KEY in .env.")
        return 2

    client = build_chat_client() if live else None
    report = evaluate(offline=not live, client=client)
    print(render(report))

    if not report.is_safe():
        print("FAILED: something happened twice, or a failure left something behind.")
        return 1
    if report.passed() != len(report.results):
        print("FAILED: %d scenario(s) did not end as they should."
              % (len(report.results) - report.passed()))
        return 1

    print("PASSED: every scenario ended correctly, and nothing happened twice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
