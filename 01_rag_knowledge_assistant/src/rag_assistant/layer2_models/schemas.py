"""
LAYER 2 - DATA SHAPES
=====================
The objects that travel between layers, and the JSON shapes the API accepts
and returns.

Why this is its own layer:
    When every layer agrees on the same small set of objects, you can change
    one layer without breaking the others. These classes are the contract.

Nothing in this file talks to a database, a model, or the network.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field


def now_utc_text() -> str:
    """Current time as text, e.g. 2026-09-24T10:30:00+00:00."""
    return datetime.now(UTC).isoformat()


# =============================================================
#  Internal objects (used between layers)
# =============================================================

class Document(BaseModel):
    """A whole source file after loading, before it is cut into chunks."""

    document_id: str
    title: str
    source: str                      # file name or URL
    text: str
    access_tag: str = "public"       # used by RBAC: public / internal / secret
    metadata: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_utc_text)


class Chunk(BaseModel):
    """One retrievable piece of a document."""

    chunk_id: str
    document_id: str
    document_title: str
    source: str
    access_tag: str
    chunk_index: int                 # 0, 1, 2 ... position inside the document
    text: str
    metadata: dict = Field(default_factory=dict)


class ScoredChunk(BaseModel):
    """A chunk plus how relevant we think it is, and why."""

    chunk: Chunk
    score: float = 0.0               # the fused score, after step 4
    vector_rank: int | None = None   # position in the vector search results
    keyword_rank: int | None = None  # position in the keyword search results
    vector_score: float | None = None    # RAW cosine similarity, 0.0 to 1.0
    keyword_score: float | None = None   # RAW BM25 score, unbounded
    rerank_score: float | None = None
    reason: str = ""                 # short human explanation, for debugging

    # Why keep the raw scores as well as the ranks?
    #   A rank tells you "this was the best match we found".
    #   A raw score tells you "and it was actually a good match" - or not.
    # Only the raw score can answer "does the knowledge base contain this at
    # all?", which is what makes an honest "I don't know" possible.

    def label(self) -> str:
        """Short name used in the citation list shown to the user."""
        return f"{self.chunk.document_title}#{self.chunk.chunk_index}"


class Citation(BaseModel):
    """One source the answer actually used."""

    marker: int                      # the [1] / [2] shown inside the answer
    chunk_id: str
    document_title: str
    source: str
    quote: str                       # the snippet the answer leaned on
    score: float


class RetrievalTrace(BaseModel):
    """
    A record of what retrieval did. This is what makes the system debuggable:
    you can see every stage, not just the final answer.
    """

    original_query: str
    rewritten_queries: list[str] = Field(default_factory=list)
    vector_hits: int = 0
    keyword_hits: int = 0
    merged_hits: int = 0
    reranked_hits: int = 0
    dropped_below_threshold: int = 0
    filters_applied: dict = Field(default_factory=dict)
    stage_timings_ms: dict = Field(default_factory=dict)


# =============================================================
#  API request shapes
# =============================================================

class IngestTextRequest(BaseModel):
    """Add one document by pasting its text."""

    title: str
    text: str
    source: str = "pasted-text"
    access_tag: str = "public"
    metadata: dict = Field(default_factory=dict)


class AskRequest(BaseModel):
    """Ask the assistant a question."""

    question: str
    top_k: int | None = None
    filter_access_tags: list[str] | None = None   # narrow to certain tags
    filter_sources: list[str] | None = None       # narrow to certain files
    use_cache: bool = True


# =============================================================
#  API response shapes
# =============================================================

class UsageReport(BaseModel):
    """Cost and latency for one request. The numbers a production team watches."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    embedding_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    latency_ms: int = 0
    cache_hit: bool = False
    # Where the tokens and the money actually went: rewrite, embedding,
    # rerank, answer. One number per request hides which stage is expensive.
    by_stage: dict = Field(default_factory=dict)


class AnswerResponse(BaseModel):
    """The full answer payload returned by POST /ask."""

    question: str
    answer: str
    answered: bool                   # False means the assistant abstained
    abstain_reason: str = ""
    confidence: float = 0.0
    citations: list[Citation] = Field(default_factory=list)
    groundedness: float | None = None
    usage: UsageReport = Field(default_factory=UsageReport)
    trace: RetrievalTrace | None = None
    request_id: str = ""


class IngestResponse(BaseModel):
    """Result of adding a document."""

    document_id: str
    title: str
    chunks_created: int
    chunks_skipped_as_duplicate: int
    access_tag: str


class DocumentSummary(BaseModel):
    """One row in the document list shown in the UI."""

    document_id: str
    title: str
    source: str
    access_tag: str
    chunk_count: int
    created_at: str
