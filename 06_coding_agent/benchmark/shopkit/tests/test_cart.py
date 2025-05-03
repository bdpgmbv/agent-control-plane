from shopkit.cart import Cart, CartItem


def test_add_and_subtotal():
    cart = Cart()
    cart.add("A1", "Mug", 7.50, 2)
    cart.add("B2", "Plate", 12.00, 1)
    assert cart.subtotal() == 27.0
    assert cart.count() == 3


def test_adding_the_same_sku_merges_the_line():
    cart = Cart()
    cart.add("A1", "Mug", 7.50, 2)
    cart.add("A1", "Mug", 7.50, 3)
    assert len(cart.items) == 1
    assert cart.count() == 5


def test_two_carts_do_not_share_items():
    # A mutable default argument would make every cart share one list, so the
    # second cart would start with the first cart's items already in it.
    first = Cart()
    first.add("A1", "Mug", 7.50)

    second = Cart()
    assert second.is_empty()
    assert second.count() == 0


def test_removing():
    cart = Cart()
    cart.add("A1", "Mug", 7.50)
    cart.add("B2", "Plate", 12.00)
    assert cart.remove("A1") == 1
    assert cart.count() == 1
    assert cart.remove("ZZ") == 0


def test_items_sorted_by_price_numerically():
    # Sorting on the string form puts "10.00" before "9.00".
    cart = Cart()
    cart.add("A", "Ten", 10.00)
    cart.add("B", "Nine", 9.00)
    cart.add("C", "Hundred", 100.00)

    ordered = cart.items_by_price()
    prices = []
    for item in ordered:
        prices.append(item.unit_price)
    assert prices == [9.00, 10.00, 100.00]


def test_line_total():
    item = CartItem("A1", "Mug", 7.505, 3)
    assert item.unit_price == 7.51
    assert item.line_total() == 22.53


def test_empty_cart():
    cart = Cart()
    assert cart.is_empty()
    assert cart.subtotal() == 0.0
    assert cart.count() == 0
