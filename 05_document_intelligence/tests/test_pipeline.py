"""The whole pipeline, over the whole corpus."""

from tests.conftest import FakeClient

from doc_intelligence.layer2_models.schemas import Decision, DocumentType
from doc_intelligence.layer3_ingest.step1_load import load_from_bytes

# What each sample document is, and what should happen to it. This table is the
# project's specification - if a change makes one of these rows wrong, either the
# change is wrong or the expectation needs a reason to move.
EXPECTED = {
    "01_invoice_clean.txt":            (DocumentType.INVOICE, Decision.AUTO_APPROVE, 0),
    "11_purchase_order.txt":           (DocumentType.PURCHASE_ORDER, Decision.AUTO_APPROVE, 0),
    "10_receipt_cafe.txt":             (DocumentType.RECEIPT, Decision.AUTO_APPROVE, 0),
    "12_contract_services.txt":        (DocumentType.CONTRACT, Decision.NEEDS_REVIEW, 0),
    "07_invoice_high_value.txt":       (DocumentType.INVOICE, Decision.NEEDS_REVIEW, 0),
    "02_invoice_arithmetic_error.txt": (DocumentType.INVOICE, Decision.NEEDS_REVIEW, 1),
    "03_invoice_tax_error.txt":        (DocumentType.INVOICE, Decision.NEEDS_REVIEW, 1),
    "04_invoice_bad_iban.txt":         (DocumentType.INVOICE, Decision.NEEDS_REVIEW, 1),
    "05_invoice_future_date.txt":      (DocumentType.INVOICE, Decision.NEEDS_REVIEW, 1),
    "08_invoice_duplicate.txt":        (DocumentType.INVOICE, Decision.NEEDS_REVIEW, 1),
    "06_invoice_ocr_noise.txt":        (DocumentType.INVOICE, Decision.NEEDS_REVIEW, 0),
    "09_invoice_bank_changed.txt":     (DocumentType.INVOICE, Decision.NEEDS_REVIEW, 1),
    "13_unknown_letter.txt":           (DocumentType.UNKNOWN, Decision.NEEDS_REVIEW, 1),
}


def test_every_document_gets_the_outcome_it_should(pipeline):
    results, _ = pipeline.process_directory("samples")
    by_name = {}
    for result in results:
        by_name[result.filename] = result

    for name in EXPECTED:
        wanted_type, wanted_decision, wanted_errors = EXPECTED[name]
        result = by_name[name]
        assert result.document_type == wanted_type, name
        assert result.decision == wanted_decision, "%s: %s" % (name, result.decision_reasons)
        found_codes = []
        for issue in result.errors():
            found_codes.append(issue.code)
        assert len(result.errors()) == wanted_errors, "%s: %s" % (name, found_codes)


def test_nothing_with_an_error_is_ever_processed_automatically(pipeline):
    # The number that matters. A straight-through rate is worthless if wrong
    # documents ride along inside it.
    results, _ = pipeline.process_directory("samples")
    for result in results:
        if result.decision == Decision.AUTO_APPROVE:
            assert len(result.errors()) == 0, result.filename


def test_offline_costs_nothing(pipeline):
    results, _ = pipeline.process_directory("samples")
    for result in results:
        assert result.model_calls == 0
        assert result.cost_usd == 0.0


def test_the_summary_adds_up(pipeline):
    results, summary = pipeline.process_directory("samples")
    assert summary.processed == 13
    assert summary.auto_approved + summary.needs_review + summary.rejected == 13
    assert summary.auto_approved == 3
    assert summary.straight_through_rate == round(3 / 13, 3)


def test_processing_the_same_file_twice_is_caught(pipeline):
    first = pipeline.process_path("samples/01_invoice_clean.txt")
    second = pipeline.process_path("samples/01_invoice_clean.txt")
    assert first.decision == Decision.AUTO_APPROVE
    codes = []
    for issue in second.issues:
        codes.append(issue.code)
    assert "identical_file_already_processed" in codes
    assert second.decision == Decision.NEEDS_REVIEW


def test_an_empty_document_is_rejected_rather_than_queued(pipeline):
    result = pipeline.process_text("   \n  \n", "empty.txt")
    assert result.decision == Decision.REJECT


def test_a_scan_without_a_vision_model_says_so_plainly(pipeline):
    document = load_from_bytes("scan.png", b"\x89PNG not really an image")
    result = pipeline.process(document)
    assert result.decision == Decision.REJECT
    joined = " ".join(result.notes)
    assert "vision model" in joined


def test_a_scan_with_vision_is_processed_exactly_like_text(store):
    # The point of the vision step: it produces characters, and then every
    # ordinary rule applies. Nothing downstream knows it came from pixels.
    from datetime import date

    from doc_intelligence.layer9_api.pipeline import DocumentPipeline

    transcription = open("samples/01_invoice_clean.txt").read()
    client = FakeClient(image_reply=transcription)
    scanning = DocumentPipeline(store=store, client=client, today=date(2026, 9, 25))

    document = load_from_bytes("scan.png", b"\x89PNG pretend")
    result = scanning.process(document)

    assert client.image_calls == 1
    assert result.document_type == DocumentType.INVOICE
    assert result.value_of("invoice_number") == "INV-2025-0412"
    assert result.value_of("total_amount") == "2787.60"
    assert len(result.line_items) == 4
    assert result.decision == Decision.AUTO_APPROVE


def test_the_structured_output_carries_provenance(pipeline):
    result = pipeline.process_path("samples/01_invoice_clean.txt")
    structured = result.structured
    assert structured["document_type"] == "invoice"
    total = structured["fields"]["total_amount"]
    assert total["value"] == "2787.60"
    assert total["source"] == "rules"
    assert total["verbatim"] is True
    assert len(structured["line_items"]) == 4


def test_a_broken_file_does_not_abandon_the_batch(pipeline, tmp_path):
    (tmp_path / "fine.txt").write_text(open("samples/01_invoice_clean.txt").read())
    (tmp_path / "nope.xlsx").write_bytes(b"not a document")
    results, summary = pipeline.process_directory(tmp_path)
    assert summary.processed == 2
    assert summary.auto_approved == 1
    assert summary.rejected == 1


def test_results_survive_a_round_trip_through_the_store(pipeline):
    result = pipeline.process_path("samples/01_invoice_clean.txt")
    loaded = pipeline.store.load_result(result.document_id)
    assert loaded.value_of("total_amount") == "2787.60"
    assert loaded.decision == result.decision
    assert len(loaded.line_items) == len(result.line_items)
