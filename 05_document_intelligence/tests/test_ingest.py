"""Loading documents and repairing scanner confusions."""

import pytest

from doc_intelligence.layer3_ingest.step1_load import (
    UnsupportedDocument,
    classify_suffix,
    load_from_bytes,
    make_document_id,
)
from doc_intelligence.layer3_ingest.step2_clean import (
    clean_document_text,
    repair_run,
)


def test_document_id_is_content_based():
    # Renaming a file must not make it look new, or the duplicate check stops
    # recognising a file it has already processed.
    first = make_document_id("a.txt", b"same bytes")
    second = make_document_id("b.txt", b"same bytes")
    third = make_document_id("a.txt", b"other bytes")
    assert first == second
    assert first != third


def test_unsupported_file_types_are_refused_clearly():
    with pytest.raises(UnsupportedDocument):
        classify_suffix("accounts.xlsx")


def test_image_is_marked_as_needing_vision():
    document = load_from_bytes("scan.png", b"\x89PNG fake bytes")
    assert document.kind == "image"
    assert document.needs_vision()
    assert document.image_base64 != ""


def test_ocr_repair_only_touches_runs_that_already_hold_a_digit():
    assert repair_run("2O25") == "2025"
    assert repair_run("O8") == "08"
    assert repair_run("3l") == "31"
    # No digit in the run, so nothing is guessed at.
    assert repair_run("TOTAL") == "TOTAL"
    assert repair_run("lNVOICE") == "lNVOICE"
    assert repair_run("Ordered") == "Ordered"


def test_ocr_repair_leaves_mixed_alphanumeric_references_alone():
    # G and B are not confusable characters, so this is a reference, not a
    # damaged number. Leaving it broken is the point: layer 6 catches it and a
    # person looks. A silent repair would have hidden a wrong VAT number.
    assert repair_run("GB55l234567") == "GB55l234567"
    assert repair_run("A5") == "A5"
    assert repair_run("BS1") == "BS1"


def test_cleaning_reports_every_repair_it_made():
    result = clean_document_text("Invoice Number: INV-2O25-O339\nDate: O8 February 2025")
    assert "INV-2025-0339" in result.text
    assert "08 February" in result.text
    assert result.repair_count() == 3


def test_cleaning_preserves_the_column_layout():
    # Line breaks and column gaps are what separate a description from a price.
    # Collapsing them would make every table unreadable.
    original = "Widget       4      10.00      40.00\nGadget       2       5.00      10.00"
    result = clean_document_text(original)
    assert result.text.count("\n") == 1
    assert "       " in result.text


def test_text_loader_survives_bad_bytes():
    document = load_from_bytes("odd.txt", b"Total: \xff\xfe 100")
    assert "Total" in document.text
