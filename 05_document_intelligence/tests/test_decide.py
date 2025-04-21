"""Confidence, and the four gates that decide whether a person sees a document."""

from doc_intelligence.layer2_models.schemas import (
    Decision,
    DocumentType,
    ExtractedField,
    FieldSource,
    Severity,
    ValidationIssue,
)
from doc_intelligence.layer7_confidence.step1_confidence import score_confidence
from doc_intelligence.layer7_confidence.step2_decide import decide, largest_amount

GOOD_INVOICE_FIELDS = [
    ExtractedField(name="invoice_number", value="INV-1", source=FieldSource.RULES,
                   confidence=0.93, found_verbatim=True),
    ExtractedField(name="invoice_date", value="2025-03-14", source=FieldSource.RULES,
                   confidence=0.90, found_verbatim=True),
    ExtractedField(name="supplier_name", value="Acme", source=FieldSource.RULES,
                   confidence=0.85, found_verbatim=True),
    ExtractedField(name="total_amount", value="500.00", source=FieldSource.RULES,
                   confidence=0.92, found_verbatim=True),
]


def an_error():
    # A realistic message, because gate 1 passes the issue's own message through
    # to the reviewer. A fixture saying "broken" would make the "every refusal
    # explains itself" test pass or fail on the fixture rather than on the code.
    return ValidationIssue(
        code="line_items_do_not_sum_to_subtotal", severity=Severity.ERROR,
        message="the 3 line items add up to 4730.00, but the subtotal printed "
                "on the document is 4630.00 - out by 100.00")


def a_warning():
    return ValidationIssue(
        code="unusual_tax_rate", severity=Severity.WARNING,
        message="tax of 137.00 on a subtotal of 1000.00 is a rate of 13.70%, "
                "which is not close to any standard rate")


def approve(confidence, fields, issues, amount_limit=10000.0):
    return decide(DocumentType.INVOICE, confidence, fields, issues,
                  has_usable_text=True, auto_approve_confidence=0.90,
                  minimum_required_field_confidence=0.70,
                  always_review_above=amount_limit)


# ---------------------------------------------------------------- confidence

def test_an_error_does_not_reduce_confidence():
    # The central claim of layer 7: "did I read it correctly?" and "is it
    # correct?" are different questions. A crisp invoice that does not add up
    # was read perfectly.
    without = score_confidence(DocumentType.INVOICE, 0.97, GOOD_INVOICE_FIELDS, [], 0)
    with_error = score_confidence(DocumentType.INVOICE, 0.97, GOOD_INVOICE_FIELDS,
                                  [an_error()], 0)
    assert with_error.confidence == without.confidence


def test_warnings_do_reduce_confidence():
    without = score_confidence(DocumentType.INVOICE, 0.97, GOOD_INVOICE_FIELDS, [], 0)
    with_warnings = score_confidence(DocumentType.INVOICE, 0.97, GOOD_INVOICE_FIELDS,
                                     [a_warning(), a_warning()], 0)
    assert with_warnings.confidence < without.confidence


def test_scanner_repairs_reduce_confidence():
    clean = score_confidence(DocumentType.INVOICE, 0.97, GOOD_INVOICE_FIELDS, [], 0)
    repaired = score_confidence(DocumentType.INVOICE, 0.97, GOOD_INVOICE_FIELDS, [], 6)
    assert repaired.confidence < clean.confidence


def test_a_missing_required_field_counts_as_zero_not_as_absent():
    three_of_four = GOOD_INVOICE_FIELDS[:3]
    partial = score_confidence(DocumentType.INVOICE, 0.97, three_of_four, [], 0)
    full = score_confidence(DocumentType.INVOICE, 0.97, GOOD_INVOICE_FIELDS, [], 0)
    assert partial.confidence < full.confidence


def test_an_unknown_document_scores_zero():
    report = score_confidence(DocumentType.UNKNOWN, 0.0, [], [], 0)
    assert report.confidence == 0.0


def test_the_working_is_always_shown():
    report = score_confidence(DocumentType.INVOICE, 0.97, GOOD_INVOICE_FIELDS, [], 2)
    assert len(report.explanation) >= 3
    joined = " ".join(report.explanation)
    assert "errors are deliberately NOT counted" in joined


# ---------------------------------------------------------------- the gates

def test_a_clean_confident_small_invoice_is_processed_automatically():
    report = approve(0.92, GOOD_INVOICE_FIELDS, [])
    assert report.decision == Decision.AUTO_APPROVE
    assert len(report.gates_passed) == 4


def test_gate_one_any_error_stops_it():
    report = approve(0.99, GOOD_INVOICE_FIELDS, [an_error()])
    assert report.decision == Decision.NEEDS_REVIEW


def test_gate_two_low_confidence_stops_it():
    report = approve(0.80, GOOD_INVOICE_FIELDS, [])
    assert report.decision == Decision.NEEDS_REVIEW
    assert "below the 0.90" in report.reasons[0]


def test_gate_three_one_weak_required_field_stops_it():
    # The average would still be high. That is exactly the hole this gate fills.
    fields = list(GOOD_INVOICE_FIELDS)
    fields[3] = ExtractedField(name="total_amount", value="500.00",
                               source=FieldSource.MODEL, confidence=0.30,
                               found_verbatim=False)
    report = approve(0.95, fields, [])
    assert report.decision == Decision.NEEDS_REVIEW
    assert "total amount" in report.reasons[0]


def test_gate_four_a_large_amount_stops_a_perfect_document():
    big = list(GOOD_INVOICE_FIELDS)
    big[3] = ExtractedField(name="total_amount", value="59880.00",
                            source=FieldSource.RULES, confidence=0.92,
                            found_verbatim=True)
    report = approve(0.95, big, [])
    assert report.decision == Decision.NEEDS_REVIEW
    assert "policy" in report.reasons[0]
    # And it says so honestly: nothing was wrong with the reading.
    assert "passed every other gate" in report.reasons[0]


def test_a_contract_value_counts_as_money_at_stake():
    fields = [ExtractedField(name="contract_value", value="84000.00",
                             source=FieldSource.RULES, confidence=0.92,
                             found_verbatim=True)]
    assert largest_amount(fields) == 84000.0


def test_an_unreadable_document_is_rejected_not_queued():
    report = decide(DocumentType.UNKNOWN, 0.0, [], [], has_usable_text=False,
                    auto_approve_confidence=0.90,
                    minimum_required_field_confidence=0.70,
                    always_review_above=10000.0)
    assert report.decision == Decision.REJECT
    assert "supplied again" in report.reasons[0]


def test_an_unrecognised_type_goes_to_a_person_not_the_bin():
    report = decide(DocumentType.UNKNOWN, 0.0, [], [], has_usable_text=True,
                    auto_approve_confidence=0.90,
                    minimum_required_field_confidence=0.70,
                    always_review_above=10000.0)
    assert report.decision == Decision.NEEDS_REVIEW


def test_every_refusal_explains_itself():
    for confidence, fields, issues in ((0.80, GOOD_INVOICE_FIELDS, []),
                                       (0.99, GOOD_INVOICE_FIELDS, [an_error()])):
        report = approve(confidence, fields, issues)
        assert len(report.reasons) > 0
        assert len(report.reasons[0]) > 20
