"""
LAYER 0 - SHARED: COUNTING EVERY MODEL CALL
===========================================
One customer message is not one model call. It can be:

    1. intent classification   (only when the rules are unsure)
    2. memory summarisation    (only on long conversations)
    3. one call per agent step (up to MAX_TOOL_STEPS)
    4. a forced final answer   (only if the step limit was hit)

Counting only the last one understates the bill several times over. This adds
them up, and keeps a per-stage breakdown so you can see which stage is expensive
rather than guessing.
"""


class UsageAccumulator:
    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_usd = 0.0
        self.model_calls = 0
        self.by_stage: dict[str, dict] = {}

    def add_model_call(self, stage: str, prompt_tokens: int, completion_tokens: int, cost_usd: float) -> None:
        self.prompt_tokens = self.prompt_tokens + prompt_tokens
        self.completion_tokens = self.completion_tokens + completion_tokens
        self.cost_usd = self.cost_usd + cost_usd
        self.model_calls = self.model_calls + 1

        if stage not in self.by_stage:
            self.by_stage[stage] = {"calls": 0, "tokens": 0, "cost_usd": 0.0}

        entry = self.by_stage[stage]
        entry["calls"] = entry["calls"] + 1
        entry["tokens"] = entry["tokens"] + prompt_tokens + completion_tokens
        entry["cost_usd"] = round(entry["cost_usd"] + cost_usd, 8)

    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_report(self, latency_ms: int = 0):
        from support_agent.layer2_models.schemas import UsageReport

        return UsageReport(
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens(),
            estimated_cost_usd=round(self.cost_usd, 8),
            latency_ms=latency_ms,
            model_calls=self.model_calls,
            by_stage=dict(self.by_stage),
        )
