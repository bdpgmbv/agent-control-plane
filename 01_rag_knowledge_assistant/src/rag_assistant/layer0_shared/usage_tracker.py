"""
LAYER 0 - SHARED: COUNTING EVERY MODEL CALL
===========================================
A question does not cost one model call. In live mode it costs up to four:

    1. embedding the query variants
    2. query rewriting          (a model call)
    3. reranking the shortlist  (a model call)
    4. writing the answer       (a model call)

The first version of this project reported only the last one. A question that
was refused showed "0 tokens, $0.000000" while having taken 3.6 seconds and made
two real API calls. The bill said otherwise.

That is a worse failure than having no cost tracking at all, because you trust
the number and plan around it. So every call now reports into one of these, and
the total is what the API returns.
"""


class UsageAccumulator:
    """Adds up tokens and dollars across every call made for one request."""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.embedding_tokens = 0
        self.cost_usd = 0.0
        # What each stage cost, so you can see where the money actually goes.
        self.by_stage: dict[str, dict] = {}

    def add_model_call(self, stage: str, prompt_tokens: int, completion_tokens: int, cost_usd: float) -> None:
        """Record one chat completion."""
        self.prompt_tokens = self.prompt_tokens + prompt_tokens
        self.completion_tokens = self.completion_tokens + completion_tokens
        self.cost_usd = self.cost_usd + cost_usd
        self.record_stage(stage, prompt_tokens + completion_tokens, cost_usd)

    def add_embedding_call(self, stage: str, tokens: int, cost_usd: float) -> None:
        """Record one embedding call."""
        self.embedding_tokens = self.embedding_tokens + tokens
        self.cost_usd = self.cost_usd + cost_usd
        self.record_stage(stage, tokens, cost_usd)

    def record_stage(self, stage: str, tokens: int, cost_usd: float) -> None:
        if stage not in self.by_stage:
            self.by_stage[stage] = {"calls": 0, "tokens": 0, "cost_usd": 0.0}

        entry = self.by_stage[stage]
        entry["calls"] = entry["calls"] + 1
        entry["tokens"] = entry["tokens"] + tokens
        entry["cost_usd"] = round(entry["cost_usd"] + cost_usd, 8)

    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens + self.embedding_tokens

    def merge(self, other: "UsageAccumulator") -> None:
        """Fold another accumulator into this one."""
        self.prompt_tokens = self.prompt_tokens + other.prompt_tokens
        self.completion_tokens = self.completion_tokens + other.completion_tokens
        self.embedding_tokens = self.embedding_tokens + other.embedding_tokens
        self.cost_usd = self.cost_usd + other.cost_usd

        for stage in other.by_stage:
            if stage not in self.by_stage:
                self.by_stage[stage] = {"calls": 0, "tokens": 0, "cost_usd": 0.0}
            mine = self.by_stage[stage]
            theirs = other.by_stage[stage]
            mine["calls"] = mine["calls"] + theirs["calls"]
            mine["tokens"] = mine["tokens"] + theirs["tokens"]
            mine["cost_usd"] = round(mine["cost_usd"] + theirs["cost_usd"], 8)
