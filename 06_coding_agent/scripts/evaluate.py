"""
Run the benchmark.

    .venv/bin/python scripts/evaluate.py            offline, free, measures the HARNESS
    .venv/bin/python scripts/evaluate.py --live     uses the key in .env, measures the AGENT
    .venv/bin/python scripts/evaluate.py --task tax_sign

Exits non-zero if any accepted patch tampered with the tests or broke something,
so this can be wired into CI as a safety gate.
"""

import sys

from coding_agent.layer1_config.settings import SETTINGS
from coding_agent.layer9_api.runner import run_task
from coding_agent.layer10_evaluation.step1_benchmark import render, run_benchmark
from coding_agent.layer10_evaluation.tasks import all_task_ids


def main() -> int:
    live = "--live" in sys.argv

    if live and SETTINGS.is_offline():
        print("--live was asked for, but there is no OPENAI_API_KEY in .env.")
        return 2

    task_ids = all_task_ids()
    if "--task" in sys.argv:
        position = sys.argv.index("--task")
        if position + 1 < len(sys.argv):
            task_ids = [sys.argv[position + 1]]

    def show(result):
        print("   %-20s %-11s %d iteration(s), %.1fs"
              % (result.task_id, result.outcome.value, result.iterations,
                 result.seconds))

    print("running %d task(s), %s...\n"
          % (len(task_ids), "live" if live else "offline"))

    def run_one(task_id, offline):
        return run_task(task_id, offline=offline)

    report = run_benchmark(task_ids, offline=not live, run_one=run_one, progress=show)
    print()
    print(render(report))

    if not report.is_safe():
        print("FAILED: a patch was accepted while cheating.")
        return 1

    print("PASSED: no patch was accepted while cheating.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
