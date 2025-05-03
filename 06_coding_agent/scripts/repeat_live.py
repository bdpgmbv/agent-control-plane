"""
Run the live benchmark several times and report the spread.

A coding agent is not deterministic, even at temperature 0. Reporting a single
run means reporting whichever number came up, and the temptation is to keep
running until a good one does. Running it N times and printing every result
makes that impossible to do accidentally.

    .venv/bin/python scripts/repeat_live.py 3
"""

import sys

from coding_agent.layer1_config.settings import SETTINGS
from coding_agent.layer9_api.runner import run_task
from coding_agent.layer10_evaluation.step1_benchmark import run_benchmark
from coding_agent.layer10_evaluation.tasks import all_task_ids


def main() -> int:
    if SETTINGS.is_offline():
        print("no OPENAI_API_KEY in .env, so there is nothing live to measure")
        return 2

    repeats = 3
    if len(sys.argv) > 1:
        repeats = int(sys.argv[1])

    task_ids = all_task_ids()
    solved_counts = []
    per_task_solved = {}
    for task_id in task_ids:
        per_task_solved[task_id] = 0

    total_cost = 0.0
    total_tokens = 0
    safety_failures = 0

    def run_one(task_id, offline):
        return run_task(task_id, offline=offline)

    for attempt in range(1, repeats + 1):
        report = run_benchmark(task_ids, offline=False, run_one=run_one)
        solved_counts.append(report.summary.solved)
        total_cost = total_cost + report.summary.total_cost_usd
        total_tokens = total_tokens + report.summary.total_tokens
        if not report.is_safe():
            safety_failures = safety_failures + 1

        line = []
        for row in report.rows:
            line.append("%s=%s" % (row.task_id[:12], "ok" if row.outcome == "solved" else "NO"))
            if row.outcome == "solved":
                per_task_solved[row.task_id] = per_task_solved[row.task_id] + 1
        print("run %d: %d/%d solved   %s"
              % (attempt, report.summary.solved, report.summary.tasks, "  ".join(line)))

    print()
    print("=" * 70)
    print("  %d runs of %d tasks" % (repeats, len(task_ids)))
    print("=" * 70)
    counts_text = []
    for count in solved_counts:
        counts_text.append(str(count))
    print("  solved per run:   " + ", ".join(counts_text))
    print("  best / worst:     %d / %d" % (max(solved_counts), min(solved_counts)))
    print("  mean:             %.1f of %d" % (sum(solved_counts) / len(solved_counts),
                                              len(task_ids)))
    print()
    print("  per task, solved out of %d runs:" % repeats)
    for task_id in task_ids:
        count = per_task_solved[task_id]
        bar = "#" * count + "." * (repeats - count)
        print("     %-20s %s  %d/%d" % (task_id, bar, count, repeats))
    print()
    print("  total cost:       $%.4f over %d runs" % (total_cost, repeats))
    print("  total tokens:     %d" % total_tokens)
    print("  safety failures:  %d  (accepted patches that cheated)" % safety_failures)
    print("=" * 70)
    return 0 if safety_failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
