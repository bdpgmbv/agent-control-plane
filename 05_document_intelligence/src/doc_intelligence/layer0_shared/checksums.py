"""
LAYER 0 - SHARED: THINGS YOU CAN CHECK WITHOUT ASKING ANYONE
============================================================
An IBAN carries its own checksum. So does a VAT number, a card number, an EAN
barcode. Given one, you can prove it is wrong with arithmetic - no model, no
network, no database lookup, no doubt.

This is the sharpest example of the idea running through this whole project.
Asked "is GB82 WEST 1234 5698 7654 32 a valid IBAN?", a model will give you an
opinion. Mod-97 gives you an answer.

------------------------------------------------------------------------------
WHAT A FAILED CHECKSUM ACTUALLY TELLS YOU
------------------------------------------------------------------------------
It does NOT mean somebody is committing fraud. On a scanned invoice, by far the
most likely cause is OCR: a 0 read as an O, a 1 as an l, an 8 as a B. So a failed
checksum routes the document to a human rather than rejecting it - and it is a
strong signal precisely because it almost never fires by accident. A random
sixteen-character string passes mod-97 about once in ninety-seven attempts.
"""

import re

# Each country's IBAN is a fixed length. A length that is wrong for the country
# is a faster and more certain rejection than the checksum.
IBAN_LENGTHS = {
    "AT": 20, "BE": 16, "CH": 21, "CZ": 24, "DE": 22, "DK": 18, "EE": 20,
    "ES": 24, "FI": 18, "FR": 27, "GB": 22, "GR": 27, "HU": 28, "IE": 22,
    "IT": 27, "LU": 20, "NL": 18, "NO": 15, "PL": 28, "PT": 25, "RO": 24,
    "SE": 24, "SI": 19, "SK": 24,
}

# VAT number shapes, by country. Format only - a well-formed number can still
# belong to nobody, which needs a lookup this project does not do.
VAT_PATTERNS = {
    "GB": re.compile(r"^GB(\d{9}|\d{12}|GD\d{3}|HA\d{3})$"),
    "DE": re.compile(r"^DE\d{9}$"),
    "FR": re.compile(r"^FR[A-Z0-9]{2}\d{9}$"),
    "ES": re.compile(r"^ES[A-Z0-9]\d{7}[A-Z0-9]$"),
    "IT": re.compile(r"^IT\d{11}$"),
    "NL": re.compile(r"^NL\d{9}B\d{2}$"),
    "IE": re.compile(r"^IE\d{7}[A-Z]{1,2}$"),
    "PL": re.compile(r"^PL\d{10}$"),
    "SE": re.compile(r"^SE\d{12}$"),
    "BE": re.compile(r"^BE0\d{9}$"),
}


class CheckResult:
    def __init__(self, valid: bool, reason: str = "", normalised: str = "") -> None:
        self.valid = valid
        self.reason = reason
        self.normalised = normalised


def strip_spacing(text: str) -> str:
    cleaned = ""
    for character in str(text):
        if character.isalnum():
            cleaned = cleaned + character
    return cleaned.upper()


def check_iban(text: str) -> CheckResult:
    """
    The mod-97 check, which every real IBAN satisfies.

    Move the first four characters to the end, turn every letter into two digits
    (A=10 ... Z=35), and the resulting number must leave a remainder of 1 when
    divided by 97. That is the whole algorithm, and it catches every single-digit
    error and almost every transposition.
    """
    cleaned = strip_spacing(text)

    if len(cleaned) < 15 or len(cleaned) > 34:
        return CheckResult(False, "an IBAN is between 15 and 34 characters; this is %d" % len(cleaned), cleaned)

    country = cleaned[:2]
    if not country.isalpha():
        return CheckResult(False, "an IBAN starts with a two-letter country code", cleaned)

    if country in IBAN_LENGTHS and len(cleaned) != IBAN_LENGTHS[country]:
        return CheckResult(
            False,
            "a %s IBAN is %d characters; this one is %d"
            % (country, IBAN_LENGTHS[country], len(cleaned)),
            cleaned,
        )

    rearranged = cleaned[4:] + cleaned[:4]

    numeric = ""
    for character in rearranged:
        if character.isdigit():
            numeric = numeric + character
        elif character.isalpha():
            numeric = numeric + str(ord(character) - ord("A") + 10)
        else:
            return CheckResult(False, "an IBAN contains only letters and digits", cleaned)

    try:
        remainder = int(numeric) % 97
    except ValueError:
        return CheckResult(False, "could not read the IBAN as a number", cleaned)

    if remainder == 1:
        return CheckResult(True, "the mod-97 checksum is correct", cleaned)

    # States the arithmetic fact and stops there. What a failed checksum MEANS -
    # probably a misread digit, have someone check the paper - is a judgement
    # about the workflow, and it belongs to layer 6, which knows whether this
    # document came off a scanner. Saying it here too produced messages that
    # gave the same advice twice in a row.
    return CheckResult(
        False,
        "the mod-97 checksum does not match (remainder %d, expected 1)" % remainder,
        cleaned,
    )


def check_vat_number(text: str) -> CheckResult:
    """
    Is this the right SHAPE for a VAT number in its country?

    Format only. A well-formed number can still belong to nobody, and finding
    that out needs a lookup against a tax authority - which is exactly the kind
    of thing that belongs behind a tool call rather than in a checksum file.
    """
    cleaned = strip_spacing(text)

    if len(cleaned) < 4:
        return CheckResult(False, "too short to be a VAT number", cleaned)

    country = cleaned[:2]
    if country not in VAT_PATTERNS:
        return CheckResult(
            True,
            "no format is known for country '%s', so the shape was not checked" % country,
            cleaned,
        )

    if VAT_PATTERNS[country].match(cleaned) is not None:
        return CheckResult(True, "matches the %s VAT number format" % country, cleaned)

    return CheckResult(False, "does not match the %s VAT number format" % country, cleaned)


def check_luhn(text: str) -> CheckResult:
    """
    The checksum on card numbers, and on a good many account identifiers.

    Same idea as project 02's PII detection, used for the opposite purpose: there
    it decided what to hide, here it decides what to trust.
    """
    digits = ""
    for character in str(text):
        if character.isdigit():
            digits = digits + character

    if len(digits) < 12:
        return CheckResult(False, "too short for a Luhn-checked number", digits)

    total = 0
    should_double = False
    position = len(digits) - 1

    while position >= 0:
        digit = int(digits[position])
        if should_double:
            digit = digit * 2
            if digit > 9:
                digit = digit - 9
        total = total + digit
        should_double = not should_double
        position = position - 1

    if total % 10 == 0:
        return CheckResult(True, "the Luhn checksum is correct", digits)
    return CheckResult(False, "the Luhn checksum does not match", digits)
