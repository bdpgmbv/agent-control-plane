"""
LAYER 0 - SHARED: TOKEN COST
============================
Dollars per 1,000,000 tokens. Update when the provider changes its prices.

An agent costs more per question than a plain chatbot, because one customer
message can mean several model calls: routing, then a tool-calling turn for each
tool, then the final answer. Counting only the last one understates the bill by
several times - the mistake project 01 made and fixed.
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

    input_cost = (prompt_tokens / 1_000_000.0) * input_price
    output_cost = (completion_tokens / 1_000_000.0) * output_price
    return round(input_cost + output_cost, 8)


def rough_token_count(text: str) -> int:
    """Roughly four characters per token. Used when no usage is reported."""
    if text == "":
        return 0
    return max(1, len(text) // 4)
