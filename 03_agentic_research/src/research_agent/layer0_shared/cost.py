"""
LAYER 0 - SHARED: TOKEN COST
============================
Dollars per 1,000,000 tokens.

A research run makes many model calls: one to plan, one or two per sub-question
to extract evidence, and one to synthesise. Counting only the last one would
understate the bill by roughly the number of sub-questions.
"""

CHAT_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-4o": (2.50, 10.00),
}
FALLBACK_CHAT_PRICE = (0.15, 0.60)

EMBEDDING_PRICES: dict[str, float] = {
    "text-embedding-3-small": 0.02,
    "text-embedding-3-large": 0.13,
}
FALLBACK_EMBEDDING_PRICE = 0.02


def chat_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    if model in CHAT_PRICES:
        input_price, output_price = CHAT_PRICES[model]
    else:
        input_price, output_price = FALLBACK_CHAT_PRICE

    input_cost = (prompt_tokens / 1_000_000.0) * input_price
    output_cost = (completion_tokens / 1_000_000.0) * output_price
    return round(input_cost + output_cost, 8)


def embedding_cost_usd(model: str, tokens: int) -> float:
    price = EMBEDDING_PRICES.get(model, FALLBACK_EMBEDDING_PRICE)
    return round((tokens / 1_000_000.0) * price, 8)


def rough_token_count(text: str) -> int:
    if text == "":
        return 0
    return max(1, len(text) // 4)
