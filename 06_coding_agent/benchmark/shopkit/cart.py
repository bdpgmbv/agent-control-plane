"""A shopping cart."""

from shopkit.money import round_money


class CartItem:
    def __init__(self, sku, name, unit_price, quantity=1):
        self.sku = sku
        self.name = name
        self.unit_price = round_money(unit_price)
        self.quantity = quantity

    def line_total(self):
        return round_money(self.unit_price * self.quantity)

    def __repr__(self):
        return "CartItem(%r, %g x %.2f)" % (self.sku, self.quantity, self.unit_price)


class Cart:
    def __init__(self, items=None):
        """
        Start a cart.

        `items` defaults to None and a fresh list is made here. Using [] as the
        default would share one list between every cart ever created.
        """
        if items is None:
            items = []
        self.items = list(items)

    def add(self, sku, name, unit_price, quantity=1):
        """Add an item, merging with an existing line for the same sku."""
        for item in self.items:
            if item.sku == sku:
                item.quantity = item.quantity + quantity
                return item

        item = CartItem(sku, name, unit_price, quantity)
        self.items.append(item)
        return item

    def remove(self, sku):
        kept = []
        for item in self.items:
            if item.sku != sku:
                kept.append(item)
        removed = len(self.items) - len(kept)
        self.items = kept
        return removed

    def count(self):
        """How many physical things are in the cart, not how many lines."""
        total = 0
        for item in self.items:
            total = total + item.quantity
        return total

    def subtotal(self):
        total = 0.0
        for item in self.items:
            total = total + item.line_total()
        return round_money(total)

    def items_by_price(self):
        """Items cheapest first. Ties keep their original order."""
        ordered = list(self.items)
        ordered.sort(key=lambda item: item.unit_price)
        return ordered

    def is_empty(self):
        return len(self.items) == 0
