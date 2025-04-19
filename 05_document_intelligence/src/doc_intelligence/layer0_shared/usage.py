"""
LAYER 0 - USAGE AND COST
========================
Counting tokens per stage, not per run.

Project 01 in this series shipped a cost figure that was 3.6x too low because it
counted only the final call and forgot the rewrite and rerank calls. The fix is
this class: every stage that talks to a model reports through it, so the total is
a sum of real calls and the breakdown shows which stage is expensive.
"""

from dataclasses import dataclass


@dataclass
class StageUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class UsageAccumulator:
    def __init__(self, usd_per_1k_input: float, usd_per_1k_output: float) -> None:
        self.usd_per_1k_input = usd_per_1k_input
        self.usd_per_1k_output = usd_per_1k_output
        self.stages: dict[str, StageUsage] = {}

    def record(self, stage: str, input_tokens: int, output_tokens: int) -> None:
        if stage not in self.stages:
            self.stages[stage] = StageUsage()
        entry = self.stages[stage]
        entry.calls = entry.calls + 1
        entry.input_tokens = entry.input_tokens + input_tokens
        entry.output_tokens = entry.output_tokens + output_tokens

    def total_calls(self) -> int:
        total = 0
        for entry in self.stages.values():
            total = total + entry.calls
        return total

    def total_input_tokens(self) -> int:
        total = 0
        for entry in self.stages.values():
            total = total + entry.input_tokens
        return total

    def total_output_tokens(self) -> int:
        total = 0
        for entry in self.stages.values():
            total = total + entry.output_tokens
        return total

    def total_cost_usd(self) -> float:
        input_cost = (self.total_input_tokens() / 1000.0) * self.usd_per_1k_input
        output_cost = (self.total_output_tokens() / 1000.0) * self.usd_per_1k_output
        return round(input_cost + output_cost, 6)

    def breakdown(self) -> dict:
        result = {}
        for stage_name, entry in self.stages.items():
            input_cost = (entry.input_tokens / 1000.0) * self.usd_per_1k_input
            output_cost = (entry.output_tokens / 1000.0) * self.usd_per_1k_output
            result[stage_name] = {
                "calls": entry.calls,
                "input_tokens": entry.input_tokens,
                "output_tokens": entry.output_tokens,
                "cost_usd": round(input_cost + output_cost, 6),
            }
        return result
