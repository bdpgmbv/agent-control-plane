"""TESTS FOR LAYER 3 - storage, search and access control at the database level."""

import hashlib

from rag_assistant.layer0_shared.text_tools import to_stems
from rag_assistant.layer2_models.schemas import Chunk, Document


def add_one(store, embedder, document_id, title, source, tag, text):
    """Helper: put a single one-chunk document into the store."""
    store.add_document(
        Document(document_id=document_id, title=title, source=source, text=text, access_tag=tag)
    )
    chunk = Chunk(
        chunk_id=document_id + "_c0",
        document_id=document_id,
        document_title=title,
        source=source,
        access_tag=tag,
        chunk_index=0,
        text=text,
    )
    vector = embedder.embed([text]).vectors[0]
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return store.add_chunk(chunk, vector, content_hash)


def test_vector_search_ranks_the_relevant_chunk_first(store, embedder):
    add_one(store, embedder, "d1", "Refunds", "refunds.md", "public",
            "Customers may request a refund within 30 days of delivery.")
    add_one(store, embedder, "d2", "Dog", "dog.md", "public",
            "The office dog is a labrador named Biscuit.")

    query = embedder.embed(["refund within how many days"]).vectors[0]
    results = store.vector_search(query, top_k=2, allowed_tags=["public"])

    assert results[0][0].document_title == "Refunds"
    assert results[0][1] > results[1][1]


def test_keyword_search_finds_an_exact_number(store, embedder):
    """Vector search is weak at exact strings. This is why BM25 exists."""
    add_one(store, embedder, "d1", "Shipping", "ship.md", "public",
            "Express delivery costs 12.99 dollars.")
    add_one(store, embedder, "d2", "Other", "other.md", "public",
            "Standard delivery is free above fifty dollars.")

    results = store.keyword_search(to_stems("express 12.99"), top_k=2, allowed_tags=["public"])
    assert len(results) > 0
    assert results[0][0].document_title == "Shipping"


def test_a_caller_cannot_see_a_forbidden_tag(store, embedder):
    add_one(store, embedder, "d1", "Salaries", "salary.md", "secret",
            "Engineer level three earns between 90000 and 120000.")

    query = embedder.embed(["engineer salary band level three"]).vectors[0]

    assert store.vector_search(query, top_k=5, allowed_tags=["public"]) == []
    assert store.keyword_search(to_stems("engineer salary"), top_k=5, allowed_tags=["public"]) == []

    allowed = store.vector_search(query, top_k=5, allowed_tags=["public", "secret"])
    assert len(allowed) == 1


def test_an_empty_tag_list_returns_nothing(store, embedder):
    """A caller with no permissions must see nothing, not everything."""
    add_one(store, embedder, "d1", "Anything", "a.md", "public", "Some public text about refunds.")
    query = embedder.embed(["refunds"]).vectors[0]
    assert store.vector_search(query, top_k=5, allowed_tags=[]) == []


def test_identical_chunks_are_deduplicated(store, embedder):
    text = "Refunds are issued within 30 days."
    first = add_one(store, embedder, "d1", "A", "a.md", "public", text)
    second = add_one(store, embedder, "d2", "B", "b.md", "public", text)

    assert first is True
    assert second is False
    assert store.count_chunks() == 1


def test_the_same_text_under_a_different_tag_is_not_a_duplicate(store, embedder):
    """
    Public text and secret text that happen to read the same are two different
    records, because different people may see them.
    """
    text = "The retention period is seven years."
    store.add_document(Document(document_id="d1", title="A", source="a.md", text=text, access_tag="public"))
    store.add_document(Document(document_id="d2", title="B", source="b.md", text=text, access_tag="secret"))

    from rag_assistant.layer4_ingestion.step4_pipeline import make_content_hash

    vector = embedder.embed([text]).vectors[0]
    first = store.add_chunk(
        Chunk(chunk_id="c1", document_id="d1", document_title="A", source="a.md",
              access_tag="public", chunk_index=0, text=text),
        vector, make_content_hash(text, "public"))
    second = store.add_chunk(
        Chunk(chunk_id="c2", document_id="d2", document_title="B", source="b.md",
              access_tag="secret", chunk_index=0, text=text),
        vector, make_content_hash(text, "secret"))

    assert first is True
    assert second is True


def test_deleting_a_document_removes_its_chunks(store, embedder):
    add_one(store, embedder, "d1", "Refunds", "refunds.md", "public", "Refunds within 30 days please.")
    assert store.count_chunks() == 1

    removed = store.delete_document("d1")
    assert removed == 1
    assert store.count_chunks() == 0

    # The inverted index must be cleaned up too, or deleted text keeps matching.
    assert store.keyword_search(to_stems("refunds"), top_k=5, allowed_tags=["public"]) == []


def test_listing_documents_respects_permissions(store, embedder):
    add_one(store, embedder, "d1", "Public", "p.md", "public", "Public text about shipping times.")
    add_one(store, embedder, "d2", "Secret", "s.md", "secret", "Secret text about salary bands.")

    assert len(store.list_documents(["public"])) == 1
    assert len(store.list_documents(["public", "secret"])) == 2


def test_the_index_guard_blocks_a_changed_embedder(store, embedder, ingestion):
    """
    The trap this prevents: you paste an API key, restart, and every stored
    vector silently becomes meaningless. Nothing crashes - search still returns
    five passages, they are just the wrong five.
    """
    from pathlib import Path

    import pytest

    from rag_assistant.layer3_storage.index_guard import (
        IndexMismatchError,
        check_index_matches_embedder,
    )

    samples = Path(__file__).resolve().parents[1] / "samples"
    ingestion.ingest_file(samples / "refund_policy.md", access_tag="public")

    class ADifferentEmbedder:
        name = "text-embedding-3-small"
        dimensions = 1536

    with pytest.raises(IndexMismatchError) as error:
        check_index_matches_embedder(store, ADifferentEmbedder())

    assert "reindex" in str(error.value)


def test_the_same_embedder_passes_the_guard(store, embedder, ingestion):
    from pathlib import Path

    from rag_assistant.layer3_storage.index_guard import check_index_matches_embedder

    samples = Path(__file__).resolve().parents[1] / "samples"
    ingestion.ingest_file(samples / "refund_policy.md", access_tag="public")

    # Must not raise.
    check_index_matches_embedder(store, embedder)


def test_an_empty_index_adopts_whichever_embedder_arrives(store):
    """Nothing indexed means nothing to invalidate."""
    from rag_assistant.layer3_storage.index_guard import check_index_matches_embedder

    class ADifferentEmbedder:
        name = "text-embedding-3-small"
        dimensions = 1536

    check_index_matches_embedder(store, ADifferentEmbedder())
    assert store.read_index_fingerprint() == "text-embedding-3-small:1536"


def test_a_document_keeps_its_original_text_for_reindexing(store, ingestion):
    from pathlib import Path

    samples = Path(__file__).resolve().parents[1] / "samples"
    result = ingestion.ingest_file(samples / "refund_policy.md", access_tag="public")

    text = store.read_document_text(result.document_id)
    assert "30 days" in text
