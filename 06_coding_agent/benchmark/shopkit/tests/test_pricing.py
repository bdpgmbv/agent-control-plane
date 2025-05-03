from shopkit.cart import Cart
from shopkit.pricing import (
    DiscountTier,
    apply_coupon,
    average_item_price,
    tiered_discount,
    total_with_tax,
)


def test_tiered_discount_picks_the_right_band():
    assert tiered_discount(40.0) == 0.0
    assert tiered_discount(50.0) == 2.5
    assert tiered_discount(120.0) == 12.0
    assert tiered_discount(300.0) == 45.0


def test_the_highest_tier_is_not_skipped():
    # Walking the tiers with range(len(tiers) - 1) drops the last one, so the
    # biggest orders quietly get the second-best rate. 600 must earn 20%, not 15%.
    assert tiered_discount(600.0) == 120.0
    assert tiered_discount(1000.0) == 200.0


def test_a_single_tier_list_still_applies():
    tiers = [DiscountTier(10.0, 50.0)]
    assert tiered_discount(20.0, tiers) == 10.0


def test_discount_on_nothing():
    assert tiered_discount(0.0) == 0.0
    assert tiered_discount(None) == 0.0
    assert tiered_discount(-5.0) == 0.0


def test_tax_is_added_not_subtracted():
    assert total_with_tax(100.0) == 120.0
    assert total_with_tax(19.99) == 23.99
    assert total_with_tax(100.0, 0.0) == 100.0


def test_coupons():
    assert apply_coupon(50.0, "SAVE5") == 45.0
    assert apply_coupon(50.0, "save10") == 40.0
    assert apply_coupon(50.0, "HALFOFF") == 25.0
    assert apply_coupon(50.0, "NOTACODE") == 50.0


def test_a_missing_coupon_is_normal():
    # Most orders have no coupon. This must not raise.
    assert apply_coupon(50.0) == 50.0
    assert apply_coupon(50.0, None) == 50.0


def test_a_coupon_cannot_make_the_total_negative():
    assert apply_coupon(3.0, "SAVE5") == 0.0


def test_average_item_price():
    cart = Cart()
    cart.add("A", "Mug", 10.00, 2)
    cart.add("B", "Plate", 20.00, 2)
    assert average_item_price(cart) == 15.0


def test_average_of_an_empty_cart_is_zero_not_a_crash():
    assert average_item_price(Cart()) == 0.0
    assert average_item_price(None) == 0.0
