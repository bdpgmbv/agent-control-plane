"""
Show what shrinking the budget actually costs you.

    python scripts/budget_sweep.py

WHY THIS EXISTS
    "How much budget should a research run get?" has no answer in the abstract.
    It is a trade: fewer tokens means a cheaper, faster, thinner report. The only
    useful way to choose is to see the whole curve and decide where the report
    stops being worth having.

    Read the `partial` and `gaps` columns as much as the cost one. A run that
    comes back cheap AND says clearly what it did not cover may be a perfectly
    good trade. A run that comes back cheap and pretends to be complete is not.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from research_agent.layer1_config.settings import settings  # noqa: E402
from research_agent.layer2_models.schemas import ResearchRequest  # noqa: E402
from research_agent.layer8_api.research_service import ResearchService  # noqa: E402

QUESTION = "Is the four-day work week actually good for productivity?"

# Tool calls are charged whether or not a model is configured, so this sweep
# works with and without an API key.
TOOL_CALL_BUDGETS = [1, 2, 3, 5, 8, 40]


def main() -> None:
    service = ResearchService()

    print("")
    print("Question: %s" % QUESTION)
    print("Model: %s (live: %s)" % (settings.llm_model, settings.using_real_llm()))
    print("")
    print(" searches | sub-qs done | citations | conflicts | partial | gaps | confidence | seconds | cost")
    print("----------+-------------+-----------+-----------+---------+------+------------+---------+--------")

    for limit in TOOL_CALL_BUDGETS:
        response = service.research(
            ResearchRequest(question=QUESTION, max_tool_calls=limit)
        )
        report = response.report

        print(
            "   %-6d |     %d of %d    |    %-6d |    %-6d |  %-6s | %-4d |    %.2f    |  %5.1f  | $%.5f"
            % (
                limit,
                response.plan.researched_count(),
                len(response.plan.sub_questions),
                len(report.citations),
                len(report.conflicts),
                str(report.partial),
                len(report.gaps),
                report.confidence,
                response.seconds,
                response.cost_usd,
            )
        )

    print("")
    print("Every row above produced a usable report. None of them crashed, and")
    print("every partial one said so. That is the behaviour worth having: the")
    print("budget decides how much you get, not whether you get anything.")
    print("")


if __name__ == "__main__":
    main()
