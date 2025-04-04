"""
LAYER 5 - WORKERS, STEP 1: SEARCHING
====================================
A thin wrapper around the sources that does two things the sources must not know
about: it charges the budget, and it refuses when there is none left.

WHY SEARCHES ARE BUDGETED SEPARATELY FROM TOKENS
    They are a different kind of cost. Tokens are your bill; searches are load on
    somebody else's service, and the thing that gets your API key revoked. A run
    that stays inside its token budget can still make four hundred searches, and
    the two limits catch different runaway behaviours.
"""

from research_agent.layer0_shared.budget import Budget, BudgetLimit
from research_agent.layer0_shared.logging_setup import get_logger, log_event
from research_agent.layer2_models.schemas import SearchHit
from research_agent.layer3_sources.registry import search_everywhere

log = get_logger(__name__)


class SearchService:
    """The only way a worker reaches a source."""

    def __init__(self, budget: Budget) -> None:
        self.budget = budget

    def search(self, query: str, top_k: int, describe: str = "") -> list[SearchHit]:
        """
        Run one search, or return nothing if the budget says no.

        Returning an empty list rather than raising means a worker that runs out
        of searches produces a thin section, not a failed run.
        """
        limit = self.budget.check()
        if limit != BudgetLimit.NONE:
            if describe == "":
                describe = "search: " + query[:60]
            self.budget.record_refusal(describe, limit)
            return []

        self.budget.charge_tool_call()

        hits = search_everywhere(query, top_k)

        log_event(
            log,
            "search.done",
            query=query[:80],
            results=len(hits),
            tool_calls_used=self.budget.tool_calls_made,
        )
        return hits
