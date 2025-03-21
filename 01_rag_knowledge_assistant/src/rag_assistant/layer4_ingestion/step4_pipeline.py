"""
LAYER 4 - INGESTION, STEP 4: THE PIPELINE
=========================================
Runs the previous three steps in order and writes the result to storage:

    load  ->  clean  ->  chunk  ->  embed  ->  store

Two production details that tutorials skip:

  DEDUPLICATION
      The same paragraph often appears in many files (a boilerplate legal
      notice, a repeated FAQ answer). If you index it five times it crowds out
      five other passages from the top results. We hash the chunk text and let
      the database reject repeats.

  ACCESS TAGS
      Every chunk carries the tag of the document it came from, so the retrieval
      layer can filter by permission without a second lookup.
"""

import hashlib
import time
from pathlib import Path

from rag_assistant.layer0_shared.logging_setup import get_logger, log_event
from rag_assistant.layer0_shared.metrics import metrics
from rag_assistant.layer0_shared.text_tools import collapse_whitespace
from rag_assistant.layer1_config.settings import settings
from rag_assistant.layer2_models.schemas import Chunk, Document, IngestResponse
from rag_assistant.layer3_storage.base import DocumentStore
from rag_assistant.layer3_storage.index_guard import check_index_matches_embedder
from rag_assistant.layer4_ingestion.step1_load import LoadedFile, load_file, load_from_text
from rag_assistant.layer4_ingestion.step2_clean import clean_text
from rag_assistant.layer4_ingestion.step3_chunk import chunk_document

log = get_logger(__name__)


def make_document_id(source: str, title: str) -> str:
    """
    A stable id for a document, derived from its name.

    Stable means: ingesting the same file twice updates the same row instead of
    creating a second copy.
    """
    raw = source.strip().lower() + "||" + title.strip().lower()
    return "doc_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def make_content_hash(chunk_text: str, access_tag: str) -> str:
    """
    The fingerprint used for deduplication.

    The access tag is part of it on purpose: the same sentence published to
    "public" and to "secret" is genuinely two different records, because they
    are visible to different people.
    """
    normalised = collapse_whitespace(chunk_text).lower()
    raw = normalised + "||" + access_tag
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class IngestionPipeline:
    """Adds documents to the knowledge base."""

    def __init__(self, store: DocumentStore, embedder) -> None:
        self.store = store
        self.embedder = embedder

    def ingest_loaded_file(
        self,
        loaded: LoadedFile,
        access_tag: str = "public",
        extra_metadata: dict | None = None,
    ) -> IngestResponse:
        started = time.perf_counter()

        # Refuse to mix vectors from two different embedding models.
        check_index_matches_embedder(self.store, self.embedder)

        if extra_metadata is None:
            extra_metadata = {}

        # --- step 2: clean ---
        cleaned = clean_text(loaded.text)
        if cleaned == "":
            raise ValueError(f"'{loaded.source}' produced no readable text after cleaning.")

        document_id = make_document_id(loaded.source, loaded.title)

        document_metadata = dict(extra_metadata)
        document_metadata["character_count"] = len(cleaned)
        if loaded.page_count > 0:
            document_metadata["page_count"] = loaded.page_count

        document = Document(
            document_id=document_id,
            title=loaded.title,
            source=loaded.source,
            text=cleaned,
            access_tag=access_tag,
            metadata=document_metadata,
        )
        self.store.add_document(document)

        # --- step 3: chunk ---
        text_chunks = chunk_document(
            cleaned,
            size_words=settings.chunk_size_words,
            overlap_words=settings.chunk_overlap_words,
        )
        if len(text_chunks) == 0:
            raise ValueError(f"'{loaded.source}' produced no chunks. Is the file empty?")

        # --- step 4a: embed, all chunks in one batch ---
        texts_to_embed: list[str] = []
        for text_chunk in text_chunks:
            texts_to_embed.append(text_chunk.text)

        embedding_result = self.embedder.embed(texts_to_embed)

        # --- step 4b: store, skipping duplicates ---
        stored_count = 0
        skipped_count = 0
        position = 0

        while position < len(text_chunks):
            text_chunk = text_chunks[position]
            vector = embedding_result.vectors[position]

            chunk_metadata = dict(extra_metadata)
            chunk_metadata["heading"] = text_chunk.heading
            chunk_metadata["word_count"] = len(text_chunk.text.split())

            chunk = Chunk(
                chunk_id=f"{document_id}_c{text_chunk.index}",
                document_id=document_id,
                document_title=loaded.title,
                source=loaded.source,
                access_tag=access_tag,
                chunk_index=text_chunk.index,
                text=text_chunk.text,
                metadata=chunk_metadata,
            )

            was_stored = self.store.add_chunk(
                chunk=chunk,
                vector=vector,
                content_hash=make_content_hash(text_chunk.text, access_tag),
            )
            if was_stored:
                stored_count = stored_count + 1
            else:
                skipped_count = skipped_count + 1

            position = position + 1

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        metrics.increment("documents_ingested_total")
        metrics.increment("chunks_stored_total", stored_count)
        metrics.increment("chunks_deduplicated_total", skipped_count)
        metrics.increment("embedding_tokens_total", embedding_result.tokens)
        metrics.observe("ingest_latency_ms", elapsed_ms)

        log_event(
            log,
            "ingest.finished",
            document_id=document_id,
            title=loaded.title,
            chunks_stored=stored_count,
            chunks_skipped=skipped_count,
            embedding_tokens=embedding_result.tokens,
            latency_ms=elapsed_ms,
        )

        return IngestResponse(
            document_id=document_id,
            title=loaded.title,
            chunks_created=stored_count,
            chunks_skipped_as_duplicate=skipped_count,
            access_tag=access_tag,
        )

    def ingest_text(
        self,
        title: str,
        text: str,
        source: str = "pasted-text",
        access_tag: str = "public",
        extra_metadata: dict | None = None,
    ) -> IngestResponse:
        loaded = load_from_text(title=title, text=text, source=source)
        return self.ingest_loaded_file(loaded, access_tag=access_tag, extra_metadata=extra_metadata)

    def ingest_file(
        self,
        path: Path,
        access_tag: str = "public",
        extra_metadata: dict | None = None,
    ) -> IngestResponse:
        loaded = load_file(path)
        return self.ingest_loaded_file(loaded, access_tag=access_tag, extra_metadata=extra_metadata)

    def ingest_folder(self, folder: Path, access_tag: str = "public") -> list[IngestResponse]:
        """Ingest every supported file in a folder. Used by the seed script."""
        from rag_assistant.layer4_ingestion.step1_load import SUPPORTED_EXTENSIONS

        results: list[IngestResponse] = []
        for path in sorted(folder.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            results.append(self.ingest_file(path, access_tag=access_tag))
        return results
