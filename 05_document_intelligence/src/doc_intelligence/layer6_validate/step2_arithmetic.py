"""
LAYER 6, STEP 2 - DOES IT ADD UP?
=================================
The part of this project that no language model should be doing.

Three sums, each checked in Python:

    quantity x unit price   ==  line total     for every row
    sum of line totals      ==  subtotal
    subtotal + tax          ==  total

A model asked "does this invoice add up?" will usually say yes, because invoices
usually do add up and fluent agreement is what it is optimised for. It has no
mechanism that forces it to actually perform the addition. Python has nothing
else.

This is the single clearest illustration of the idea the project is built
around. The model is good at finding the numbers on a messy page. Code is good at
adding them. Give each the job it cannot fail at.

Tolerance exists because printed invoices round per line. It is set to 0.02 - two
pennies - and the smallest genuine error in the sample corpus is out by 100.00,
so there is a factor of five thousand between "rounding" and "wrong". Any
tolerance in that range works, which is exactly what you want from a threshold.
"""

from doc_intelligence.layer0_shared.money import round_money
from doc_intelligence.layer2_models.schemas import (
    ExtractedField,
    LineItem,
    Severity,
    ValidationIssue,
)


def money_value(fields: list[ExtractedField], name: str) -> float | None:
    for field in fields:
        if field.name != name or not field.is_present():
            continue
        try:
            return float(field.value)
        except ValueError:
            return None
    return None


def check_line_item_maths(items: list[LineItem], tolerance: float) -> list[ValidationIssue]:
    """quantity x unit price must equal the printed line total."""
    issues: list[ValidationIssue] = []

    for position, item in enumerate(items):
        expected = item.computed_total()
        difference = abs(expected - item.line_total)
        if difference <= tolerance:
            continue

        issues.append(ValidationIssue(
            code="line_item_does_not_multiply",
            severity=Severity.ERROR,
            field="line_items[%d]" % position,
            message="line %d ('%s'): %g x %.2f is %.2f, but the line total says "
                    "%.2f - out by %.2f"
                    % (position + 1, item.description[:40], item.quantity,
                       item.unit_price, expected, item.line_total, difference),
            expected="%.2f" % expected,
            actual="%.2f" % item.line_total,
        ))

    return issues


def check_items_sum_to_subtotal(items: list[LineItem], fields: list[ExtractedField],
                                tolerance: float) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    subtotal = money_value(fields, "subtotal")
    if subtotal is None or len(items) == 0:
        return issues

    total_of_lines = 0.0
    for item in items:
        total_of_lines = total_of_lines + item.line_total
    total_of_lines = round_money(total_of_lines)

    difference = abs(total_of_lines - subtotal)
    if difference <= tolerance:
        return issues

    issues.append(ValidationIssue(
        code="line_items_do_not_sum_to_subtotal",
        severity=Severity.ERROR,
        field="subtotal",
        message="the %d line items add up to %.2f, but the subtotal printed on "
                "the document is %.2f - out by %.2f"
                % (len(items), total_of_lines, subtotal, difference),
        expected="%.2f" % total_of_lines,
        actual="%.2f" % subtotal,
    ))
    return issues


def check_subtotal_plus_tax(fields: list[ExtractedField],
                            tolerance: float) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    subtotal = money_value(fields, "subtotal")
    tax = money_value(fields, "tax_amount")
    total = money_value(fields, "total_amount")

    if total is None:
        return issues

    if subtotal is None:
        return issues

    if tax is None:
        tax = 0.0

    expected = round_money(subtotal + tax)
    difference = abs(expected - total)
    if difference <= tolerance:
        return issues

    issues.append(ValidationIssue(
        code="subtotal_plus_tax_is_not_total",
        severity=Severity.ERROR,
        field="total_amount",
        message="subtotal %.2f plus tax %.2f is %.2f, but the total printed on "
                "the document is %.2f - out by %.2f"
                % (subtotal, tax, expected, total, difference),
        expected="%.2f" % expected,
        actual="%.2f" % total,
    ))
    return issues


def check_items_sum_to_total(items: list[LineItem], fields: list[ExtractedField],
                             tolerance: float) -> list[ValidationIssue]:
    """
    The fallback when no subtotal is printed.

    Without this, a document that omits its subtotal skips every sum check and
    sails through on a total nobody added up. A gap in the checks is a hole an
    error walks through, so the checks have to cover the shapes documents
    actually come in, not just the tidy one.
    """
    issues: list[ValidationIssue] = []

    if money_value(fields, "subtotal") is not None:
        return issues

    total = money_value(fields, "total_amount")
    if total is None or len(items) == 0:
        return issues

    tax = money_value(fields, "tax_amount")
    if tax is None:
        tax = 0.0

    total_of_lines = 0.0
    for item in items:
        total_of_lines = total_of_lines + item.line_total
    expected = round_money(total_of_lines + tax)

    difference = abs(expected - total)
    if difference <= tolerance:
        return issues

    issues.append(ValidationIssue(
        code="line_items_do_not_sum_to_total",
        severity=Severity.ERROR,
        field="total_amount",
        message="no subtotal is printed, so the %d line items plus tax %.2f "
                "should equal the total: that is %.2f, but the document says "
                "%.2f - out by %.2f"
                % (len(items), tax, expected, total, difference),
        expected="%.2f" % expected,
        actual="%.2f" % total,
    ))
    return issues


# VAT and sales tax rates that actually exist. A rate outside this set is not
# proof of an error - rates change and mixed-rate invoices average out to odd
# numbers - so it is a warning, not an error. Saying "20.1% is impossible" would
# be wrong; saying "20.1% is worth a glance" is right.
KNOWN_TAX_RATES = [0.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 12.0, 15.0, 16.0,
                   17.5, 19.0, 20.0, 21.0, 22.0, 23.0, 24.0, 25.0, 27.0]
TAX_RATE_TOLERANCE = 0.35


def check_tax_rate_is_plausible(fields: list[ExtractedField]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    subtotal = money_value(fields, "subtotal")
    tax = money_value(fields, "tax_amount")
    if subtotal is None or tax is None or subtotal <= 0:
        return issues

    rate = (tax / subtotal) * 100.0

    closest = KNOWN_TAX_RATES[0]
    smallest_gap = abs(rate - closest)
    for candidate in KNOWN_TAX_RATES:
        gap = abs(rate - candidate)
        if gap < smallest_gap:
            smallest_gap = gap
            closest = candidate

    if smallest_gap <= TAX_RATE_TOLERANCE:
        return issues

    issues.append(ValidationIssue(
        code="unusual_tax_rate",
        severity=Severity.WARNING,
        field="tax_amount",
        message="tax of %.2f on a subtotal of %.2f is a rate of %.2f%%, which "
                "is not close to any standard rate (nearest is %.1f%%)"
                % (tax, subtotal, rate, closest),
        expected="a standard rate such as %.1f%%" % closest,
        actual="%.2f%%" % rate,
    ))
    return issues


def check_amounts_are_sane(fields: list[ExtractedField]) -> list[ValidationIssue]:
    """Negative or zero totals, which are legal but never routine."""
    issues: list[ValidationIssue] = []

    total = money_value(fields, "total_amount")
    if total is None:
        return issues

    if total < 0:
        issues.append(ValidationIssue(
            code="negative_total",
            severity=Severity.WARNING,
            field="total_amount",
            message="the total is negative (%.2f), which usually means this is "
                    "a credit note rather than an invoice" % total,
            expected="a positive amount",
            actual="%.2f" % total,
        ))
    elif total == 0:
        issues.append(ValidationIssue(
            code="zero_total",
            severity=Severity.WARNING,
            field="total_amount",
            message="the total is zero, which is unusual enough to be worth a look",
            expected="a non-zero amount",
            actual="0.00",
        ))

    return issues


def run_arithmetic_checks(items: list[LineItem], fields: list[ExtractedField],
                          tolerance: float, tax_tolerance: float) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    for issue in check_line_item_maths(items, tolerance):
        issues.append(issue)
    for issue in check_items_sum_to_subtotal(items, fields, tolerance):
        issues.append(issue)
    for issue in check_subtotal_plus_tax(fields, tax_tolerance):
        issues.append(issue)
    for issue in check_items_sum_to_total(items, fields, tax_tolerance):
        issues.append(issue)
    for issue in check_tax_rate_is_plausible(fields):
        issues.append(issue)
    for issue in check_amounts_are_sane(fields):
        issues.append(issue)

    return issues
