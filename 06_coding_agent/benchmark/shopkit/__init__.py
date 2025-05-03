"""
shopkit - a small pricing and cart library.

This package is the repository the coding agent works on. It is deliberately
ordinary: a few hundred lines of plain Python with a test suite that passes.

The evaluation works by breaking it. Each task in layer10 applies one exact
string replacement that introduces a realistic bug, which turns a specific test
red. The agent is given the failing test and the issue text, and has to make the
suite green again without touching the tests.

The tests are the specification. Every behaviour a task depends on is asserted
here first, so that "the suite is green" means something.
"""

from shopkit.cart import Cart, CartItem
from shopkit.money import round_money, format_money, parse_money
from shopkit.pricing import (
    DiscountTier,
    apply_coupon,
    average_item_price,
    tiered_discount,
    total_with_tax,
)
from shopkit.inventory import Inventory, StockError

__all__ = [
    "Cart", "CartItem",
    "round_money", "format_money", "parse_money",
    "DiscountTier", "apply_coupon", "average_item_price",
    "tiered_discount", "total_with_tax",
    "Inventory", "StockError",
]
