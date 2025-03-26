"""
LAYER 0 - SHARED: FINDING AND REMOVING PERSONAL DATA
====================================================
Customers type their card number into a chat box. They do it constantly. The
moment they do, that number is on its way into your log files, your error
tracker, your analytics, and your model provider's servers.

So the rule is: redact at the boundary, every time, before anything is written
down or sent anywhere.

TWO THINGS THAT MAKE THIS HARDER THAN IT LOOKS

  1. FALSE POSITIVES ARE EXPENSIVE.
     An order number like 4532015112830366 is sixteen digits. So is a card
     number. If you redact every sixteen-digit string, the agent can no longer
     see order numbers and stops working. We run the Luhn checksum, which real
     card numbers pass and arbitrary numbers almost never do.

  2. WHAT YOU KEEP MATTERS AS MUCH AS WHAT YOU REMOVE.
     "[CARD_REDACTED]" is safe but useless to a support agent. "[CARD_ENDING_0366]"
     is equally safe and still lets a human match it to a payment record. Redaction
     is not deletion; it is keeping the least information that still does the job.
"""

import re

# --- patterns ---------------------------------------------------------------

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Long digit runs, possibly separated by spaces or dashes. Checked with Luhn.
CARD_PATTERN = re.compile(r"\b(?:\d[ -]?){12,19}\b")

# International and local phone shapes. Deliberately requires 9+ digits so it
# does not swallow prices, quantities or years.
PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+\d{1,3}[ -]?)?(?:\(\d{2,4}\)[ -]?)?\d[\d -]{7,}\d(?!\w)")

# UK-style and US-style postcodes.
POSTCODE_PATTERN = re.compile(
    r"\b(?:[A-Z]{1,2}\d[A-Z\d]?[ ]?\d[A-Z]{2}|\d{5}(?:-\d{4})?)\b", re.IGNORECASE
)

IBAN_PATTERN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")

# Things that LOOK like personal data but are ours, so must never be redacted.
# Without this, the agent loses the ability to read its own identifiers.
PROTECTED_PATTERNS = [
    re.compile(r"\bORD-\d+\b", re.IGNORECASE),
    re.compile(r"\bCUST-\d+\b", re.IGNORECASE),
    re.compile(r"\bREF-\d+\b", re.IGNORECASE),
    re.compile(r"\bTICK-\d+\b", re.IGNORECASE),
    re.compile(r"\b1Z[A-Z0-9]{10,}\b"),          # tracking numbers
]


class PiiMatch:
    """One piece of personal data found in some text."""

    def __init__(self, kind: str, start: int, end: int, original: str, replacement: str) -> None:
        self.kind = kind
        self.start = start
        self.end = end
        self.original = original
        self.replacement = replacement


def passes_luhn_check(digits: str) -> bool:
    """
    The checksum every real payment card satisfies.

    Double every second digit from the right; if doubling gives more than 9,
    subtract 9. The total must divide by 10. An arbitrary sixteen-digit number
    passes about one time in ten, which is a far better filter than none.
    """
    if len(digits) < 12 or len(digits) > 19:
        return False

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

    return total % 10 == 0


def find_protected_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges holding our own identifiers, which must survive."""
    spans: list[tuple[int, int]] = []
    for pattern in PROTECTED_PATTERNS:
        for found in pattern.finditer(text):
            spans.append((found.start(), found.end()))
    return spans


def overlaps_any(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    for span_start, span_end in spans:
        if start < span_end and end > span_start:
            return True
    return False


def find_pii(text: str) -> list[PiiMatch]:
    """
    Every piece of personal data in the text.

    Order matters. Cards are checked before phone numbers, because a card number
    also looks like a long run of digits, and whichever runs first wins.
    """
    if text == "":
        return []

    protected = find_protected_spans(text)
    matches: list[PiiMatch] = []
    claimed: list[tuple[int, int]] = list(protected)

    # --- emails ---
    for found in EMAIL_PATTERN.finditer(text):
        if overlaps_any(found.start(), found.end(), claimed):
            continue
        matches.append(PiiMatch("email", found.start(), found.end(), found.group(), "[EMAIL_REDACTED]"))
        claimed.append((found.start(), found.end()))

    # --- payment cards (Luhn-checked) ---
    for found in CARD_PATTERN.finditer(text):
        if overlaps_any(found.start(), found.end(), claimed):
            continue

        digits = ""
        for character in found.group():
            if character.isdigit():
                digits = digits + character

        if not passes_luhn_check(digits):
            continue

        # Keep the last four: safe to store, and still useful to a human.
        replacement = "[CARD_ENDING_" + digits[-4:] + "]"
        matches.append(PiiMatch("card", found.start(), found.end(), found.group(), replacement))
        claimed.append((found.start(), found.end()))

    # --- IBANs ---
    for found in IBAN_PATTERN.finditer(text):
        if overlaps_any(found.start(), found.end(), claimed):
            continue
        matches.append(PiiMatch("iban", found.start(), found.end(), found.group(), "[IBAN_REDACTED]"))
        claimed.append((found.start(), found.end()))

    # --- phone numbers ---
    for found in PHONE_PATTERN.finditer(text):
        if overlaps_any(found.start(), found.end(), claimed):
            continue

        digit_count = 0
        for character in found.group():
            if character.isdigit():
                digit_count = digit_count + 1
        if digit_count < 9:
            continue

        matches.append(PiiMatch("phone", found.start(), found.end(), found.group(), "[PHONE_REDACTED]"))
        claimed.append((found.start(), found.end()))

    # --- postcodes ---
    for found in POSTCODE_PATTERN.finditer(text):
        if overlaps_any(found.start(), found.end(), claimed):
            continue
        matches.append(PiiMatch("postcode", found.start(), found.end(), found.group(), "[POSTCODE_REDACTED]"))
        claimed.append((found.start(), found.end()))

    matches.sort(key=lambda match: match.start)
    return matches


def redact(text: str) -> tuple[str, list[str]]:
    """
    Replace every piece of personal data.

    Returns the cleaned text and the kinds that were found, so the API can tell
    the user "we removed an email address and a card number" without repeating
    the values back.
    """
    matches = find_pii(text)
    if len(matches) == 0:
        return (text, [])

    # Rebuild from the end so earlier positions stay valid.
    result = text
    position = len(matches) - 1
    while position >= 0:
        match = matches[position]
        result = result[: match.start] + match.replacement + result[match.end :]
        position = position - 1

    kinds: list[str] = []
    for match in matches:
        if match.kind not in kinds:
            kinds.append(match.kind)

    return (result, kinds)


def contains_pii(text: str) -> bool:
    """A quick yes/no, for tests and for guard checks."""
    return len(find_pii(text)) > 0
