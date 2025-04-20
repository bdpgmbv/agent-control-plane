"""
The guard that stops an invented number being paid.

Every value a model returns is searched for in the document. These tests are the
ones that would fail loudest if someone ever decided to trust the model's output
directly, so they are worth reading before changing layer 5.
"""

from tests.conftest import FakeClient

from doc_intelligence.layer2_models.schemas import DocumentType
from doc_intelligence.layer5_extract.step1_schemas import spec_for
from doc_intelligence.layer5_extract.step3_model import (
    UNVERIFIED_CONFIDENCE,
    VERIFIED_CONFIDENCE,
    appears_in_document,
    extract_missing_fields,
    normalise_value,
    parse_json_object,
)
from doc_intelligence.layer5_extract.step4_extract import extract_fields, merge_model_fields

DOCUMENT = """INVOICE
Invoice Number:   INV-2025-0412
Invoice Date:     14 March 2025
                         Subtotal      2,323.00
                         TOTAL        2,787.60
"""


def test_a_value_printed_on_the_page_is_verified():
    assert appears_in_document("INV-2025-0412", DOCUMENT)
    assert appears_in_document("2,787.60", DOCUMENT)
    assert appears_in_document("14 March 2025", DOCUMENT)


def test_thousands_separators_do_not_defeat_the_check():
    assert appears_in_document("2787.60", DOCUMENT)


def test_a_number_that_is_one_penny_out_is_caught():
    assert not appears_in_document("2,787.61", DOCUMENT)


def test_a_reference_that_is_one_digit_out_is_caught():
    assert not appears_in_document("INV-2025-0413", DOCUMENT)


def test_a_number_that_is_nowhere_on_the_page_is_caught():
    assert not appears_in_document("9,999.00", DOCUMENT)


def test_an_invented_value_is_kept_but_cannot_clear_any_gate():
    specs = [spec_for(DocumentType.INVOICE, "total_amount")]
    client = FakeClient(replies=['{"total_amount": "9999.00"}'])
    fields, _, error = extract_missing_fields(DOCUMENT, specs, client)

    assert error == ""
    assert len(fields) == 1
    assert fields[0].value == "9999.00"
    assert not fields[0].found_verbatim
    assert fields[0].confidence == UNVERIFIED_CONFIDENCE
    assert "UNVERIFIED" in fields[0].note
    # Below every gate in layer 7, which is the whole point of the number.
    assert fields[0].confidence < 0.70


def test_a_value_read_off_the_page_is_trusted():
    specs = [spec_for(DocumentType.INVOICE, "total_amount")]
    client = FakeClient(replies=['{"total_amount": "2,787.60"}'])
    fields, _, _ = extract_missing_fields(DOCUMENT, specs, client)
    assert fields[0].found_verbatim
    assert fields[0].confidence == VERIFIED_CONFIDENCE
    assert fields[0].value == "2787.60"


def test_a_null_answer_is_respected_rather_than_filled_in():
    specs = [spec_for(DocumentType.INVOICE, "iban")]
    client = FakeClient(replies=['{"iban": null}'])
    fields, _, _ = extract_missing_fields(DOCUMENT, specs, client)
    assert len(fields) == 0


def test_json_fences_and_stray_prose_are_survived():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Here you go:\n{"a": 1}\nhope that helps') == {"a": 1}
    assert parse_json_object("sorry, I cannot help with that") == {}


def test_an_unparseable_reply_does_not_crash_the_document():
    specs = [spec_for(DocumentType.INVOICE, "total_amount")]
    client = FakeClient(replies=["I am not able to read this document."])
    fields, _, error = extract_missing_fields(DOCUMENT, specs, client)
    assert fields == []
    assert error != ""


def test_a_model_value_that_is_not_the_right_kind_is_discarded():
    spec = spec_for(DocumentType.INVOICE, "invoice_date")
    value, note = normalise_value(spec, "sometime next spring")
    assert value == ""
    assert "not a date" in note


def test_a_pattern_result_is_never_overwritten_by_the_model():
    from doc_intelligence.layer2_models.schemas import ExtractedField, FieldSource

    from_patterns = [ExtractedField(name="total_amount", value="2787.60",
                                    source=FieldSource.RULES, confidence=0.92)]
    from_model = [ExtractedField(name="total_amount", value="9999.00",
                                 source=FieldSource.MODEL, confidence=0.30)]
    merged = merge_model_fields(from_patterns, from_model)
    assert merged[0].value == "2787.60"
    assert merged[0].source == FieldSource.RULES


def test_the_model_failing_does_not_fail_the_document():
    class BrokenClient:
        def complete(self, system_prompt, user_prompt, max_tokens=1200):
            from doc_intelligence.layer0_shared.model_client import ModelUnavailable
            raise ModelUnavailable("no credit", "no_credit")

    text = "INVOICE\nBill To: Someone\nTOTAL 100.00\nInvoice Date: 01 March 2025"
    outcome = extract_fields(text, DocumentType.INVOICE, client=BrokenClient())
    # Whatever the patterns found survives, and the reason is recorded.
    assert len(outcome.fields) > 0
    joined = " ".join(outcome.notes)
    assert "no credit" in joined or "could not" in joined
