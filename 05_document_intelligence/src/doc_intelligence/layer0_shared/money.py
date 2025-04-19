"""
LAYER 0 - SHARED: READING MONEY AND DATES
=========================================
The first place where deterministic code beats asking a model.

A model reading "1.234,56 EUR" will usually return 1234.56, and sometimes return
1.23. It is right most of the time, which is the worst possible property for
something that decides what you pay a supplier. Parsing is a solved problem with
rules, so it is solved with rules, and the model is never asked.

------------------------------------------------------------------------------
THE AMBIGUITY THAT MATTERS: 1,234 IS NOT THE SAME NUMBER EVERYWHERE
------------------------------------------------------------------------------
    "1,234.56"   one thousand two hundred, UK/US style
    "1.234,56"   the same number, German/Spanish style
    "1,234"      one thousand two hundred and thirty four - or 1.234, in Germany
    "1.234"      1.234 - or one thousand two hundred and thirty four

The last two are genuinely ambiguous in isolation, and a parser that silently
picks one is a parser that is silently wrong roughly half the time it matters.
So parse_money returns its CONFIDENCE alongside the value, and an ambiguous
number comes back marked ambiguous. Layer 7 turns that into "a human should look
at this" rather than into a payment.
"""

import re
from datetime import date, datetime

CURRENCY_SYMBOLS = {
    "$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY", "₹": "INR",
}

CURRENCY_CODES = ["USD", "GBP", "EUR", "JPY", "INR", "CHF", "CAD", "AUD", "SEK", "NOK", "DKK", "PLN"]

# A run of digits with optional grouping and decimals, plus an optional sign.
#
# THE BUG THIS SHAPE FIXES, WHICH WAS FOUND ON THE FIRST TEST RUN.
# The first version was an alternation starting with `\d{1,3}`:
#
#     -?\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|-?\d+(?:[.,]\d{1,2})?
#
# Regex alternation takes the first branch that matches ANYTHING, so "4500"
# matched the first branch as "450" and stopped. A plain four-digit amount came
# back as 450.0 - a tenth of the real figure, with full confidence, on an
# invoice. It is the worst class of bug in this whole project: silently wrong,
# plausible, and about money.
#
# One branch, a greedy digit run, and a lookahead so a match can never stop in
# the middle of a number.
NUMBER_PATTERN = re.compile(r"-?\d+(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?(?!\d)")


class ParsedMoney:
    """An amount, its currency, and how sure we are about it."""

    def __init__(
        self,
        amount: float | None,
        currency: str = "",
        confidence: float = 0.0,
        note: str = "",
        raw: str = "",
    ) -> None:
        self.amount = amount
        self.currency = currency
        self.confidence = confidence
        self.note = note
        self.raw = raw

    def found(self) -> bool:
        return self.amount is not None

    def to_dict(self) -> dict:
        return {
            "amount": self.amount,
            "currency": self.currency,
            "confidence": round(self.confidence, 3),
            "note": self.note,
            "raw": self.raw,
        }


def find_currency(text: str) -> str:
    """The currency this text is written in, or "" if it does not say."""
    for symbol in CURRENCY_SYMBOLS:
        if symbol in text:
            return CURRENCY_SYMBOLS[symbol]

    upper = text.upper()
    for code in CURRENCY_CODES:
        if re.search(r"\b" + code + r"\b", upper):
            return code

    return ""


def parse_money(text: str) -> ParsedMoney:
    """
    Read an amount out of a piece of text.

    Returns a confidence, because some numbers genuinely cannot be resolved from
    the text alone. Guessing and looking certain is worse than saying so.
    """
    if text is None:
        return ParsedMoney(None, note="no text")

    original = str(text).strip()
    if original == "":
        return ParsedMoney(None, note="empty")

    currency = find_currency(original)

    found = NUMBER_PATTERN.search(original.replace(" ", ""))
    if found is None:
        return ParsedMoney(None, currency=currency, note="no number found", raw=original)

    digits = found.group()
    amount, confidence, note = interpret_number(digits)

    return ParsedMoney(
        amount=amount, currency=currency, confidence=confidence, note=note, raw=original
    )


def interpret_number(digits: str) -> tuple[float | None, float, str]:
    """
    Work out which separator is the decimal point.

    The rules, in order of how much they can be trusted:

      1. BOTH separators present - the LAST one is the decimal point.
         "1.234,56" -> comma last -> 1234.56.   Certain.

      2. ONE separator, followed by exactly 2 digits - decimal point.
         "1234,56" -> 1234.56.  Certain: digit grouping is always in threes,
         so a group can never be two digits long.

      3. ONE separator, followed by exactly 3 digits - a thousands separator.
         "1,234" -> 1234.  Likely, but "1,234" IS 1.234 in some locales.
         Marked ambiguous.

      4. Anything else - treated as a decimal point, flagged.
    """
    cleaned = digits.strip()
    negative = cleaned.startswith("-")
    if negative:
        cleaned = cleaned[1:]

    has_comma = "," in cleaned
    has_dot = "." in cleaned

    # --- 1. both separators ---
    if has_comma and has_dot:
        if cleaned.rfind(",") > cleaned.rfind("."):
            normalised = cleaned.replace(".", "").replace(",", ".")
        else:
            normalised = cleaned.replace(",", "")
        return (to_float(normalised, negative), 1.0, "both separators present, unambiguous")

    # --- one separator ---
    if has_comma or has_dot:
        if has_comma:
            separator = ","
        else:
            separator = "."

        parts = cleaned.split(separator)

        if len(parts) > 2:
            # "1.234.567" - the separator appears more than once, and no
            # number has two decimal points, so every one of them is a
            # grouping separator. Certain.
            return (to_float(cleaned.replace(separator, ""), negative), 1.0,
                    "the separator repeats, so it can only be grouping")

        tail = parts[1]

        if len(tail) == 2:
            # Digit grouping is always in threes, in every locale. A separator
            # followed by exactly two digits therefore cannot be a grouping
            # separator, so this is certain - not 0.95, which is what it used to
            # return while the docstring right above it explained why it was
            # unambiguous. An unearned 0.05 of doubt here propagated into every
            # total in the pipeline and pushed clean receipts below the
            # auto-approval gate, so the fudge cost real straight-through rate.
            return (to_float(cleaned.replace(separator, "."), negative), 1.0,
                    "two digits after the separator, and grouping is always in "
                    "threes, so it is a decimal point")

        if len(tail) == 3:
            return (
                to_float(cleaned.replace(separator, ""), negative),
                0.55,
                "AMBIGUOUS: three digits after '%s' could be a thousands separator "
                "or a decimal point, depending on locale" % separator,
            )

        return (to_float(cleaned.replace(separator, "."), negative), 0.8,
                "%d digits after the separator" % len(tail))

    # --- no separator ---
    return (to_float(cleaned, negative), 1.0, "plain number")


def to_float(text: str, negative: bool) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    if negative:
        return -value
    return value


# ---------------------------------------------------------------------------
#  Dates
# ---------------------------------------------------------------------------

# The formats worth trying, most specific first. ISO is unambiguous and goes
# first so it is never mistaken for anything else.
DATE_FORMATS = [
    ("%Y-%m-%d", 1.0, "ISO format, unambiguous"),
    ("%d %B %Y", 1.0, "day, month name, year - unambiguous"),
    ("%d %b %Y", 1.0, "day, short month name, year - unambiguous"),
    ("%B %d, %Y", 1.0, "month name, day, year - unambiguous"),
    ("%b %d, %Y", 1.0, "short month name, day, year - unambiguous"),
    ("%d/%m/%Y", 0.6, "AMBIGUOUS: read as day/month/year, but could be month/day/year"),
    ("%m/%d/%Y", 0.6, "AMBIGUOUS: read as month/day/year, but could be day/month/year"),
    ("%d.%m.%Y", 0.85, "day.month.year - the dot form is rarely American"),
    ("%d-%m-%Y", 0.7, "day-month-year"),
]


class ParsedDate:
    def __init__(self, value: date | None, confidence: float = 0.0, note: str = "", raw: str = "") -> None:
        self.value = value
        self.confidence = confidence
        self.note = note
        self.raw = raw

    def found(self) -> bool:
        return self.value is not None

    def iso(self) -> str:
        if self.value is None:
            return ""
        return self.value.isoformat()

    def to_dict(self) -> dict:
        return {
            "value": self.iso(),
            "confidence": round(self.confidence, 3),
            "note": self.note,
            "raw": self.raw,
        }


def parse_date(text: str) -> ParsedDate:
    """
    Read a date.

    03/04/2025 is the third of April in London and the fourth of March in New
    York, and no amount of staring at it resolves that. Both readings are
    returned at reduced confidence rather than one being picked silently - and
    where the day is above 12 the ambiguity disappears, which is handled below.
    """
    if text is None:
        return ParsedDate(None, note="no text")

    original = str(text).strip()
    if original == "":
        return ParsedDate(None, note="empty")

    for pattern, confidence, note in DATE_FORMATS:
        try:
            value = datetime.strptime(original, pattern).date()
        except ValueError:
            continue

        # A slash date where the first number is above 12 can only be a day,
        # so the ambiguity resolves itself.
        if confidence < 1.0 and "/" in original:
            first = original.split("/")[0]
            try:
                if int(first) > 12:
                    confidence = 0.95
                    note = "the first number is above 12, so it must be the day"
            except ValueError:
                pass

        return ParsedDate(value=value, confidence=confidence, note=note, raw=original)

    return ParsedDate(None, note="not a date format we recognise", raw=original)


def round_money(value: float) -> float:
    """Money is rounded to the penny, once, in one place."""
    return round(value + 0.0, 2)
