"""
LAYER 0 - SHARED: TOKEN COST
============================
Dollars per 1,000,000 tokens.

One question can cost several model calls: generating the SQL, repairing it when
it fails, and explaining the result. A repair loop is where the cost of a
question quietly doubles.
"""

CHAT_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-4o": (2.50, 10.00),
}
FALLBACK_CHAT_PRICE = (0.15, 0.60)


def chat_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    if model in CHAT_PRICES:
        input_price, output_price = CHAT_PRICES[model]
    else:
        input_price, output_price = FALLBACK_CHAT_PRICE

    return round(
        (prompt_tokens / 1_000_000.0) * input_price
        + (completion_tokens / 1_000_000.0) * output_price,
        8,
    )


def rough_token_count(text: str) -> int:
    if text == "":
        return 0
    return max(1, len(text) // 4)
