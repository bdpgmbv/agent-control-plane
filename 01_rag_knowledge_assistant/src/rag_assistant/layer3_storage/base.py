"""
LAYER 3 - STORAGE: THE INTERFACE
================================
Every storage backend must provide exactly these methods. Nothing above this
layer knows whether the data sits in SQLite or in PostgreSQL.

That is the whole point: you can learn the system on SQLite with zero setup,
then switch one line in .env to run the production path on pgvector.

Two searches are stored separately on purpose:

    vector_search  - finds passages that MEAN the same thing.
    keyword_search - finds passages that use the same WORDS
                     (exact product codes, error numbers, names).

Layer 5 combines them. Neither alone is good enough.
"""

from abc import ABC, abstractmethod

from rag_assistant.layer2_models.schemas import Chunk, Document, DocumentSummary


class DocumentStore(ABC):
    """The contract every storage backend implements."""

    name: str = "unnamed"

    @abstractmethod
    def initialise(self) -> None:
        """Create tables and indexes if they do not exist yet."""

    @abstractmethod
    def add_document(self, document: Document) -> None:
        """Save the document row (not its chunks)."""

    @abstractmethod
    def add_chunk(self, chunk: Chunk, vector: list[float], content_hash: str) -> bool:
        """
        Save one chunk with its vector.
        Returns True if stored, False if an identical chunk already existed.
        """

    @abstractmethod
    def vector_search(
        self,
        query_vector: list[float],
        top_k: int,
        allowed_tags: list[str],
        sources: list[str] | None = None,
    ) -> list[tuple[Chunk, float]]:
        """Passages closest in meaning. Highest score first."""

    @abstractmethod
    def keyword_search(
        self,
        query_stems: list[str],
        top_k: int,
        allowed_tags: list[str],
        sources: list[str] | None = None,
    ) -> list[tuple[Chunk, float]]:
        """Passages that share vocabulary with the question. Highest score first."""

    @abstractmethod
    def list_documents(self, allowed_tags: list[str]) -> list[DocumentSummary]:
        """Every document the caller is allowed to see."""

    @abstractmethod
    def delete_document(self, document_id: str) -> int:
        """Remove a document and its chunks. Returns how many chunks were removed."""

    @abstractmethod
    def get_chunk(self, chunk_id: str) -> Chunk | None:
        """Fetch one chunk by id."""

    @abstractmethod
    def read_document_text(self, document_id: str) -> str:
        """The original cleaned text of a document, for re-indexing."""

    @abstractmethod
    def read_index_fingerprint(self) -> str:
        """
        Which embedder built the vectors currently stored, e.g.
        "text-embedding-3-small:1536". Empty string when nothing is indexed yet.
        """

    @abstractmethod
    def write_index_fingerprint(self, fingerprint: str) -> None:
        """Record which embedder is building the index."""

    @abstractmethod
    def document_frequencies(self, terms: list[str]) -> dict[str, int]:
        """
        For each word, how many chunks contain it.

        This is what tells the reranker that "policy" is worthless in a corpus of
        policy documents, while "parental" is highly informative. Without it,
        every word counts the same and a question matching one generic word looks
        relevant.
        """

    @abstractmethod
    def count_chunks(self) -> int:
        """Total number of stored chunks."""

    @abstractmethod
    def reset(self) -> None:
        """Delete everything. Used by tests and by the demo reset button."""

    def close(self) -> None:
        """Release connections. Optional for backends that need it."""
        return None
