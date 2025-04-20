"""Every deterministic check, including the ones that must NOT fire."""


from tests.conftest import TODAY

from doc_intelligence.layer2_models.schemas import (
    DocumentType,
    ExtractedField,
    FieldSource,
    LineItem,
    Severity,
)
from doc_intelligence.layer5_extract.step2_patterns import extract_with_patterns
from doc_intelligence.layer6_validate.step1_fields import (
    check_required_fields,
    check_values_are_traceable,
)
from doc_intelligence.layer6_validate.step2_arithmetic import run_arithmetic_checks
from doc_intelligence.layer6_validate.step3_identifiers import run_identifier_checks
from doc_intelligence.layer6_validate.step4_dates import run_date_checks
from doc_intelligence.layer6_validate.step5_duplicates import SeenDocument, run_duplicate_checks
from doc_intelligence.layer6_validate.step6_validate import ValidationSettings, validate_document

SETTINGS = ValidationSettings(today=TODAY)


def codes(issues):
    found = []
    for issue in issues:
        found.append(issue.code)
    return found


def money_field(name, value):
    return ExtractedField(name=name, value=value, source=FieldSource.RULES,
                          confidence=0.92, found_verbatim=True)


def run_all(name, sample_text, already_seen=None):
    text = sample_text(name)
    document_type = {
        "12_contract_services.txt": DocumentType.CONTRACT,
        "10_receipt_cafe.txt": DocumentType.RECEIPT,
        "11_purchase_order.txt": DocumentType.PURCHASE_ORDER,
    }.get(name, DocumentType.INVOICE)
    fields, items, _ = extract_with_patterns(text, document_type)
    return validate_document("doc_x", document_type, text, fields, items,
                             SETTINGS, already_seen or [])


# ---------------------------------------------------------------- arithmetic

def test_line_items_that_do_not_sum_to_the_subtotal_are_caught(sample_text):
    assert "line_items_do_not_sum_to_subtotal" in codes(
        run_all("02_invoice_arithmetic_error.txt", sample_text))


def test_a_subtotal_plus_tax_that_is_not_the_total_is_caught(sample_text):
    assert "subtotal_plus_tax_is_not_total" in codes(
        run_all("03_invoice_tax_error.txt", sample_text))


def test_a_row_that_does_not_multiply_is_caught():
    items = [LineItem(description="Chair", quantity=4, unit_price=249.00,
                      line_total=1996.00)]
    issues = run_arithmetic_checks(items, [], 0.02, 0.05)
    assert "line_item_does_not_multiply" in codes(issues)


def test_rounding_of_a_penny_is_not_an_error():
    items = [LineItem(description="A", quantity=3, unit_price=33.33,
                      line_total=99.99)]
    fields = [money_field("subtotal", "100.00")]
    issues = run_arithmetic_checks(items, fields, 0.02, 0.05)
    assert "line_items_do_not_sum_to_subtotal" not in codes(issues)


def test_a_document_with_no_subtotal_is_still_checked():
    # Without this fallback, omitting the subtotal skipped every sum check.
    items = [LineItem(description="A", quantity=1, unit_price=100.0, line_total=100.0)]
    fields = [money_field("total_amount", "500.00"), money_field("tax_amount", "20.00")]
    assert "line_items_do_not_sum_to_total" in codes(
        run_arithmetic_checks(items, fields, 0.02, 0.05))


def test_an_odd_tax_rate_is_a_warning_not_an_error():
    fields = [money_field("subtotal", "1000.00"), money_field("tax_amount", "137.00")]
    issues = run_arithmetic_checks([], fields, 0.02, 0.05)
    for issue in issues:
        if issue.code == "unusual_tax_rate":
            assert issue.severity == Severity.WARNING
            return
    raise AssertionError("a 13.7% rate should have been flagged")


def test_standard_rates_are_not_flagged():
    for subtotal, tax in (("1000.00", "200.00"), ("1000.00", "190.00"),
                          ("1000.00", "50.00"), ("1000.00", "0.00")):
        fields = [money_field("subtotal", subtotal), money_field("tax_amount", tax)]
        assert "unusual_tax_rate" not in codes(run_arithmetic_checks([], fields, 0.02, 0.05))


# ---------------------------------------------------------------- identifiers

def test_a_bad_iban_checksum_is_an_error(sample_text):
    assert "iban_checksum_failed" in codes(run_all("04_invoice_bad_iban.txt", sample_text))


def test_a_good_iban_passes(sample_text):
    assert "iban_checksum_failed" not in codes(run_all("01_invoice_clean.txt", sample_text))


def test_a_scanned_document_gets_wording_about_the_scan():
    fields = [ExtractedField(name="iban", value="GB82 WEST 1234 5698 7654 99",
                             source=FieldSource.RULES, confidence=0.9,
                             found_verbatim=True)]
    issues = run_identifier_checks(DocumentType.INVOICE, fields, had_scanner_repairs=True)
    assert "scanner repairs" in issues[0].message


def test_a_damaged_vat_number_is_only_a_warning(sample_text):
    issues = run_all("06_invoice_ocr_noise.txt", sample_text)
    for issue in issues:
        if issue.code == "vat_number_format_wrong":
            assert issue.severity == Severity.WARNING
            return
    raise AssertionError("the damaged VAT number was not flagged")


# ---------------------------------------------------------------- dates

def test_a_document_dated_in_the_future_is_an_error(sample_text):
    assert "document_dated_in_the_future" in codes(
        run_all("05_invoice_future_date.txt", sample_text))


def test_a_due_date_before_the_invoice_date_is_an_error():
    fields = [
        ExtractedField(name="invoice_date", value="2025-03-14",
                       source=FieldSource.RULES, confidence=0.9, found_verbatim=True),
        ExtractedField(name="due_date", value="2025-02-14",
                       source=FieldSource.RULES, confidence=0.9, found_verbatim=True),
    ]
    assert "due_before_issue" in codes(
        run_date_checks(DocumentType.INVOICE, fields, TODAY, 30, 1825))


def test_a_contract_that_ends_before_it_starts_is_an_error():
    fields = [
        ExtractedField(name="start_date", value="2026-04-15",
                       source=FieldSource.RULES, confidence=0.9, found_verbatim=True),
        ExtractedField(name="end_date", value="2025-04-14",
                       source=FieldSource.RULES, confidence=0.9, found_verbatim=True),
    ]
    assert "term_ends_before_it_starts" in codes(
        run_date_checks(DocumentType.CONTRACT, fields, TODAY, 30, 1825))


def test_a_backdated_contract_is_not_an_error():
    # Signed in April, effective from January. Ordinary practice, and an "error"
    # that fires on correct documents teaches people to ignore the queue.
    fields = [
        ExtractedField(name="agreement_date", value="2026-04-01",
                       source=FieldSource.RULES, confidence=0.9, found_verbatim=True),
        ExtractedField(name="start_date", value="2026-01-01",
                       source=FieldSource.RULES, confidence=0.9, found_verbatim=True),
        ExtractedField(name="end_date", value="2026-12-31",
                       source=FieldSource.RULES, confidence=0.9, found_verbatim=True),
    ]
    assert codes(run_date_checks(DocumentType.CONTRACT, fields, TODAY, 30, 1825)) == []


# ---------------------------------------------------------------- duplicates

def test_the_same_reference_from_the_same_supplier_is_an_error(sample_text):
    seen = [SeenDocument(document_id="doc_earlier", filename="01_invoice_clean.txt",
                         reference="INV-2025-0412", supplier="NORTHWIND SUPPLIES LTD",
                         total="2787.60", document_date="2025-03-14")]
    assert "same_reference_already_processed" in codes(
        run_all("08_invoice_duplicate.txt", sample_text, seen))


def test_punctuation_does_not_defeat_the_duplicate_check():
    fields = [
        ExtractedField(name="invoice_number", value="INV-2025-0412",
                       source=FieldSource.RULES, confidence=0.9, found_verbatim=True),
        ExtractedField(name="supplier_name", value="Northwind Supplies Ltd.",
                       source=FieldSource.RULES, confidence=0.9, found_verbatim=True),
    ]
    seen = [SeenDocument(document_id="other", reference="inv 2025 0412",
                         supplier="NORTHWIND SUPPLIES LTD")]
    assert "same_reference_already_processed" in codes(
        run_duplicate_checks("doc_new", "", fields, seen))


def test_the_same_file_twice_is_caught_by_its_hash():
    seen = [SeenDocument(document_id="doc_same", filename="first.txt")]
    assert "identical_file_already_processed" in codes(
        run_duplicate_checks("doc_same", "", [], seen))


def test_the_words_on_the_page_are_read(sample_text):
    assert "document_says_it_is_a_duplicate" in codes(
        run_all("08_invoice_duplicate.txt", sample_text))


# ---------------------------------------------------------------- fields

def test_a_missing_required_field_is_an_error():
    issues = check_required_fields(DocumentType.INVOICE, [])
    assert len(issues) == 4
    for issue in issues:
        assert issue.severity == Severity.ERROR


def test_a_value_that_is_not_in_the_document_is_an_error():
    fields = [ExtractedField(name="total_amount", value="9999.00",
                             raw_value="9999.00", source=FieldSource.MODEL,
                             confidence=0.30, found_verbatim=False)]
    issues = check_values_are_traceable(fields)
    assert codes(issues) == ["value_not_in_document"]


def test_a_computed_or_corrected_value_is_not_flagged_as_untraceable():
    fields = [
        ExtractedField(name="currency", value="GBP", source=FieldSource.COMPUTED,
                       confidence=0.6, found_verbatim=False),
        ExtractedField(name="subtotal", value="4730.00", source=FieldSource.CORRECTED,
                       confidence=1.0, found_verbatim=False),
    ]
    assert check_values_are_traceable(fields) == []


# ---------------------------------------------------------------- no false alarms

def test_the_clean_documents_produce_no_errors_at_all(sample_text):
    for name in ("01_invoice_clean.txt", "07_invoice_high_value.txt",
                 "11_purchase_order.txt", "10_receipt_cafe.txt", "12_contract_services.txt"):
        issues = run_all(name, sample_text)
        errors = []
        for issue in issues:
            if issue.severity == Severity.ERROR:
                errors.append(issue.code)
        assert errors == [], "%s produced %s" % (name, errors)


def test_a_contract_is_not_put_through_the_arithmetic_checks(sample_text):
    issues = run_all("12_contract_services.txt", sample_text)
    for code in codes(issues):
        assert "sum" not in code and "multiply" not in code


# ---------------------------------------------------------------- bank details

def test_a_changed_bank_account_is_an_error(sample_text):
    seen = [SeenDocument(document_id="doc_before", filename="march.txt",
                         supplier="NORTHWIND SUPPLIES LTD",
                         iban="GB82 WEST 1234 5698 7654 32",
                         decision="auto_approve")]
    assert "supplier_bank_details_changed" in codes(
        run_all("09_invoice_bank_changed.txt", sample_text, seen))


def test_the_same_bank_account_is_not_flagged(sample_text):
    seen = [SeenDocument(document_id="doc_before", filename="march.txt",
                         supplier="NORTHWIND SUPPLIES LTD",
                         iban="GB82 WEST 1234 5698 7654 32",
                         decision="auto_approve")]
    assert "supplier_bank_details_changed" not in codes(
        run_all("01_invoice_clean.txt", sample_text, seen))


def test_an_unapproved_document_does_not_become_the_trusted_account(sample_text):
    # A forged invoice that was sent to review must not become the baseline, or
    # every genuine invoice afterwards gets accused of changing the bank details.
    seen = [SeenDocument(document_id="doc_fraud", filename="forged.txt",
                         supplier="NORTHWIND SUPPLIES LTD",
                         iban="GB29 NWBK 6016 1331 9268 19",
                         decision="needs_review")]
    assert "supplier_bank_details_changed" not in codes(
        run_all("01_invoice_clean.txt", sample_text, seen))


def test_a_different_supplier_is_not_compared(sample_text):
    seen = [SeenDocument(document_id="doc_other", filename="other.txt",
                         supplier="ASHDOWN LOGISTICS LTD",
                         iban="GB29 NWBK 6016 1331 9268 19",
                         decision="auto_approve")]
    assert "supplier_bank_details_changed" not in codes(
        run_all("01_invoice_clean.txt", sample_text, seen))
