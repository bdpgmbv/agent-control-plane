"""
LAYER 0 - SHARED: TOKEN COST
============================
"How much did that answer cost?" is a production question, so we answer it on
every single request instead of guessing at the end of the month.

Prices are US dollars per 1,000,000 tokens.
Update them when the provider changes its price list.
"""

# model name -> (input price per 1M tokens, output price per 1M tokens)
CHAT_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-4o": (2.50, 10.00),
}

# model name -> price per 1M tokens
EMBEDDING_PRICES: dict[str, float] = {
    "text-embedding-3-small": 0.02,
    "text-embedding-3-large": 0.13,
}

# Used when a model name is not in the tables above, so the number is never None.
FALLBACK_CHAT_PRICE = (0.15, 0.60)
FALLBACK_EMBEDDING_PRICE = 0.02


def chat_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Cost of one chat completion call."""
    if model in CHAT_PRICES:
        input_price, output_price = CHAT_PRICES[model]
    else:
        input_price, output_price = FALLBACK_CHAT_PRICE

    input_cost = (prompt_tokens / 1_000_000.0) * input_price
    output_cost = (completion_tokens / 1_000_000.0) * output_price
    return round(input_cost + output_cost, 8)


def embedding_cost_usd(model: str, tokens: int) -> float:
    """Cost of one embedding call."""
    if model in EMBEDDING_PRICES:
        price = EMBEDDING_PRICES[model]
    else:
        price = FALLBACK_EMBEDDING_PRICE
    return round((tokens / 1_000_000.0) * price, 8)


def rough_token_count(text: str) -> int:
    """
    A cheap estimate of token count, used when the provider does not report
    usage (for example the offline provider). Roughly 4 characters per token.
    """
    if text == "":
        return 0
    return max(1, len(text) // 4)
