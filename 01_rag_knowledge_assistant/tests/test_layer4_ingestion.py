"""TESTS FOR LAYER 4 - loading, cleaning and chunking."""

from pathlib import Path

import pytest

from rag_assistant.layer4_ingestion.step1_load import UnsupportedFileError, load_file
from rag_assistant.layer4_ingestion.step2_clean import (
    clean_text,
    join_broken_lines,
    remove_repeated_lines,
)
from rag_assistant.layer4_ingestion.step3_chunk import (
    chunk_document,
    looks_like_heading,
    split_into_sections,
    split_into_word_windows,
)


def test_headings_are_recognised():
    assert looks_like_heading("# Refund Policy") == "Refund Policy"
    assert looks_like_heading("## Shipping") == "Shipping"
    assert looks_like_heading("3. Data Retention Rules") == "Data Retention Rules"
    assert looks_like_heading("REFUND POLICY") == "Refund Policy"
    assert looks_like_heading("Customers may request a refund within 30 days.") is None


def test_sections_split_on_headings():
    text = "# Refunds\nThirty days.\n\n# Shipping\nFive days."
    sections = split_into_sections(text)

    assert len(sections) == 2
    assert sections[0].heading == "Refunds"
    assert "Thirty days." in sections[0].text
    assert sections[1].heading == "Shipping"


def test_no_words_are_lost_when_chunking():
    """
    The bug this test exists to prevent: the final short window of a section gets
    dropped, and the end of every document silently disappears from the index.
    """
    words = []
    for number in range(1, 61):
        words.append("w%d" % number)
    text = " ".join(words)

    windows = split_into_word_windows(text, size_words=12, overlap_words=4)

    covered = set()
    for window in windows:
        for token in window.split():
            covered.add(token)

    assert len(covered) == 60


def test_chunks_overlap_so_nothing_falls_between_them():
    words = []
    for number in range(1, 41):
        words.append("w%d" % number)
    windows = split_into_word_windows(" ".join(words), size_words=10, overlap_words=3)

    first_window_words = windows[0].split()
    second_window_words = windows[1].split()

    shared = 0
    for word in first_window_words:
        if word in second_window_words:
            shared = shared + 1
    assert shared == 3


def test_the_heading_is_added_to_every_chunk():
    """
    "30 days" means nothing on its own. "Refund Policy: 30 days" is retrievable.
    """
    text = "# Refund Policy\n" + ("word " * 50)
    chunks = chunk_document(text, size_words=20, overlap_words=5)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.text.startswith("Refund Policy:")


def test_broken_lines_are_joined():
    text = "Customers may request a\nrefund within 30 days.\nNext sentence here."
    joined = join_broken_lines(text)
    assert "request a refund within 30 days." in joined


def test_repeated_page_headers_are_removed():
    lines = []
    for page in range(1, 7):
        lines.append("ACME INTERNAL - PAGE HEADER")
        lines.append("Real content for page %d." % page)
    cleaned = remove_repeated_lines("\n".join(lines))

    assert "ACME INTERNAL - PAGE HEADER" not in cleaned
    assert "Real content for page 3." in cleaned


def test_cleaning_never_empties_real_text():
    cleaned = clean_text("# Title\n\nSome real content here.\n\n\n\nMore content.")
    assert "Some real content here." in cleaned
    assert "More content." in cleaned


def test_unsupported_file_types_are_rejected_clearly(tmp_path):
    path = tmp_path / "data.xlsx"
    path.write_text("not really a spreadsheet")

    with pytest.raises(UnsupportedFileError) as error:
        load_file(path)
    assert ".pdf" in str(error.value)


def test_ingesting_a_sample_file_creates_chunks(ingestion, store):
    samples = Path(__file__).resolve().parents[1] / "samples"
    result = ingestion.ingest_file(samples / "refund_policy.md", access_tag="public")

    assert result.chunks_created > 0
    assert store.count_chunks() == result.chunks_created


def test_ingesting_the_same_file_twice_adds_nothing(ingestion, store):
    samples = Path(__file__).resolve().parents[1] / "samples"

    first = ingestion.ingest_file(samples / "refund_policy.md", access_tag="public")
    count_after_first = store.count_chunks()

    second = ingestion.ingest_file(samples / "refund_policy.md", access_tag="public")

    assert second.chunks_created == 0
    assert second.chunks_skipped_as_duplicate == first.chunks_created
    assert store.count_chunks() == count_after_first
