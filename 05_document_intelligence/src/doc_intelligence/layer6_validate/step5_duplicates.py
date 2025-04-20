"""
LAYER 6, STEP 5 - HAVE WE SEEN THIS BEFORE?
===========================================
Paying the same invoice twice is one of the most common and most expensive
failures in accounts payable, and it is entirely preventable with a lookup.

Four independent signals, from strongest to weakest:

  1. the same file          identical bytes, so an identical content hash
  2. the same reference     the same supplier and the same invoice number
  3. the same money         the same supplier, amount and date, under a
                            different reference - a re-issued invoice
  4. the page says so       the words "DUPLICATE COPY" printed on it

Signal 4 deserves a note. It is the cheapest check in this entire project - a
substring search - and on the sample corpus it catches the duplicate invoice
before any database is consulted. The lesson is not that string matching is
clever. It is that documents often state the thing you were about to build
infrastructure to infer, and reading what they say costs nothing.
"""

from dataclasses import dataclass

from doc_intelligence.layer2_models.schemas import (
    ExtractedField,
    Severity,
    ValidationIssue,
)

DUPLICATE_PHRASES = (
    "duplicate copy",
    "duplicate invoice",
    "duplicate - ",
    "copy invoice",
    "this is a duplicate",
    "do not pay twice",
    "second notice",
)


@dataclass
class SeenDocument:
    """A document already in the system, for the duplicate and bank checks."""

    document_id: str
    filename: str = ""
    reference: str = ""        # invoice or receipt number
    supplier: str = ""
    total: str = ""
    document_date: str = ""
    iban: str = ""             # for the bank-detail change check
    decision: str = ""         # only an ACCEPTED document sets the known account


def normalise_for_matching(value: str) -> str:
    """
    Compare names and references without being defeated by punctuation.

    "NORTHWIND SUPPLIES LTD" and "Northwind Supplies Ltd." are the same supplier.
    An exact string comparison says they are not, and then the duplicate check
    silently stops working - which is the worst kind of broken, because it still
    returns an answer.
    """
    cleaned = ""
    for character in value.lower():
        if character.isalnum():
            cleaned = cleaned + character
    return cleaned


def field_value(fields: list[ExtractedField], name: str) -> str:
    for field in fields:
        if field.name == name and field.is_present():
            return field.value
    return ""


def reference_of(fields: list[ExtractedField]) -> str:
    for name in ("invoice_number", "receipt_number", "po_number"):
        value = field_value(fields, name)
        if value != "":
            return value
    return ""


def supplier_of(fields: list[ExtractedField]) -> str:
    for name in ("supplier_name", "merchant_name", "party_two"):
        value = field_value(fields, name)
        if value != "":
            return value
    return ""


def document_date_of(fields: list[ExtractedField]) -> str:
    for name in ("invoice_date", "receipt_date", "order_date", "agreement_date"):
        value = field_value(fields, name)
        if value != "":
            return value
    return ""


def describe(seen: SeenDocument) -> str:
    if seen.filename != "":
        return "%s (%s)" % (seen.filename, seen.document_id)
    return seen.document_id


def check_page_says_duplicate(text: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    lowered = text.lower()

    for phrase in DUPLICATE_PHRASES:
        if phrase not in lowered:
            continue
        issues.append(ValidationIssue(
            code="document_says_it_is_a_duplicate",
            severity=Severity.WARNING,
            field="",
            message="the document itself carries the words '%s', so it is a copy "
                    "of something already sent" % phrase,
            expected="an original document",
            actual="'%s' printed on the page" % phrase,
        ))
        break

    return issues


def check_against_seen(document_id: str, fields: list[ExtractedField],
                       already_seen: list[SeenDocument]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    reference = normalise_for_matching(reference_of(fields))
    supplier = normalise_for_matching(supplier_of(fields))
    total = field_value(fields, "total_amount")
    document_date = document_date_of(fields)

    for seen in already_seen:
        if seen.document_id == document_id:
            issues.append(ValidationIssue(
                code="identical_file_already_processed",
                severity=Severity.ERROR,
                field="",
                message="this is byte-for-byte the same file as %s, which has "
                        "already been processed" % describe(seen),
                expected="a document not already in the system",
                actual=document_id,
            ))
            return issues

    for seen in already_seen:
        seen_reference = normalise_for_matching(seen.reference)
        seen_supplier = normalise_for_matching(seen.supplier)

        if reference != "" and seen_reference == reference and supplier == seen_supplier:
            issues.append(ValidationIssue(
                code="same_reference_already_processed",
                severity=Severity.ERROR,
                field="invoice_number",
                message="%s has already sent reference '%s' (processed as %s). "
                        "Paying this would pay the same invoice twice."
                        % (supplier_of(fields) or "this supplier",
                           reference_of(fields), describe(seen)),
                expected="a reference not seen before from this supplier",
                actual=reference_of(fields),
            ))
            return issues

    for seen in already_seen:
        seen_supplier = normalise_for_matching(seen.supplier)
        if supplier == "" or seen_supplier != supplier:
            continue
        if total == "" or seen.total != total:
            continue
        if document_date == "" or seen.document_date != document_date:
            continue

        issues.append(ValidationIssue(
            code="same_amount_same_day_same_supplier",
            severity=Severity.WARNING,
            field="total_amount",
            message="%s already has a document dated %s for exactly %s "
                    "(processed as %s) under a different reference - this may be "
                    "a re-issue of the same charge"
                    % (supplier_of(fields), document_date, total, describe(seen)),
            expected="a charge not already recorded",
            actual="%s on %s" % (total, document_date),
        ))
        return issues

    return issues


def check_bank_details_changed(fields: list[ExtractedField],
                              already_seen: list[SeenDocument]) -> list[ValidationIssue]:
    """
    Has this supplier's bank account changed since last time?

    This is the single most common invoice fraud there is. Someone takes a real
    supplier relationship, sends a real-looking invoice, and changes one line:
    the account number. Every other check in this project passes. The arithmetic
    is right, the VAT number is right, the dates are right, the checksum on the
    new IBAN is right, because the attacker owns a real bank account.

    The only thing that gives it away is that the number is different from last
    month's, and the only way to know that is to have kept last month's. It is
    an ERROR, because a payment to the wrong account is rarely recoverable and
    the cost of asking is one phone call.

    A supplier genuinely changing bank is a real event, and this will fire on it.
    That is the right outcome: a genuine change should be confirmed by phone too.
    """
    issues: list[ValidationIssue] = []

    iban = normalise_for_matching(field_value(fields, "iban"))
    supplier = normalise_for_matching(supplier_of(fields))
    if iban == "" or supplier == "":
        return issues

    for seen in already_seen:
        if normalise_for_matching(seen.supplier) != supplier:
            continue

        # Only a document that was ACCEPTED sets the known-good account.
        #
        # Without this, the first fraudulent invoice poisons the baseline: it is
        # stored, and every genuine invoice afterwards is accused of changing the
        # bank details, because it differs from the fraud. The attack script made
        # this obvious - after one forged IBAN went through, unrelated documents
        # started failing this check for no reason. An account becomes trusted by
        # being paid, not by being seen.
        if seen.decision != "auto_approve":
            continue

        seen_iban = normalise_for_matching(seen.iban)
        if seen_iban == "" or seen_iban == iban:
            continue

        issues.append(ValidationIssue(
            code="supplier_bank_details_changed",
            severity=Severity.ERROR,
            field="iban",
            message="%s has been paid before, but to a different account. The "
                    "last document from them (%s) gave %s, and this one gives "
                    "%s. Confirm the change by telephone using a number you "
                    "already had, not one printed on this document."
                    % (supplier_of(fields), describe(seen), seen.iban,
                       field_value(fields, "iban")),
            expected=seen.iban,
            actual=field_value(fields, "iban"),
        ))
        return issues

    return issues


def run_duplicate_checks(document_id: str, text: str, fields: list[ExtractedField],
                         already_seen: list[SeenDocument]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    for issue in check_page_says_duplicate(text):
        issues.append(issue)
    for issue in check_against_seen(document_id, fields, already_seen):
        issues.append(issue)
    for issue in check_bank_details_changed(fields, already_seen):
        issues.append(issue)

    return issues
