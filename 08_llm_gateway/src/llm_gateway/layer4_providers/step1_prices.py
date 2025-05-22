"""
LAYER 4, STEP 1 - WHAT THINGS COST
==================================
Prices per MILLION tokens, because per-thousand puts four leading zeros on every
number in the interface and people stop reading them.

These are a snapshot and they go out of date. That is why they live in one table
rather than being scattered through the code: when a price changes, one line
changes, and every cost figure the gateway has ever reported stays traceable to
the rate that produced it.

The offline models are priced too, at zero. A cost of nothing is still a cost,
and reporting it as such keeps the arithmetic identical in both modes - rather
than having a branch that skips costing, which is how a live figure ends up
silently missing the cheap calls.
"""

from llm_gateway.layer2_models.schemas import ModelPrice

PRICES = {
    # --- real ---
    "gpt-4o-mini": ModelPrice(
        name="gpt-4o-mini", provider="openai",
        usd_per_million_input=0.15, usd_per_million_output=0.60),
    "gpt-4o": ModelPrice(
        name="gpt-4o", provider="openai",
        usd_per_million_input=2.50, usd_per_million_output=10.00),

    # --- deterministic stand-ins, so every path works with no key ---
    "offline-fast": ModelPrice(
        name="offline-fast", provider="offline",
        usd_per_million_input=0.0, usd_per_million_output=0.0),
    "offline-strong": ModelPrice(
        name="offline-strong", provider="offline",
        usd_per_million_input=0.0, usd_per_million_output=0.0),
}


def price_for(model: str) -> ModelPrice:
    """
    The price of a model, or a loud placeholder.

    An unknown model costs zero, which would quietly under-report the bill. The
    placeholder keeps the name so it shows up in the by-model breakdown as
    something nobody priced, rather than as free.
    """
    known = PRICES.get(model)
    if known is not None:
        return known
    return ModelPrice(name=model + " (no price on file)", provider="unknown",
                      usd_per_million_input=0.0, usd_per_million_output=0.0)


def is_priced(model: str) -> bool:
    return model in PRICES
