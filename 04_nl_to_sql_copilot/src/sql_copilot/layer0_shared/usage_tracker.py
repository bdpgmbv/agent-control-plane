"""
LAYER 0 - SHARED: COUNTING EVERY MODEL CALL
===========================================
One question can be three or four calls: generating the SQL, repairing it once or
twice, and explaining the result. The repair loop is where the cost of a question
quietly doubles, so the breakdown is kept per stage rather than as one total.
"""


class UsageAccumulator:
    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_usd = 0.0
        self.model_calls = 0
        self.by_stage: dict[str, dict] = {}

    def add(self, stage: str, result) -> None:
        self.prompt_tokens = self.prompt_tokens + result.prompt_tokens
        self.completion_tokens = self.completion_tokens + result.completion_tokens
        self.cost_usd = self.cost_usd + result.cost_usd
        self.model_calls = self.model_calls + 1

        if stage not in self.by_stage:
            self.by_stage[stage] = {"calls": 0, "tokens": 0, "cost_usd": 0.0}

        entry = self.by_stage[stage]
        entry["calls"] = entry["calls"] + 1
        entry["tokens"] = entry["tokens"] + result.total_tokens()
        entry["cost_usd"] = round(entry["cost_usd"] + result.cost_usd, 8)

    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_report(self, latency_ms: int = 0):
        from sql_copilot.layer2_models.schemas import UsageReport

        return UsageReport(
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens(),
            estimated_cost_usd=round(self.cost_usd, 8),
            model_calls=self.model_calls,
            latency_ms=latency_ms,
            by_stage=dict(self.by_stage),
        )
