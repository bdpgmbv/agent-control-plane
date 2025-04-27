"""
LAYER 0 - TOKENS, COST AND THE BUDGET
=====================================
One object holds the whole budget for a task, and every part of the agent asks
it for permission before spending anything.

This is the same shape as project 03's shared budget, and it exists for the same
reason: a loop that decides its own stopping condition does not have one. The
agent asks `budget.may_continue()`, and the answer is a fact about tokens and
seconds elapsed, not the model's opinion about whether it is finished.

The reserve matters. When the budget runs out mid-task the agent still has to
write up what it did and hand back a usable result, and that costs tokens too. A
budget spent down to zero leaves nothing to explain itself with.
"""

import time
from dataclasses import dataclass


@dataclass
class StageUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class Budget:
    def __init__(self, max_iterations: int, max_tokens: int, max_seconds: float,
                 usd_per_1k_input: float = 0.00015,
                 usd_per_1k_output: float = 0.00060) -> None:
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self.max_seconds = max_seconds
        self.usd_per_1k_input = usd_per_1k_input
        self.usd_per_1k_output = usd_per_1k_output

        self.iterations_used = 0
        self.stages: dict[str, StageUsage] = {}
        self.started_at = time.time()
        self.stop_reason = ""

    # ---------------- spending ----------------

    def record(self, stage: str, input_tokens: int, output_tokens: int) -> None:
        if stage not in self.stages:
            self.stages[stage] = StageUsage()
        entry = self.stages[stage]
        entry.calls = entry.calls + 1
        entry.input_tokens = entry.input_tokens + input_tokens
        entry.output_tokens = entry.output_tokens + output_tokens

    def start_iteration(self) -> None:
        self.iterations_used = self.iterations_used + 1

    # ---------------- asking ----------------

    def seconds_used(self) -> float:
        return time.time() - self.started_at

    def tokens_used(self) -> int:
        total = 0
        for entry in self.stages.values():
            total = total + entry.input_tokens + entry.output_tokens
        return total

    def total_calls(self) -> int:
        total = 0
        for entry in self.stages.values():
            total = total + entry.calls
        return total

    def may_continue(self) -> bool:
        """
        The stopping condition, as a fact rather than a judgement.

        Whichever limit is hit first is recorded, so the write-up can say "ran
        out of time" rather than the uninformative "stopped".
        """
        if self.iterations_used >= self.max_iterations:
            self.stop_reason = ("reached the limit of %d attempts"
                                % self.max_iterations)
            return False
        if self.tokens_used() >= self.max_tokens:
            self.stop_reason = ("used the whole token budget (%d)" % self.max_tokens)
            return False
        if self.seconds_used() >= self.max_seconds:
            self.stop_reason = ("ran out of time after %.0f seconds"
                                % self.seconds_used())
            return False
        return True

    def remaining_iterations(self) -> int:
        left = self.max_iterations - self.iterations_used
        if left < 0:
            return 0
        return left

    # ---------------- reporting ----------------

    def cost_usd(self) -> float:
        cost = 0.0
        for entry in self.stages.values():
            cost = cost + (entry.input_tokens / 1000.0) * self.usd_per_1k_input
            cost = cost + (entry.output_tokens / 1000.0) * self.usd_per_1k_output
        return round(cost, 6)

    def breakdown(self) -> dict:
        result = {}
        for name, entry in self.stages.items():
            cost = ((entry.input_tokens / 1000.0) * self.usd_per_1k_input
                    + (entry.output_tokens / 1000.0) * self.usd_per_1k_output)
            result[name] = {
                "calls": entry.calls,
                "input_tokens": entry.input_tokens,
                "output_tokens": entry.output_tokens,
                "cost_usd": round(cost, 6),
            }
        return result

    def summary(self) -> dict:
        return {
            "iterations_used": self.iterations_used,
            "max_iterations": self.max_iterations,
            "model_calls": self.total_calls(),
            "tokens_used": self.tokens_used(),
            "max_tokens": self.max_tokens,
            "seconds_used": round(self.seconds_used(), 2),
            "cost_usd": self.cost_usd(),
            "stop_reason": self.stop_reason,
            "by_stage": self.breakdown(),
        }
