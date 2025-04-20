"""Pulling fields and line items out without a model."""

from tests.conftest import FakeClient

from doc_intelligence.layer2_models.schemas import DocumentType, FieldSource
from doc_intelligence.layer5_extract.step1_schemas import fields_for, has_line_items
from doc_intelligence.layer5_extract.step2_patterns import (
    extract_line_items,
    extract_with_patterns,
    money_from_line,
    parse_columnar_row,
    tidy_company_name,
)
from doc_intelligence.layer5_extract.step4_extract import extract_fields, missing_specs_for


def value_of(fields, name):
    for field in fields:
        if field.name == name:
            return field.value
    return None


def test_a_clean_invoice_gives_up_every_field(sample_text):
    fields, items, _ = extract_with_patterns(sample_text("01_invoice_clean.txt"),
                                             DocumentType.INVOICE)
    assert value_of(fields, "invoice_number") == "INV-2025-0412"
    assert value_of(fields, "invoice_date") == "2025-03-14"
    assert value_of(fields, "due_date") == "2025-04-13"
    assert value_of(fields, "supplier_name") == "NORTHWIND SUPPLIES LTD"
    assert value_of(fields, "supplier_vat") == "GB123456789"
    assert value_of(fields, "customer_name") == "Fenwick Analytics Ltd"
    assert value_of(fields, "subtotal") == "2323.00"
    assert value_of(fields, "tax_amount") == "464.60"
    assert value_of(fields, "total_amount") == "2787.60"
    assert value_of(fields, "iban") == "GB82 WEST 1234 5698 7654 32"
    assert len(items) == 4


def test_total_is_not_read_out_of_the_word_subtotal(sample_text):
    fields, _, _ = extract_with_patterns(sample_text("01_invoice_clean.txt"),
                                         DocumentType.INVOICE)
    assert value_of(fields, "total_amount") == "2787.60"
    assert value_of(fields, "subtotal") == "2323.00"


def test_a_tax_line_gives_the_amount_not_the_percentage():
    # "VAT 20%   464.60" - the first number is the rate.
    assert money_from_line(" 20%         464.60")[0] == 464.60


def test_a_vat_registration_number_is_never_read_as_tax(sample_text):
    fields, _, _ = extract_with_patterns(sample_text("01_invoice_clean.txt"),
                                         DocumentType.INVOICE)
    assert value_of(fields, "tax_amount") == "464.60"
    assert value_of(fields, "tax_amount") != "123456789.00"


def test_german_number_formatting(sample_text):
    fields, items, _ = extract_with_patterns(sample_text("02_invoice_arithmetic_error.txt"),
                                             DocumentType.INVOICE)
    assert value_of(fields, "subtotal") == "4630.00"
    assert value_of(fields, "total_amount") == "5509.70"
    assert items[0].unit_price == 3450.00


def test_a_number_inside_a_description_is_not_the_quantity():
    # "Sample rack, 50 position   8   62,50   500,00" - reading from the left
    # would make the quantity 50.
    item = parse_columnar_row("Sample rack, 50 position             8        62,50       500,00")
    assert item.quantity == 8
    assert item.unit_price == 62.50
    assert item.line_total == 500.00
    assert item.description == "Sample rack, 50 position"


def test_an_address_is_not_a_line_item():
    # "44 Harbour Road, Bristol BS1 5TY" holds three numbers, which is exactly
    # the shape of a table row. The column-gap rule is what rejects it.
    assert parse_columnar_row("44 Harbour Road, Bristol BS1 5TY") is None
    lines = ["NORTHWIND SUPPLIES LTD", "44 Harbour Road, Bristol BS1 5TY"]
    items, _ = extract_line_items(lines)
    assert len(items) == 0


def test_a_till_roll_is_read_from_the_quantity_first_form(sample_text):
    fields, items, _ = extract_with_patterns(sample_text("10_receipt_cafe.txt"),
                                             DocumentType.RECEIPT)
    assert len(items) == 3
    assert items[0].description == "Flat white"
    assert items[0].quantity == 2
    assert items[0].line_total == 6.40
    assert value_of(fields, "total_amount") == "14.10"


def test_a_contract_uses_a_different_schema_entirely(sample_text):
    fields, items, _ = extract_with_patterns(sample_text("12_contract_services.txt"),
                                             DocumentType.CONTRACT)
    assert value_of(fields, "party_one") == "Fenwick Analytics Ltd"
    assert value_of(fields, "party_two") == "Copperfield Data Consulting Ltd"
    assert value_of(fields, "start_date") == "2025-04-15"
    assert value_of(fields, "contract_value") == "84000.00"
    assert len(items) == 0
    assert not has_line_items(DocumentType.CONTRACT)


def test_a_date_that_wrapped_onto_the_next_line_is_still_found(sample_text):
    # "commences on 15 April 2025 and continues until\n   14 April 2026"
    fields, _, _ = extract_with_patterns(sample_text("12_contract_services.txt"),
                                         DocumentType.CONTRACT)
    assert value_of(fields, "end_date") == "2026-04-14"


def test_the_most_specific_label_wins_wherever_it_appears(sample_text):
    # "Payment Terms:" appears BELOW a sentence containing the word "invoiced".
    # Scanning line by line found the wrong one first.
    fields, _, _ = extract_with_patterns(sample_text("12_contract_services.txt"),
                                         DocumentType.CONTRACT)
    assert value_of(fields, "payment_terms") == "30 days from the date of invoice."
    assert value_of(fields, "governing_law") == "the laws of England and Wales."


def test_currency_inference_is_marked_as_inferred(sample_text):
    fields, _, _ = extract_with_patterns(sample_text("01_invoice_clean.txt"),
                                         DocumentType.INVOICE)
    for field in fields:
        if field.name == "currency":
            assert field.value == "GBP"
            assert field.source == FieldSource.COMPUTED
            assert "inferred" in field.note


def test_company_names_are_trimmed_to_the_name():
    assert tidy_company_name(
        'Fenwick Analytics Ltd, of 12 Rowan Street, Leeds LS2 8JT ("the Client")'
    ) == "Fenwick Analytics Ltd"


def test_the_model_is_not_called_when_every_required_field_was_found(sample_text):
    client = FakeClient(replies=['{"payment_terms": "30 days"}'])
    outcome = extract_fields(sample_text("01_invoice_clean.txt"),
                             DocumentType.INVOICE, client=client)
    assert len(client.prompts) == 0
    assert not outcome.model_used


def test_a_missing_optional_field_alone_does_not_buy_a_model_call(sample_text):
    # Eight of the sample invoices state no payment terms. They state none
    # because there are none, not because the extractor failed.
    client = FakeClient(replies=['{"payment_terms": "30 days"}'])
    outcome = extract_fields(sample_text("03_invoice_tax_error.txt"),
                             DocumentType.INVOICE, client=client)
    required, optional = missing_specs_for(DocumentType.INVOICE, outcome.fields)
    assert len(required) == 0
    assert len(optional) > 0
    assert len(client.prompts) == 0


def test_a_missing_required_field_does_buy_a_model_call():
    text = "INVOICE\nBill To: Someone\nTOTAL 100.00\nInvoice Date: 01 March 2025"
    client = FakeClient(replies=['{"invoice_number": "INV-9"}'])
    extract_fields(text, DocumentType.INVOICE, client=client)
    assert len(client.prompts) == 1


def test_every_document_type_has_at_least_one_required_field():
    for document_type in (DocumentType.INVOICE, DocumentType.RECEIPT,
                          DocumentType.PURCHASE_ORDER, DocumentType.CONTRACT):
        required = 0
        for spec in fields_for(document_type):
            if spec.required:
                required = required + 1
        assert required > 0, document_type


def test_a_minus_sign_is_not_mistaken_for_a_label_separator():
    # The separator stripper used to eat the minus, turning a credit note for
    # -2,787.60 into a payable of +2,787.60. The attack script found it.
    from doc_intelligence.layer5_extract.step2_patterns import label_pattern, text_after_label

    cases = [
        ("TOTAL       -2,787.60", "total", "-2,787.60"),
        ("Total - 500.00", "total", "500.00"),
        ("Total: 500.00", "total", "500.00"),
        ("TOTAL        480.00", "total", "480.00"),
    ]
    for line, label, wanted in cases:
        match = label_pattern(label).search(line)
        assert text_after_label(line, match) == wanted, line


def test_a_negative_total_survives_extraction(sample_text):
    text = sample_text("01_invoice_clean.txt").replace("TOTAL        2,787.60",
                                                    "TOTAL       -2,787.60")
    fields, _, _ = extract_with_patterns(text, DocumentType.INVOICE)
    assert value_of(fields, "total_amount") == "-2787.60"
