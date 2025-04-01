"""
LAYER 0 - SHARED: THE BUDGET
============================
The most important file in this project.

A research agent decides for itself how much work to do. Left alone it will
decompose a question into eight sub-questions, each of which spawns four
searches, each of which gets summarised, and the bill arrives later. There is no
natural stopping point, because "I have researched enough" is a judgement the
agent is not well placed to make about your money.

So there is a budget, and it covers four different ways a run can get away from you:

    TOKENS       the bill
    TOOL CALLS   the load you put on whatever you are searching
    SECONDS      the user's patience
    DEPTH        how far the planner may decompose before it must stop

------------------------------------------------------------------------------
THE RULE THAT MATTERS MOST: ONE BUDGET, SHARED BY EVERYONE
------------------------------------------------------------------------------
The obvious design is to give each worker its own budget. It is also wrong, and
wrong in a way that looks fine in testing:

    4 workers x 30,000 tokens each = 120,000 tokens
    8 workers x 30,000 tokens each = 240,000 tokens

The limit silently scales with the number of workers, which is precisely the
thing you were trying to control. So there is ONE Budget object per run and every
agent charges against it. Workers run in parallel threads, so every counter is
behind a lock.

------------------------------------------------------------------------------
RUNNING OUT IS A NORMAL OUTCOME, NOT AN ERROR
------------------------------------------------------------------------------
When the budget is exhausted the run does not crash and does not return nothing.
It stops starting new work and synthesises a report from the evidence it already
has, clearly marked as partial, listing what it did not get to.

A partial answer with its gaps named is useful. A crash after ninety seconds and
two dollars is not.
"""

import threading
import time
from enum import Enum


class BudgetLimit(str, Enum):
    """Which limit ran out. Recorded so the report can say so plainly."""

    TOKENS = "tokens"
    TOOL_CALLS = "tool_calls"
    SECONDS = "seconds"
    DEPTH = "depth"
    NONE = "none"


class BudgetExhausted(Exception):
    """Raised only where stopping immediately is the right thing to do."""

    def __init__(self, limit: BudgetLimit, detail: str) -> None:
        super().__init__(detail)
        self.limit = limit
        self.detail = detail


class Budget:
    """One budget for one research run, shared by every agent in it."""

    def __init__(
        self,
        max_tokens: int,
        max_tool_calls: int,
        max_seconds: float,
        max_depth: int,
        reserve_fraction: float = 0.15,
    ) -> None:
        self.max_tokens = max_tokens
        self.max_tool_calls = max_tool_calls
        self.max_seconds = max_seconds
        self.max_depth = max_depth

        # HOLD SOMETHING BACK FOR THE ANSWER.
        # Research will consume every token you give it. If it consumes all of
        # them, there is nothing left to write the report with, and you have paid
        # full price for a pile of evidence and no answer. This fraction is
        # reserved: ordinary work is refused once it is reached, and only
        # synthesis may spend it.
        self.reserve_fraction = reserve_fraction

        self.spent_tokens = 0
        self.spent_cost_usd = 0.0
        self.tool_calls_made = 0
        self.model_calls_made = 0
        self.started_at = time.monotonic()

        self.stopped_because = BudgetLimit.NONE
        self.refusals: list[str] = []

        # Workers run in parallel, so every counter is behind this.
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    #  Asking whether there is room
    # ------------------------------------------------------------------

    def seconds_used(self) -> float:
        return time.monotonic() - self.started_at

    def seconds_left(self) -> float:
        remaining = self.max_seconds - self.seconds_used()
        if remaining < 0:
            return 0.0
        return remaining

    def working_token_limit(self) -> int:
        """The ceiling for ordinary research work, with the reserve held back."""
        return int(self.max_tokens * (1.0 - self.reserve_fraction))

    def check(self, for_synthesis: bool = False) -> BudgetLimit:
        """
        Which limit, if any, has been reached.

        for_synthesis=True allows spending into the reserve, because writing the
        report is the one thing the reserve exists for.
        """
        with self._lock:
            if self.seconds_used() >= self.max_seconds:
                return BudgetLimit.SECONDS

            if for_synthesis:
                token_ceiling = self.max_tokens
            else:
                token_ceiling = self.working_token_limit()

            if self.spent_tokens >= token_ceiling:
                return BudgetLimit.TOKENS

            if self.tool_calls_made >= self.max_tool_calls:
                return BudgetLimit.TOOL_CALLS

            return BudgetLimit.NONE

    def has_room(self, for_synthesis: bool = False) -> bool:
        return self.check(for_synthesis) == BudgetLimit.NONE

    def depth_allowed(self, depth: int) -> bool:
        """Depth 0 is the original question. max_depth 2 allows 0, 1 and 2."""
        return depth <= self.max_depth

    # ------------------------------------------------------------------
    #  Spending
    # ------------------------------------------------------------------

    def charge_model_call(self, tokens: int, cost_usd: float) -> None:
        with self._lock:
            self.spent_tokens = self.spent_tokens + tokens
            self.spent_cost_usd = self.spent_cost_usd + cost_usd
            self.model_calls_made = self.model_calls_made + 1

    def charge_tool_call(self) -> None:
        with self._lock:
            self.tool_calls_made = self.tool_calls_made + 1

    def record_refusal(self, what: str, limit: BudgetLimit) -> None:
        """
        Note that something was not done, and why.

        This is what turns "the report is thin" into "the report is thin because
        we ran out of time after four of six sub-questions". The second one tells
        you whether to raise the budget or fix the retrieval.
        """
        with self._lock:
            note = "%s (stopped by %s)" % (what, limit.value)
            if note not in self.refusals:
                self.refusals.append(note)
            if self.stopped_because == BudgetLimit.NONE:
                self.stopped_because = limit

    # ------------------------------------------------------------------
    #  Reporting
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            seconds_used = round(self.seconds_used(), 2)

            if self.max_tokens > 0:
                token_fraction = round(self.spent_tokens / self.max_tokens, 4)
            else:
                token_fraction = 0.0

            return {
                "tokens": {
                    "spent": self.spent_tokens,
                    "limit": self.max_tokens,
                    "working_limit": self.working_token_limit(),
                    "fraction_used": token_fraction,
                },
                "tool_calls": {"made": self.tool_calls_made, "limit": self.max_tool_calls},
                "model_calls": self.model_calls_made,
                "seconds": {"used": seconds_used, "limit": self.max_seconds},
                "depth_limit": self.max_depth,
                "cost_usd": round(self.spent_cost_usd, 6),
                "stopped_because": self.stopped_because.value,
                "work_not_done": list(self.refusals),
            }

    def was_exhausted(self) -> bool:
        return self.stopped_because != BudgetLimit.NONE


def build_budget() -> Budget:
    """A budget from configuration. One per research run."""
    from research_agent.layer1_config.settings import settings

    return Budget(
        max_tokens=settings.budget_max_tokens,
        max_tool_calls=settings.budget_max_tool_calls,
        max_seconds=settings.budget_max_seconds,
        max_depth=settings.budget_max_depth,
    )
