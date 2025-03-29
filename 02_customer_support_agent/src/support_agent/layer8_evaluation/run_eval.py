"""
LAYER 8 - EVALUATION: THE RUNNER
================================
Runs every golden scenario through the SAME agent the API uses, and prints a
report.

    python -m support_agent.layer8_evaluation.run_eval

Every scenario starts from a freshly seeded database. That is not tidiness - a
refund issued by scenario 4 changes how scenario 6 behaves, and then your
results describe the order the scenarios happened to run in rather than the
agent.

WHAT THE REPORT SEPARATES, AND WHY

    QUALITY      intent accuracy, tool selection, answers containing the facts.
                 Scored as percentages. Improving these is ordinary work.

    SAFETY       money paid that should not have been, data from another
                 customer's account, personal data in the audit log.
                 Counted, not scored, and the target is zero. "We leak customer
                 data 2% of the time" is not a number anyone can ship.
"""

import json
import time
from pathlib import Path

from support_agent.layer0_shared.llm_client import build_chat_client
from support_agent.layer1_config.settings import ApiKeyRecord, settings
from support_agent.layer3_storage.database import Database
from support_agent.layer3_storage.seed_data import seed
from support_agent.layer6_agent.agent import SupportAgent
from support_agent.layer8_evaluation.scenario_checks import check_turn

DATASET_PATH = Path(__file__).resolve().parent / "golden_scenarios.json"
RESULTS_FOLDER = Path(__file__).resolve().parents[3] / "eval_results"


# The evaluation defines its own identities rather than reading them from .env.
#
# Otherwise the scores depend on how this particular deployment happens to be
# configured, and a scenario silently stops running when somebody renames a key.
# An evaluation suite has to be reproducible on any machine.
EVALUATION_IDENTITIES = {
    "alice-key": ApiKeyRecord(key="alice-key", role="customer", customer_id="CUST-1001"),
    "bob-key": ApiKeyRecord(key="bob-key", role="customer", customer_id="CUST-1002"),
    "staff-key": ApiKeyRecord(key="staff-key", role="agent_human", customer_id="*"),
}


def load_scenarios() -> dict:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def fresh_database() -> Database:
    """A clean, seeded database so every scenario starts from the same state."""
    database = Database(settings.sqlite_file())
    database.initialise()
    database.reset_agent_data()
    seed(database, fresh=True)
    return database


def average_of(values: list[float]) -> float:
    if len(values) == 0:
        return 0.0
    total = 0.0
    for value in values:
        total = total + value
    return round(total / len(values), 4)


def run_evaluation() -> dict:
    dataset = load_scenarios()
    chat_client = build_chat_client()

    scenario_reports: list[dict] = []
    started_all = time.perf_counter()

    for scenario in dataset["scenarios"]:
        database = fresh_database()
        agent = SupportAgent(database=database, chat_client=chat_client)

        caller = EVALUATION_IDENTITIES.get(scenario["api_key"])
        if caller is None:
            raise ValueError(
                "Scenario '%s' uses the unknown identity '%s'. Add it to "
                "EVALUATION_IDENTITIES." % (scenario["id"], scenario["api_key"])
            )

        customer_id = caller.customer_id
        if caller.is_human_agent():
            customer_id = scenario.get("acting_for", "CUST-1001")

        conversation_id = ""
        turn_reports: list[dict] = []

        for expectation in scenario["turns"]:
            before = {
                "tickets": database.count_tickets(),
                "approvals": len(database.list_approvals("pending")),
                "refunds": database.count_refunds(),
            }

            response = agent.handle(
                message_text=expectation["message"],
                conversation_id=conversation_id,
                caller=caller,
                customer_id=customer_id,
            )
            conversation_id = response.conversation_id

            checks = check_turn(expectation, response, database, before)

            check_dicts: list[dict] = []
            for check in checks:
                check_dicts.append(check.to_dict())

            tool_summary: list[str] = []
            for trace in response.tool_calls:
                tool_summary.append(trace.tool_name + ":" + trace.outcome)

            escalation_reason = ""
            if response.escalation.reason is not None:
                escalation_reason = response.escalation.reason.value

            turn_reports.append(
                {
                    "message": expectation["message"],
                    "reply": response.reply,
                    "intent": response.intent,
                    "tools": tool_summary,
                    "escalation": escalation_reason,
                    "pii_redacted": response.pii_redacted,
                    "checks": check_dicts,
                    "usage": response.usage.model_dump(),
                }
            )

        database.close()

        scenario_reports.append(
            {
                "id": scenario["id"],
                "note": scenario.get("note", ""),
                "api_key": scenario["api_key"],
                "turns": turn_reports,
            }
        )

    total_seconds = round(time.perf_counter() - started_all, 2)
    summary = build_summary(scenario_reports)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "configuration": settings.describe(),
        "scenarios_run": len(scenario_reports),
        "total_seconds": total_seconds,
        "summary": summary,
        "scenarios": scenario_reports,
    }

    RESULTS_FOLDER.mkdir(parents=True, exist_ok=True)
    output_path = RESULTS_FOLDER / ("eval_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["saved_to"] = str(output_path)

    return report


def build_summary(scenario_reports: list[dict]) -> dict:
    """Turn every check into the handful of numbers that matter."""
    total_checks = 0
    passed_checks = 0

    critical_total = 0
    critical_failed = 0
    critical_failures: list[str] = []

    by_check_name: dict[str, dict] = {}

    latencies: list[float] = []
    tokens: list[float] = []
    costs: list[float] = []
    model_calls: list[float] = []

    scenarios_passed = 0

    for scenario in scenario_reports:
        scenario_ok = True

        for turn in scenario["turns"]:
            usage = turn["usage"]
            latencies.append(usage["latency_ms"])
            tokens.append(usage["total_tokens"])
            costs.append(usage["estimated_cost_usd"])
            model_calls.append(usage["model_calls"])

            for check in turn["checks"]:
                total_checks = total_checks + 1
                name = check["name"]

                if name not in by_check_name:
                    by_check_name[name] = {"total": 0, "passed": 0}
                by_check_name[name]["total"] = by_check_name[name]["total"] + 1

                if check["passed"]:
                    passed_checks = passed_checks + 1
                    by_check_name[name]["passed"] = by_check_name[name]["passed"] + 1
                else:
                    scenario_ok = False

                if check["critical"]:
                    critical_total = critical_total + 1
                    if not check["passed"]:
                        critical_failed = critical_failed + 1
                        critical_failures.append(
                            "%s: %s (%s)" % (scenario["id"], name, check["detail"])
                        )

        if scenario_ok:
            scenarios_passed = scenarios_passed + 1

    if total_checks > 0:
        check_pass_rate = round(passed_checks / total_checks, 4)
    else:
        check_pass_rate = 0.0

    latencies_sorted = sorted(latencies)
    if len(latencies_sorted) > 0:
        p50 = latencies_sorted[int(round(0.50 * (len(latencies_sorted) - 1)))]
        p95 = latencies_sorted[int(round(0.95 * (len(latencies_sorted) - 1)))]
    else:
        p50 = 0
        p95 = 0

    total_cost = 0.0
    for cost in costs:
        total_cost = total_cost + cost

    return {
        "scenarios_passed": scenarios_passed,
        "scenarios_total": len(scenario_reports),
        "checks_passed": passed_checks,
        "checks_total": total_checks,
        "check_pass_rate": check_pass_rate,
        "safety": {
            "critical_checks": critical_total,
            "critical_failures": critical_failed,
            "failures": critical_failures,
        },
        "by_check": by_check_name,
        "performance": {
            "p50_latency_ms": p50,
            "p95_latency_ms": p95,
            "average_tokens_per_turn": average_of(tokens),
            "average_model_calls_per_turn": average_of(model_calls),
            "total_cost_usd": round(total_cost, 6),
            "average_cost_per_turn_usd": round(average_of(costs), 8),
        },
    }


def print_report(report: dict) -> None:
    summary = report["summary"]
    configuration = report["configuration"]

    print("")
    print("=" * 76)
    print(" CUSTOMER SUPPORT AGENT - EVALUATION REPORT")
    print("=" * 76)
    print(" model       : %s   (live: %s)" % (configuration["llm_model"], configuration["llm_is_live"]))
    print(" scenarios   : %d in %.2fs" % (report["scenarios_run"], report["total_seconds"]))
    print("")

    print(" OVERALL")
    print("   scenarios fully passed   %d of %d" % (summary["scenarios_passed"], summary["scenarios_total"]))
    print("   individual checks passed %d of %d   (%.1f%%)" % (
        summary["checks_passed"], summary["checks_total"], summary["check_pass_rate"] * 100))
    print("")

    safety = summary["safety"]
    print(" SAFETY  (target is zero, not a percentage)")
    print("   critical checks run      %d" % safety["critical_checks"])
    print("   critical FAILURES        %d" % safety["critical_failures"])
    if safety["critical_failures"] > 0:
        for failure in safety["failures"]:
            print("      ! %s" % failure)
    else:
        print("      no money paid wrongly, no cross-customer data, no leaked PII")
    print("")

    print(" BY CHECK")
    names = sorted(summary["by_check"].keys())
    for name in names:
        entry = summary["by_check"][name]
        if entry["total"] > 0:
            rate = entry["passed"] / entry["total"]
        else:
            rate = 0.0
        print("   %-34s %d/%d  %.0f%%" % (name, entry["passed"], entry["total"], rate * 100))
    print("")

    performance = summary["performance"]
    print(" PERFORMANCE AND COST")
    print("   p50 latency              %d ms" % performance["p50_latency_ms"])
    print("   p95 latency              %d ms" % performance["p95_latency_ms"])
    print("   model calls per turn     %.1f" % performance["average_model_calls_per_turn"])
    print("   tokens per turn          %.0f" % performance["average_tokens_per_turn"])
    print("   cost per turn            $%.6f" % performance["average_cost_per_turn_usd"])
    print("   total run cost           $%.6f" % performance["total_cost_usd"])
    print("")

    print(" FAILED CHECKS")
    any_failure = False
    for scenario in report["scenarios"]:
        for turn in scenario["turns"]:
            for check in turn["checks"]:
                if check["passed"]:
                    continue
                any_failure = True
                print("   [%s] %s" % (scenario["id"], check["name"]))
                print("        %s" % check["detail"])
                print("        message: %s" % turn["message"][:80])
                print("        reply  : %s" % turn["reply"][:90].replace("\n", " "))
    if not any_failure:
        print("   none")
    print("")
    print(" saved to %s" % report.get("saved_to", "-"))
    print("=" * 76)


def main() -> None:
    report = run_evaluation()
    print_report(report)


if __name__ == "__main__":
    main()
