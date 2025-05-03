import pytest

from shopkit.inventory import Inventory, StockError


def test_stock_levels():
    inventory = Inventory({"A1": 10, "B2": 0})
    assert inventory.stock_of("A1") == 10
    assert inventory.stock_of("B2") == 0
    assert inventory.stock_of("NOPE") == 0


def test_exactly_enough_stock_is_enough():
    # Asking for all ten of the ten on the shelf is a valid order.
    inventory = Inventory({"A1": 10})
    assert inventory.in_stock("A1", 10)
    assert inventory.in_stock("A1", 9)
    assert not inventory.in_stock("A1", 11)


def test_zero_quantity_is_always_available():
    inventory = Inventory({"A1": 0})
    assert inventory.in_stock("A1", 0)


def test_reserving_reduces_stock():
    inventory = Inventory({"A1": 10})
    assert inventory.reserve("A1", 4) == 6
    assert inventory.stock_of("A1") == 6


def test_reserving_too_much_raises():
    inventory = Inventory({"A1": 2})
    with pytest.raises(StockError):
        inventory.reserve("A1", 3)


def test_restocking():
    inventory = Inventory()
    assert inventory.restock("NEW", 5) == 5
    assert inventory.restock("NEW", 5) == 10


def test_low_stock_report():
    inventory = Inventory({"A1": 2, "B2": 50, "C3": 5})
    assert inventory.low_stock() == ["A1", "C3"]
    assert inventory.low_stock(1) == []


def test_two_inventories_do_not_share_levels():
    first = Inventory()
    first.restock("A1", 5)
    second = Inventory()
    assert second.stock_of("A1") == 0
