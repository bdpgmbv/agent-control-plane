"""
LAYER 3 - STORAGE: POSTGRES + PGVECTOR BACKEND (the production path)
====================================================================
Same interface as the SQLite backend, but the heavy work happens inside the
database instead of in Python:

    vector search  -> the `vector` column type and the `<=>` cosine operator,
                      accelerated by an HNSW index.
    keyword search -> PostgreSQL's built-in full text search (`tsvector`),
                      which is BM25-like and also indexed.

Turn it on with:
    docker compose up -d
    STORAGE_BACKEND=postgres   (in .env)

Read this file next to sqlite_store.py. The methods line up one for one, which
is the clearest way to see what changes when you move to production.
"""

import json

from rag_assistant.layer2_models.schemas import Chunk, Document, DocumentSummary
from rag_assistant.layer3_storage.base import DocumentStore

SCHEMA_STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
    CREATE TABLE IF NOT EXISTS documents (
        document_id   TEXT PRIMARY KEY,
        title         TEXT NOT NULL,
        source        TEXT NOT NULL,
        access_tag    TEXT NOT NULL,
        metadata_json JSONB NOT NULL,
        created_at    TEXT NOT NULL,
        text          TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chunks (
        chunk_id       TEXT PRIMARY KEY,
        document_id    TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
        document_title TEXT NOT NULL,
        source         TEXT NOT NULL,
        access_tag     TEXT NOT NULL,
        chunk_index    INTEGER NOT NULL,
        text           TEXT NOT NULL,
        content_hash   TEXT NOT NULL UNIQUE,
        metadata_json  JSONB NOT NULL,
        embedding      VECTOR(EMBEDDING_DIMENSIONS_PLACEHOLDER),
        search_text    TSVECTOR
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chunks_tag ON chunks(access_tag)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_search ON chunks USING GIN(search_text)",
    # HNSW makes nearest-neighbour search fast at large scale.
    "CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops)",
    "CREATE TABLE IF NOT EXISTS index_metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL)",
]


def vector_to_literal(vector: list[float]) -> str:
    """
    pgvector accepts a text literal like '[0.1,0.2,0.3]'.
    Writing it out this way avoids needing an extra Python package.
    """
    pieces: list[str] = []
    for value in vector:
        pieces.append(repr(float(value)))
    return "[" + ",".join(pieces) + "]"


class PostgresDocumentStore(DocumentStore):
    """Production storage backend."""

    name = "postgres"

    def __init__(self, dsn: str, embedding_dimensions: int) -> None:
        import psycopg

        self.dsn = dsn
        self.embedding_dimensions = embedding_dimensions
        self.connection = psycopg.connect(dsn, autocommit=True)

    def initialise(self) -> None:
        cursor = self.connection.cursor()
        for statement in SCHEMA_STATEMENTS:
            prepared = statement.replace(
                "EMBEDDING_DIMENSIONS_PLACEHOLDER", str(self.embedding_dimensions)
            )
            cursor.execute(prepared)

    # ------------------------------------------------------------------
    #  Writing
    # ------------------------------------------------------------------

    def add_document(self, document: Document) -> None:
        self.connection.execute(
            """
            INSERT INTO documents
                (document_id, title, source, access_tag, metadata_json, created_at, text)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (document_id) DO UPDATE
                SET title = EXCLUDED.title,
                    source = EXCLUDED.source,
                    access_tag = EXCLUDED.access_tag,
                    text = EXCLUDED.text
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

    def add_chunk(self, chunk: Chunk, vector: list[float], content_hash: str) -> bool:
        cursor = self.connection.execute(
            """
            INSERT INTO chunks
                (chunk_id, document_id, document_title, source, access_tag,
                 chunk_index, text, content_hash, metadata_json, embedding, search_text)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, to_tsvector('english', %s))
            ON CONFLICT (content_hash) DO NOTHING
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
                vector_to_literal(vector),
                chunk.text,
            ),
        )
        # rowcount is 0 when ON CONFLICT DO NOTHING skipped a duplicate.
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    #  Reading
    # ------------------------------------------------------------------

    def filter_sql(self, allowed_tags: list[str], sources: list[str] | None) -> tuple[str, list]:
        """Build the WHERE clause that enforces access rules and source filters."""
        if len(allowed_tags) == 0:
            return ("FALSE", [])

        clauses = ["access_tag = ANY(%s)"]
        values: list = [list(allowed_tags)]

        if sources is not None and len(sources) > 0:
            clauses.append("source = ANY(%s)")
            values.append(list(sources))

        return (" AND ".join(clauses), values)

    def row_to_chunk(self, row) -> Chunk:
        metadata = row[7]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return Chunk(
            chunk_id=row[0],
            document_id=row[1],
            document_title=row[2],
            source=row[3],
            access_tag=row[4],
            chunk_index=row[5],
            text=row[6],
            metadata=metadata,
        )

    SELECT_COLUMNS = (
        "chunk_id, document_id, document_title, source, access_tag, "
        "chunk_index, text, metadata_json"
    )

    def vector_search(
        self,
        query_vector: list[float],
        top_k: int,
        allowed_tags: list[str],
        sources: list[str] | None = None,
    ) -> list[tuple[Chunk, float]]:
        where_sql, where_values = self.filter_sql(allowed_tags, sources)

        query = f"""
            SELECT {self.SELECT_COLUMNS}, 1 - (embedding <=> %s::vector) AS similarity
            FROM chunks
            WHERE {where_sql}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        literal = vector_to_literal(query_vector)
        values = [literal] + where_values + [literal, top_k]

        rows = self.connection.execute(query, values).fetchall()

        results: list[tuple[Chunk, float]] = []
        for row in rows:
            results.append((self.row_to_chunk(row), float(row[8])))
        return results

    def keyword_search(
        self,
        query_stems: list[str],
        top_k: int,
        allowed_tags: list[str],
        sources: list[str] | None = None,
    ) -> list[tuple[Chunk, float]]:
        if len(query_stems) == 0:
            return []

        # PostgreSQL's OR query: any of these words may match.
        tsquery_text = " | ".join(query_stems)
        where_sql, where_values = self.filter_sql(allowed_tags, sources)

        query = f"""
            SELECT {self.SELECT_COLUMNS},
                   ts_rank_cd(search_text, to_tsquery('english', %s)) AS rank
            FROM chunks
            WHERE {where_sql}
              AND search_text @@ to_tsquery('english', %s)
            ORDER BY rank DESC
            LIMIT %s
        """
        values = [tsquery_text] + where_values + [tsquery_text, top_k]
        rows = self.connection.execute(query, values).fetchall()

        results: list[tuple[Chunk, float]] = []
        for row in rows:
            results.append((self.row_to_chunk(row), float(row[8])))
        return results

    # ------------------------------------------------------------------
    #  Housekeeping
    # ------------------------------------------------------------------

    def list_documents(self, allowed_tags: list[str]) -> list[DocumentSummary]:
        if len(allowed_tags) == 0:
            return []

        rows = self.connection.execute(
            """
            SELECT d.document_id, d.title, d.source, d.access_tag, d.created_at,
                   COUNT(c.chunk_id) AS chunk_count
            FROM documents d
            LEFT JOIN chunks c ON c.document_id = d.document_id
            WHERE d.access_tag = ANY(%s)
            GROUP BY d.document_id, d.title, d.source, d.access_tag, d.created_at
            ORDER BY d.created_at DESC
            """,
            (list(allowed_tags),),
        ).fetchall()

        summaries: list[DocumentSummary] = []
        for row in rows:
            summaries.append(
                DocumentSummary(
                    document_id=row[0],
                    title=row[1],
                    source=row[2],
                    access_tag=row[3],
                    created_at=row[4],
                    chunk_count=int(row[5]),
                )
            )
        return summaries

    def delete_document(self, document_id: str) -> int:
        count_row = self.connection.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id = %s", (document_id,)
        ).fetchone()
        removed = int(count_row[0]) if count_row is not None else 0
        # The foreign key has ON DELETE CASCADE, so chunks go with the document.
        self.connection.execute("DELETE FROM documents WHERE document_id = %s", (document_id,))
        return removed

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        row = self.connection.execute(
            f"SELECT {self.SELECT_COLUMNS} FROM chunks WHERE chunk_id = %s", (chunk_id,)
        ).fetchone()
        if row is None:
            return None
        return self.row_to_chunk(row)

    def read_document_text(self, document_id: str) -> str:
        row = self.connection.execute(
            "SELECT text FROM documents WHERE document_id = %s", (document_id,)
        ).fetchone()
        if row is None:
            return ""
        return str(row[0])

    def read_index_fingerprint(self) -> str:
        row = self.connection.execute(
            "SELECT value FROM index_metadata WHERE name = 'embedder_fingerprint'"
        ).fetchone()
        if row is None:
            return ""
        return str(row[0])

    def write_index_fingerprint(self, fingerprint: str) -> None:
        self.connection.execute(
            """
            INSERT INTO index_metadata (name, value) VALUES ('embedder_fingerprint', %s)
            ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value
            """,
            (fingerprint,),
        )

    def document_frequencies(self, terms: list[str]) -> dict[str, int]:
        """One indexed count per word. The GIN index makes each of these cheap."""
        frequencies: dict[str, int] = {}
        for term in terms:
            if term in frequencies:
                continue
            row = self.connection.execute(
                "SELECT COUNT(*) FROM chunks WHERE search_text @@ to_tsquery('english', %s)",
                (term,),
            ).fetchone()
            frequencies[term] = int(row[0]) if row is not None else 0
        return frequencies

    def count_chunks(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) FROM chunks").fetchone()
        return int(row[0]) if row is not None else 0

    def reset(self) -> None:
        self.connection.execute("TRUNCATE chunks, documents")
        self.connection.execute("DELETE FROM index_metadata")

    def close(self) -> None:
        self.connection.close()
