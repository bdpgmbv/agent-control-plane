"""
LAYER 3 - STORAGE: SQLITE BACKEND (the default)
===============================================
One file on disk, no server to install. Everything the production backend does,
it does too - it just does it on a smaller scale.

Three tables:

    documents    - one row per source file
    chunks       - one row per retrievable passage, including its vector
    chunk_terms  - the INVERTED INDEX: which passages contain which word

The inverted index is how real search engines work. Instead of reading every
passage to find the word "refund", you look the word up and it tells you which
passages contain it.

Honest limit: vector search here loads the candidate vectors and compares them
in Python. That is fine up to roughly tens of thousands of chunks. Past that,
switch STORAGE_BACKEND to postgres, which compares them inside the database.
"""

import json
import sqlite3
from pathlib import Path

from rag_assistant.layer0_shared.embeddings import cosine_similarity
from rag_assistant.layer2_models.schemas import Chunk, Document, DocumentSummary
from rag_assistant.layer3_storage.base import DocumentStore
from rag_assistant.layer3_storage.bm25 import bm25_score

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS documents (
        document_id   TEXT PRIMARY KEY,
        title         TEXT NOT NULL,
        source        TEXT NOT NULL,
        access_tag    TEXT NOT NULL,
        metadata_json TEXT NOT NULL,
        created_at    TEXT NOT NULL,
        text          TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chunks (
        chunk_id       TEXT PRIMARY KEY,
        document_id    TEXT NOT NULL,
        document_title TEXT NOT NULL,
        source         TEXT NOT NULL,
        access_tag     TEXT NOT NULL,
        chunk_index    INTEGER NOT NULL,
        text           TEXT NOT NULL,
        content_hash   TEXT NOT NULL,
        metadata_json  TEXT NOT NULL,
        vector_json    TEXT NOT NULL,
        term_length    INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chunk_terms (
        term       TEXT NOT NULL,
        chunk_id   TEXT NOT NULL,
        term_count INTEGER NOT NULL,
        PRIMARY KEY (term, chunk_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_tag ON chunks(access_tag)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(content_hash)",
    "CREATE INDEX IF NOT EXISTS idx_terms_term ON chunk_terms(term)",
    """
    CREATE TABLE IF NOT EXISTS index_metadata (
        name  TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
]


def build_tag_filter(allowed_tags: list[str], table_prefix: str = "") -> tuple[str, list[str]]:
    """
    Build the SQL that restricts rows to the tags this caller may see.

    This is the RBAC enforcement point. It is pushed down into the query rather
    than filtered afterwards, so a document the caller may not see is never even
    loaded into memory.

    table_prefix is used when the query joins two tables and the column needs
    to be written as "chunks.access_tag".
    """
    if len(allowed_tags) == 0:
        # No tags allowed means no rows at all. "1=0" is always false.
        return ("1=0", [])

    placeholders: list[str] = []
    for _tag in allowed_tags:
        placeholders.append("?")

    clause = table_prefix + "access_tag IN (" + ", ".join(placeholders) + ")"
    return (clause, list(allowed_tags))


def build_source_filter(sources: list[str] | None, table_prefix: str = "") -> tuple[str, list[str]]:
    """Optionally restrict to certain source files."""
    if sources is None or len(sources) == 0:
        return ("1=1", [])

    placeholders: list[str] = []
    for _source in sources:
        placeholders.append("?")

    clause = table_prefix + "source IN (" + ", ".join(placeholders) + ")"
    return (clause, list(sources))


class SqliteDocumentStore(DocumentStore):
    """The default storage backend."""

    name = "sqlite"

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row

    # ------------------------------------------------------------------
    #  Setup
    # ------------------------------------------------------------------

    def initialise(self) -> None:
        cursor = self.connection.cursor()
        for statement in SCHEMA_STATEMENTS:
            cursor.execute(statement)
        self.connection.commit()

    # ------------------------------------------------------------------
    #  Writing
    # ------------------------------------------------------------------

    def add_document(self, document: Document) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO documents
                (document_id, title, source, access_tag, metadata_json, created_at, text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.document_id,
                document.title,
                document.source,
                document.access_tag,
                json.dumps(document.metadata),
                document.created_at,
                document.text,
            ),
        )
        self.connection.commit()

    def add_chunk(self, chunk: Chunk, vector: list[float], content_hash: str) -> bool:
        from rag_assistant.layer0_shared.text_tools import to_stems

        stems = to_stems(chunk.text)

        # Count how many times each word appears in this chunk.
        term_counts: dict[str, int] = {}
        for stem in stems:
            if stem not in term_counts:
                term_counts[stem] = 0
            term_counts[stem] = term_counts[stem] + 1

        try:
            self.connection.execute(
                """
                INSERT INTO chunks
                    (chunk_id, document_id, document_title, source, access_tag,
                     chunk_index, text, content_hash, metadata_json, vector_json, term_length)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.chunk_id,
                    chunk.document_id,
                    chunk.document_title,
                    chunk.source,
                    chunk.access_tag,
                    chunk.chunk_index,
                    chunk.text,
                    content_hash,
                    json.dumps(chunk.metadata),
                    json.dumps(vector),
                    len(stems),
                ),
            )
        except sqlite3.IntegrityError:
            # The unique index on content_hash rejected it: we already have this
            # exact passage. Deduplication, enforced by the database.
            return False

        for term in term_counts:
            self.connection.execute(
                "INSERT OR REPLACE INTO chunk_terms (term, chunk_id, term_count) VALUES (?, ?, ?)",
                (term, chunk.chunk_id, term_counts[term]),
            )

        self.connection.commit()
        return True

    # ------------------------------------------------------------------
    #  Reading: vector search
    # ------------------------------------------------------------------

    def row_to_chunk(self, row: sqlite3.Row) -> Chunk:
        return Chunk(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            document_title=row["document_title"],
            source=row["source"],
            access_tag=row["access_tag"],
            chunk_index=row["chunk_index"],
            text=row["text"],
            metadata=json.loads(row["metadata_json"]),
        )

    def vector_search(
        self,
        query_vector: list[float],
        top_k: int,
        allowed_tags: list[str],
        sources: list[str] | None = None,
    ) -> list[tuple[Chunk, float]]:
        tag_clause, tag_values = build_tag_filter(allowed_tags)
        source_clause, source_values = build_source_filter(sources)

        query = f"SELECT * FROM chunks WHERE {tag_clause} AND {source_clause}"
        rows = self.connection.execute(query, tag_values + source_values).fetchall()

        scored: list[tuple[Chunk, float]] = []
        for row in rows:
            stored_vector = json.loads(row["vector_json"])
            similarity = cosine_similarity(query_vector, stored_vector)
            scored.append((self.row_to_chunk(row), similarity))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]

    # ------------------------------------------------------------------
    #  Reading: keyword search
    # ------------------------------------------------------------------

    def keyword_search(
        self,
        query_stems: list[str],
        top_k: int,
        allowed_tags: list[str],
        sources: list[str] | None = None,
    ) -> list[tuple[Chunk, float]]:
        if len(query_stems) == 0:
            return []

        # Remove duplicates while keeping the order stable.
        unique_stems: list[str] = []
        for stem in query_stems:
            if stem not in unique_stems:
                unique_stems.append(stem)

        term_placeholders: list[str] = []
        for _stem in unique_stems:
            term_placeholders.append("?")
        term_clause = "chunk_terms.term IN (" + ", ".join(term_placeholders) + ")"

        tag_clause, tag_values = build_tag_filter(allowed_tags, table_prefix="chunks.")
        source_clause, source_values = build_source_filter(sources, table_prefix="chunks.")

        # Step 1: use the inverted index to find candidate chunks and the
        # per-term counts we need for BM25.
        query = f"""
            SELECT chunk_terms.term, chunk_terms.term_count, chunks.*
            FROM chunk_terms
            JOIN chunks ON chunks.chunk_id = chunk_terms.chunk_id
            WHERE {term_clause}
              AND {tag_clause}
              AND {source_clause}
        """
        rows = self.connection.execute(
            query, unique_stems + tag_values + source_values
        ).fetchall()

        if len(rows) == 0:
            return []

        # Step 2: how many chunks (in total, not just candidates) contain each term.
        chunks_containing_term: dict[str, int] = {}
        for stem in unique_stems:
            count_row = self.connection.execute(
                "SELECT COUNT(*) AS total FROM chunk_terms WHERE term = ?", (stem,)
            ).fetchone()
            chunks_containing_term[stem] = count_row["total"]

        total_row = self.connection.execute(
            "SELECT COUNT(*) AS total, AVG(term_length) AS average FROM chunks"
        ).fetchone()
        total_chunks = total_row["total"]
        average_length = total_row["average"]
        if average_length is None:
            average_length = 1.0

        # Step 3: group the candidate rows by chunk.
        per_chunk_terms: dict[str, dict[str, int]] = {}
        per_chunk_row: dict[str, sqlite3.Row] = {}
        for row in rows:
            chunk_id = row["chunk_id"]
            if chunk_id not in per_chunk_terms:
                per_chunk_terms[chunk_id] = {}
                per_chunk_row[chunk_id] = row
            per_chunk_terms[chunk_id][row["term"]] = row["term_count"]

        # Step 4: score each candidate chunk.
        scored: list[tuple[Chunk, float]] = []
        for chunk_id in per_chunk_terms:
            row = per_chunk_row[chunk_id]
            score = bm25_score(
                term_counts_in_chunk=per_chunk_terms[chunk_id],
                chunk_length=row["term_length"],
                average_chunk_length=average_length,
                total_chunks=total_chunks,
                chunks_containing_term=chunks_containing_term,
            )
            scored.append((self.row_to_chunk(row), score))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]

    # ------------------------------------------------------------------
    #  Housekeeping
    # ------------------------------------------------------------------

    def list_documents(self, allowed_tags: list[str]) -> list[DocumentSummary]:
        tag_clause, tag_values = build_tag_filter(allowed_tags)
        rows = self.connection.execute(
            f"SELECT * FROM documents WHERE {tag_clause} ORDER BY created_at DESC",
            tag_values,
        ).fetchall()

        summaries: list[DocumentSummary] = []
        for row in rows:
            count_row = self.connection.execute(
                "SELECT COUNT(*) AS total FROM chunks WHERE document_id = ?",
                (row["document_id"],),
            ).fetchone()
            summaries.append(
                DocumentSummary(
                    document_id=row["document_id"],
                    title=row["title"],
                    source=row["source"],
                    access_tag=row["access_tag"],
                    chunk_count=count_row["total"],
                    created_at=row["created_at"],
                )
            )
        return summaries

    def delete_document(self, document_id: str) -> int:
        chunk_rows = self.connection.execute(
            "SELECT chunk_id FROM chunks WHERE document_id = ?", (document_id,)
        ).fetchall()

        for row in chunk_rows:
            self.connection.execute("DELETE FROM chunk_terms WHERE chunk_id = ?", (row["chunk_id"],))

        self.connection.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        self.connection.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
        self.connection.commit()
        return len(chunk_rows)

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        row = self.connection.execute(
            "SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()
        if row is None:
            return None
        return self.row_to_chunk(row)

    def read_document_text(self, document_id: str) -> str:
        row = self.connection.execute(
            "SELECT text FROM documents WHERE document_id = ?", (document_id,)
        ).fetchone()
        if row is None:
            return ""
        return row["text"]

    def read_index_fingerprint(self) -> str:
        row = self.connection.execute(
            "SELECT value FROM index_metadata WHERE name = 'embedder_fingerprint'"
        ).fetchone()
        if row is None:
            return ""
        return row["value"]

    def write_index_fingerprint(self, fingerprint: str) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO index_metadata (name, value) VALUES ('embedder_fingerprint', ?)",
            (fingerprint,),
        )
        self.connection.commit()

    def document_frequencies(self, terms: list[str]) -> dict[str, int]:
        """Read straight off the inverted index, which is exactly what it is for."""
        frequencies: dict[str, int] = {}
        for term in terms:
            if term in frequencies:
                continue
            row = self.connection.execute(
                "SELECT COUNT(*) AS total FROM chunk_terms WHERE term = ?", (term,)
            ).fetchone()
            frequencies[term] = row["total"]
        return frequencies

    def count_chunks(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS total FROM chunks").fetchone()
        return row["total"]

    def reset(self) -> None:
        self.connection.execute("DELETE FROM chunk_terms")
        self.connection.execute("DELETE FROM chunks")
        self.connection.execute("DELETE FROM documents")
        self.connection.execute("DELETE FROM index_metadata")
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
