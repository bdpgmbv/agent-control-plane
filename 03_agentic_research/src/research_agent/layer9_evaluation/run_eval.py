"""
LAYER 9 - EVALUATION: THE RUNNER
================================
Runs every golden question through the SAME service the API uses.

    python -m research_agent.layer9_evaluation.run_eval

Two columns of results, kept apart on purpose:

  QUALITY   did it find the right sources, avoid the wrong ones, notice the
            disagreements. Percentages. Improving these is ordinary work.

  SAFETY    invented quotes, figures that appear in no source, budgets exceeded,
            a partial report presenting itself as complete. Counted, and the
            target is zero. A research system that occasionally invents a quote
            is not a research system.
"""

import json
import time
from pathlib import Path

from research_agent.layer1_config.settings import settings
from research_agent.layer2_models.schemas import ResearchRequest
from research_agent.layer8_api.research_service import ResearchService
from research_agent.layer9_evaluation.checks import check_question

DATASET_PATH = Path(__file__).resolve().parent / "golden_questions.json"
RESULTS_FOLDER = Path(__file__).resolve().parents[3] / "eval_results"


def load_questions() -> dict:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def average_of(values: list[float]) -> float:
    if len(values) == 0:
        return 0.0
    total = 0.0
    for value in values:
        total = total + value
    return round(total / len(values), 4)


def run_evaluation() -> dict:
    dataset = load_questions()
    service = ResearchService()

    reports: list[dict] = []
    started_all = time.perf_counter()

    for expectation in dataset["questions"]:
        budget_overrides = expectation.get("budget", {})

        request = ResearchRequest(
            question=expectation["question"],
            max_tokens=budget_overrides.get("max_tokens"),
            max_seconds=budget_overrides.get("max_seconds"),
            max_tool_calls=budget_overrides.get("max_tool_calls"),
        )

        response = service.research(request)
        checks = check_question(expectation, response)

        check_dicts: list[dict] = []
        for check in checks:
            check_dicts.append(check.to_dict())

        reports.append(
            {
                "id": expectation["id"],
                "question": expectation["question"],
                "note": expectation.get("note", ""),
                "checks": check_dicts,
                "summary_text": response.report.summary,
                "citations": len(response.report.citations),
                "conflicts": len(response.report.conflicts),
                "partial": response.report.partial,
                "confidence": response.report.confidence,
                "gaps": len(response.report.gaps),
                "sub_questions": len(response.plan.sub_questions),
                "researched": response.plan.researched_count(),
                "seconds": response.seconds,
                "cost_usd": response.cost_usd,
                "budget": response.trace.budget,
                "duplicates_removed": response.trace.duplicates_removed,
            }
        )

    total_seconds = round(time.perf_counter() - started_all, 2)
    summary = build_summary(reports)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "configuration": settings.describe(),
        "questions_run": len(reports),
        "total_seconds": total_seconds,
        "summary": summary,
        "questions": reports,
    }

    RESULTS_FOLDER.mkdir(parents=True, exist_ok=True)
    output_path = RESULTS_FOLDER / ("eval_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["saved_to"] = str(output_path)

    return report


def build_summary(reports: list[dict]) -> dict:
    total_checks = 0
    passed_checks = 0
    critical_total = 0
    critical_failed = 0
    critical_failures: list[str] = []
    by_check: dict[str, dict] = {}

    questions_fully_passed = 0

    seconds: list[float] = []
    costs: list[float] = []
    confidences: list[float] = []
    duplicates: list[float] = []

    for question in reports:
        all_passed = True

        for check in question["checks"]:
            total_checks = total_checks + 1
            name = check["name"]

            if name not in by_check:
                by_check[name] = {"total": 0, "passed": 0}
            by_check[name]["total"] = by_check[name]["total"] + 1

            if check["passed"]:
                passed_checks = passed_checks + 1
                by_check[name]["passed"] = by_check[name]["passed"] + 1
            else:
                all_passed = False

            if check["critical"]:
                critical_total = critical_total + 1
                if not check["passed"]:
                    critical_failed = critical_failed + 1
                    critical_failures.append("%s: %s (%s)" % (question["id"], name, check["detail"]))

        if all_passed:
            questions_fully_passed = questions_fully_passed + 1

        seconds.append(question["seconds"])
        costs.append(question["cost_usd"])
        confidences.append(question["confidence"])
        duplicates.append(question["duplicates_removed"])

    if total_checks > 0:
        pass_rate = round(passed_checks / total_checks, 4)
    else:
        pass_rate = 0.0

    seconds_sorted = sorted(seconds)
    if len(seconds_sorted) > 0:
        p50 = seconds_sorted[int(round(0.50 * (len(seconds_sorted) - 1)))]
        p95 = seconds_sorted[int(round(0.95 * (len(seconds_sorted) - 1)))]
    else:
        p50 = 0.0
        p95 = 0.0

    total_cost = 0.0
    for cost in costs:
        total_cost = total_cost + cost

    return {
        "questions_fully_passed": questions_fully_passed,
        "questions_total": len(reports),
        "checks_passed": passed_checks,
        "checks_total": total_checks,
        "check_pass_rate": pass_rate,
        "safety": {
            "critical_checks": critical_total,
            "critical_failures": critical_failed,
            "failures": critical_failures,
        },
        "by_check": by_check,
        "performance": {
            "p50_seconds": round(p50, 2),
            "p95_seconds": round(p95, 2),
            "average_confidence": average_of(confidences),
            "average_duplicates_removed": average_of(duplicates),
            "total_cost_usd": round(total_cost, 6),
            "average_cost_per_question_usd": round(average_of(costs), 8),
        },
    }


def print_report(report: dict) -> None:
    summary = report["summary"]
    configuration = report["configuration"]

    print("")
    print("=" * 78)
    print(" AGENTIC RESEARCH SYSTEM - EVALUATION REPORT")
    print("=" * 78)
    print(" model     : %s   (live: %s)" % (configuration["llm_model"], configuration["llm_is_live"]))
    print(" questions : %d in %.1fs" % (report["questions_run"], report["total_seconds"]))
    print("")

    print(" OVERALL")
    print("   questions fully passed   %d of %d" % (summary["questions_fully_passed"], summary["questions_total"]))
    print("   individual checks passed %d of %d   (%.1f%%)" % (
        summary["checks_passed"], summary["checks_total"], summary["check_pass_rate"] * 100))
    print("")

    safety = summary["safety"]
    print(" SAFETY  (target is zero, not a percentage)")
    print("   critical checks run  %d" % safety["critical_checks"])
    print("   critical FAILURES    %d" % safety["critical_failures"])
    if safety["critical_failures"] > 0:
        for failure in safety["failures"]:
            print("      ! %s" % failure)
    else:
        print("      no invented quotes, no unsupported figures, no budget overruns,")
        print("      and every partial report declared itself partial")
    print("")

    print(" BY CHECK")
    for name in sorted(summary["by_check"].keys()):
        entry = summary["by_check"][name]
        if entry["total"] > 0:
            rate = entry["passed"] / entry["total"]
        else:
            rate = 0.0
        print("   %-38s %d/%d  %.0f%%" % (name, entry["passed"], entry["total"], rate * 100))
    print("")

    performance = summary["performance"]
    print(" PERFORMANCE AND COST")
    print("   p50 run time            %.1f s" % performance["p50_seconds"])
    print("   p95 run time            %.1f s" % performance["p95_seconds"])
    print("   average confidence      %.2f" % performance["average_confidence"])
    print("   duplicates removed/run  %.1f" % performance["average_duplicates_removed"])
    print("   cost per question       $%.5f" % performance["average_cost_per_question_usd"])
    print("   total run cost          $%.5f" % performance["total_cost_usd"])
    print("")

    print(" FAILED CHECKS")
    any_failure = False
    for question in report["questions"]:
        for check in question["checks"]:
            if check["passed"]:
                continue
            any_failure = True
            print("   [%s] %s" % (question["id"], check["name"]))
            print("        %s" % check["detail"][:110])
    if not any_failure:
        print("   none")
    print("")
    print(" saved to %s" % report.get("saved_to", "-"))
    print("=" * 78)


def main() -> None:
    report = run_evaluation()
    print_report(report)


if __name__ == "__main__":
    main()
