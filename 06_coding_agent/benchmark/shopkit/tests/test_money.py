from shopkit.money import format_money, parse_money, round_money


def test_rounds_to_pennies():
    assert round_money(12.344) == 12.34
    assert round_money(12.346) == 12.35


def test_rounds_half_away_from_zero_not_to_even():
    # Python's round() gives 2.67 here, which is not what a till does.
    assert round_money(2.675) == 2.68
    assert round_money(0.125) == 0.13


def test_does_not_truncate():
    # int(x * 100) / 100 would give 9.99 for both of these.
    assert round_money(9.999) == 10.0
    assert round_money(0.999) == 1.0


def test_handles_negatives():
    assert round_money(-2.675) == -2.68
    assert round_money(-0.004) == 0.0


def test_none_is_zero():
    assert round_money(None) == 0.0


def test_formatting():
    assert format_money(12.5) == "£12.50"
    assert format_money(12.5, "USD") == "$12.50"
    assert format_money(-3.2) == "-£3.20"


def test_parsing():
    assert parse_money("12.50") == 12.5
    assert parse_money("£1,234.56") == 1234.56
    assert parse_money("nothing here") is None
    assert parse_money(None) is None
