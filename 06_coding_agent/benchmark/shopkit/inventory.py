"""Stock levels."""


class StockError(Exception):
    """Raised when an order asks for more than there is."""


class Inventory:
    def __init__(self, levels=None):
        if levels is None:
            levels = {}
        self.levels = dict(levels)

    def stock_of(self, sku):
        return self.levels.get(sku, 0)

    def in_stock(self, sku, quantity=1):
        """
        Is there enough to satisfy this quantity?

        Asking for exactly what is on the shelf is fine, so this is >= and not
        >. Asking for zero is also fine.
        """
        if quantity <= 0:
            return True
        return self.stock_of(sku) >= quantity

    def reserve(self, sku, quantity=1):
        if not self.in_stock(sku, quantity):
            raise StockError(
                "cannot reserve %d of %r, only %d in stock"
                % (quantity, sku, self.stock_of(sku))
            )
        self.levels[sku] = self.stock_of(sku) - quantity
        return self.levels[sku]

    def restock(self, sku, quantity):
        self.levels[sku] = self.stock_of(sku) + quantity
        return self.levels[sku]

    def low_stock(self, threshold=5):
        """Every sku at or below the threshold, in sku order."""
        found = []
        for sku in sorted(self.levels):
            if self.levels[sku] <= threshold:
                found.append(sku)
        return found
