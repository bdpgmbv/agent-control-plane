"""Discounts, tax and the totals that come out of them."""

from shopkit.money import round_money


class DiscountTier:
    """Spend at least `threshold` and this percentage comes off."""

    def __init__(self, threshold, percentage):
        self.threshold = threshold
        self.percentage = percentage

    def __repr__(self):
        return "DiscountTier(%.2f, %g%%)" % (self.threshold, self.percentage)


# Ordered cheapest threshold first. The highest tier the order qualifies for wins.
DEFAULT_TIERS = [
    DiscountTier(50.0, 5.0),
    DiscountTier(100.0, 10.0),
    DiscountTier(250.0, 15.0),
    DiscountTier(500.0, 20.0),
]


def tiered_discount(subtotal, tiers=None):
    """
    The discount earned by spending `subtotal`.

    Every tier is considered, including the last one. Walking the list with
    range(len(tiers) - 1) would quietly drop the top tier, so the biggest
    spenders would get the second-best rate.
    """
    if tiers is None:
        tiers = DEFAULT_TIERS
    if subtotal is None or subtotal <= 0:
        return 0.0

    best_percentage = 0.0
    for tier in tiers:
        if subtotal >= tier.threshold:
            if tier.percentage > best_percentage:
                best_percentage = tier.percentage

    return round_money(subtotal * best_percentage / 100.0)


def apply_coupon(subtotal, coupon=None):
    """
    Take a coupon off a subtotal.

    A missing coupon is normal - most orders do not have one - so None returns
    the subtotal unchanged instead of raising.
    """
    if subtotal is None:
        return 0.0
    if coupon is None:
        return round_money(subtotal)

    code = str(coupon).strip().upper()
    known = {"SAVE5": 5.0, "SAVE10": 10.0, "HALFOFF": 0.0}

    if code == "HALFOFF":
        return round_money(subtotal / 2.0)
    if code not in known:
        return round_money(subtotal)

    reduced = subtotal - known[code]
    if reduced < 0:
        return 0.0
    return round_money(reduced)


def total_with_tax(subtotal, tax_rate=20.0):
    """
    Add tax to a subtotal.

    Tax is ADDED. This is the whole function and it is still worth a test,
    because a stray minus sign here undercharges every order in the system.
    """
    if subtotal is None:
        return 0.0
    tax = round_money(subtotal * tax_rate / 100.0)
    return round_money(subtotal + tax)


def average_item_price(cart):
    """
    The mean price per physical item.

    An empty cart has no average, and returning 0.0 is more useful to callers
    than a ZeroDivisionError from deep inside a pricing call.
    """
    if cart is None:
        return 0.0

    count = cart.count()
    if count == 0:
        return 0.0

    return round_money(cart.subtotal() / count)
