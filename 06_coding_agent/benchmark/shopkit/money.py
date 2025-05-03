"""Money handling. Every amount in this package is a float rounded to pennies."""


def round_money(value):
    """
    Round to two decimal places, half away from zero.

    Python's built-in round() uses banker's rounding, so round(2.675, 2) gives
    2.67 and round(0.5) gives 0. For money people expect 2.68 and 1, so this
    does it explicitly rather than surprising anybody.
    """
    if value is None:
        return 0.0

    scaled = value * 100.0
    if scaled >= 0:
        rounded = int(scaled + 0.5)
    else:
        rounded = int(scaled - 0.5)
    return rounded / 100.0


def format_money(value, currency="GBP"):
    """Turn an amount into something a person reads."""
    symbols = {"GBP": "£", "USD": "$", "EUR": "€"}
    symbol = symbols.get(currency, "")
    amount = round_money(value)

    if amount < 0:
        return "-%s%.2f" % (symbol, abs(amount))
    return "%s%.2f" % (symbol, amount)


def parse_money(text):
    """
    Read an amount out of a string such as "12.50" or "£1,234.56".

    Returns None when the text holds no number, rather than raising - callers
    decide what a missing price means.
    """
    if text is None:
        return None

    cleaned = ""
    for character in str(text):
        if character.isdigit() or character in ".-":
            cleaned = cleaned + character

    if cleaned in ("", "-", ".", "-."):
        return None

    try:
        return round_money(float(cleaned))
    except ValueError:
        return None
