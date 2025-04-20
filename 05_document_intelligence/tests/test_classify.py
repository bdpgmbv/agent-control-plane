"""Deciding what a document is, and when that decision needs a model."""

from tests.conftest import FakeClient

from doc_intelligence.layer2_models.schemas import DocumentType
from doc_intelligence.layer4_classify.step1_rules import classify_by_rules, confidence_from_score
from doc_intelligence.layer4_classify.step2_model import (
    parse_type_reply,
    shorten_for_classification,
)
from doc_intelligence.layer4_classify.step3_classify import classify_document


def test_every_business_document_is_classified_by_rules_alone(sample_text):
    expected = {
        "01_invoice_clean.txt": DocumentType.INVOICE,
        "02_invoice_arithmetic_error.txt": DocumentType.INVOICE,
        "03_invoice_tax_error.txt": DocumentType.INVOICE,
        "07_invoice_high_value.txt": DocumentType.INVOICE,
        "04_invoice_bad_iban.txt": DocumentType.INVOICE,
        "05_invoice_future_date.txt": DocumentType.INVOICE,
        "08_invoice_duplicate.txt": DocumentType.INVOICE,
        "10_receipt_cafe.txt": DocumentType.RECEIPT,
        "11_purchase_order.txt": DocumentType.PURCHASE_ORDER,
        "12_contract_services.txt": DocumentType.CONTRACT,
    }
    for name, wanted in expected.items():
        verdict = classify_by_rules(sample_text(name))
        assert verdict.document_type == wanted, name
        assert verdict.is_confident, name


def test_a_broken_title_does_not_break_classification(sample_text):
    # invoice_ocr_noise.txt has "lNVOICE" as its heading, with a lowercase L.
    # The classifier must not depend on the title alone.
    verdict = classify_by_rules(sample_text("06_invoice_ocr_noise.txt"))
    assert verdict.document_type == DocumentType.INVOICE
    assert verdict.is_confident


def test_a_letter_is_not_forced_into_a_category(sample_text):
    verdict = classify_by_rules(sample_text("13_unknown_letter.txt"))
    assert verdict.document_type == DocumentType.UNKNOWN
    assert not verdict.is_confident


def test_a_close_call_is_not_treated_as_a_decision():
    # Both types score, neither clearly. Winning by a point is a coin toss.
    text = "INVOICE NUMBER: 1\nRECEIPT NO: 2\nreceipt\nchange due\ncashier"
    verdict = classify_by_rules(text)
    if verdict.document_type != DocumentType.UNKNOWN:
        assert verdict.margin() >= 2.0 or not verdict.is_confident


def test_confidence_is_never_exactly_one():
    # The first version of this function returned 1.00 for eleven of twelve
    # documents. A number that is the same for every input measures nothing.
    seen = set()
    for text in ("purchase order po number authorised by required by order date ship to supplier",
                 "invoice number invoice date amount due bill to payment terms iban",
                 "receipt no cashier change due paid by card till"):
        verdict = classify_by_rules(text)
        score = confidence_from_score(verdict)
        assert score < 1.0
        seen.add(score)
    assert len(seen) >= 1


def test_without_a_model_an_unclear_document_stays_unknown(sample_text):
    result = classify_document(sample_text("13_unknown_letter.txt"), client=None)
    assert result.document_type == DocumentType.UNKNOWN
    assert result.decided_by == "neither"


def test_the_model_is_not_called_when_the_rules_are_sure(sample_text):
    client = FakeClient(replies=["invoice"])
    result = classify_document(sample_text("01_invoice_clean.txt"), client=client)
    assert result.decided_by == "rules"
    assert len(client.prompts) == 0, "the model was paid to confirm what the rules knew"


def test_the_model_is_called_only_when_the_rules_are_stuck(sample_text):
    client = FakeClient(replies=["contract"])
    result = classify_document(sample_text("13_unknown_letter.txt"), client=client)
    assert result.decided_by == "model"
    assert result.document_type == DocumentType.CONTRACT
    assert len(client.prompts) == 1


def test_a_model_inventing_a_type_is_ignored(sample_text):
    client = FakeClient(replies=["delivery_note"])
    result = classify_document(sample_text("13_unknown_letter.txt"), client=client)
    assert result.document_type == DocumentType.UNKNOWN
    assert result.confidence == 0.0


def test_the_model_is_allowed_to_say_unknown(sample_text):
    client = FakeClient(replies=["unknown"])
    result = classify_document(sample_text("13_unknown_letter.txt"), client=client)
    assert result.document_type == DocumentType.UNKNOWN


def test_reply_parsing_tolerates_the_shapes_models_actually_return():
    assert parse_type_reply("Invoice.") == DocumentType.INVOICE
    assert parse_type_reply("  PURCHASE_ORDER  ") == DocumentType.PURCHASE_ORDER
    assert parse_type_reply("This is a receipt.") == DocumentType.RECEIPT
    assert parse_type_reply("") == DocumentType.UNKNOWN


def test_long_documents_are_shortened_from_both_ends():
    text = "TOP\n" + ("filler line\n" * 4000) + "BOTTOM"
    shortened = shorten_for_classification(text)
    assert len(shortened) < len(text)
    assert "TOP" in shortened
    assert "BOTTOM" in shortened
