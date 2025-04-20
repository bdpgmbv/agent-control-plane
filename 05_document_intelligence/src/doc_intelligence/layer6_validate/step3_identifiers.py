"""
LAYER 6, STEP 3 - CHECKSUMS
===========================
An IBAN and a VAT number carry their own proof. Using it costs nothing.

A bank account number with a valid mod-97 checksum is almost certainly the number
that was printed. One that fails is almost certainly not - and on a scanned
document the overwhelmingly likely cause is a misread digit, not fraud.

That distinction decides the wording, not the severity. It stays an ERROR, because
paying money into an account number that does not check out is exactly the
outcome this pipeline exists to prevent. But the message says "probably a misread
digit, have someone compare it to the paper" rather than implying something
sinister, because the message is what the person in the review queue actually
reads, and sending them looking for fraud when they should be looking at the scan
wastes their time.
"""

from doc_intelligence.layer0_shared.checksums import check_iban, check_luhn, check_vat_number
from doc_intelligence.layer2_models.schemas import (
    DocumentType,
    ExtractedField,
    Severity,
    ValidationIssue,
)


def field_value(fields: list[ExtractedField], name: str) -> str:
    for field in fields:
        if field.name == name and field.is_present():
            return field.value
    return ""


def check_bank_details(fields: list[ExtractedField],
                       had_scanner_repairs: bool) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    iban = field_value(fields, "iban")
    if iban == "":
        return issues

    result = check_iban(iban)
    if result.valid:
        return issues

    likely_cause = ("This is almost always a misread digit rather than anything "
                    "sinister - have someone compare it against the paper copy.")
    if had_scanner_repairs:
        likely_cause = ("This document already needed scanner repairs, so a "
                        "misread digit is the most likely explanation. Compare "
                        "it against the paper copy.")

    issues.append(ValidationIssue(
        code="iban_checksum_failed",
        severity=Severity.ERROR,
        field="iban",
        message="the bank account number does not pass its own checksum: %s. %s"
                % (result.reason, likely_cause),
        expected="an IBAN whose mod-97 checksum is correct",
        actual=iban,
    ))
    return issues


def check_tax_registration(fields: list[ExtractedField]) -> list[ValidationIssue]:
    """
    VAT numbers get a warning, not an error.

    There is no checksum here, only a per-country shape. A shape that does not
    match is worth flagging, but formats vary, new ones appear, and this check
    knows a limited set of countries - so it is not evidence strong enough to
    stop a payment on its own. Claiming more certainty than the check has would
    make the whole queue less trustworthy.
    """
    issues: list[ValidationIssue] = []

    vat = field_value(fields, "supplier_vat")
    if vat == "":
        return issues

    result = check_vat_number(vat)
    if result.valid:
        return issues

    issues.append(ValidationIssue(
        code="vat_number_format_wrong",
        severity=Severity.WARNING,
        field="supplier_vat",
        message="the supplier's VAT number does not look right: %s" % result.reason,
        expected="a valid VAT number format for that country",
        actual=vat,
    ))
    return issues


def check_card_number(fields: list[ExtractedField]) -> list[ValidationIssue]:
    """
    If a receipt prints a full card number, the Luhn check applies.

    Most receipts print only the last four digits, which cannot be checked and
    should not be. This only fires on something long enough to be a real card
    number - and if a full number is on the page, that is worth saying out loud
    for its own reasons.
    """
    issues: list[ValidationIssue] = []

    method = field_value(fields, "payment_method")
    if method == "":
        return issues

    digits = ""
    for character in method:
        if character.isdigit():
            digits = digits + character

    if len(digits) < 13 or len(digits) > 19:
        return issues

    issues.append(ValidationIssue(
        code="full_card_number_on_document",
        severity=Severity.WARNING,
        field="payment_method",
        message="this document appears to carry a full card number, which should "
                "not be stored. Checksum %s."
                % ("passes" if check_luhn(digits).valid else "fails"),
        expected="only the last four digits",
        actual="%d digits" % len(digits),
    ))
    return issues


def run_identifier_checks(document_type: DocumentType, fields: list[ExtractedField],
                          had_scanner_repairs: bool) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    for issue in check_bank_details(fields, had_scanner_repairs):
        issues.append(issue)
    for issue in check_tax_registration(fields):
        issues.append(issue)
    if document_type == DocumentType.RECEIPT:
        for issue in check_card_number(fields):
            issues.append(issue)

    return issues
