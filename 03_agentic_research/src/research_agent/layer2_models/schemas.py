"""
LAYER 2 - DATA SHAPES
=====================
The objects that travel between layers.

Three of them carry most of the design:

  Evidence   one specific claim, from one specific place, with the quote that
             supports it. Not "a document" - a document is a container, and a
             report cites claims. Every field exists so the finished report can
             be traced back to a sentence someone actually wrote.

  Conflict   two pieces of evidence that disagree. Kept as a first-class object
             rather than averaged away, because "sources disagree, here is both"
             is the honest answer and the useful one.

  Report     the deliverable, with `partial` and `gaps` so a run that ran out of
             budget says so instead of quietly looking complete.
"""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


def now_utc_text() -> str:
    return datetime.now(UTC).isoformat()


# =============================================================
#  Sources
# =============================================================

class SourceType(str, Enum):
    """
    Where something came from, which is most of what credibility means.

    Not a judgement about truth - a peer reviewed paper can be wrong and a blog
    post can be right. It is a prior, applied consistently, and shown to the
    reader so they can disagree with it.
    """

    PEER_REVIEWED = "peer_reviewed"
    GOVERNMENT = "government"
    INDUSTRY_REPORT = "industry_report"
    NEWS = "news"
    COMPANY_BLOG = "company_blog"
    FORUM = "forum"


# How much weight each source type carries. Visible, arguable, and in one place.
CREDIBILITY_BY_SOURCE_TYPE: dict[str, float] = {
    SourceType.PEER_REVIEWED.value: 1.00,
    SourceType.GOVERNMENT.value: 0.92,
    SourceType.INDUSTRY_REPORT.value: 0.70,
    SourceType.NEWS.value: 0.60,
    SourceType.COMPANY_BLOG.value: 0.40,
    SourceType.FORUM.value: 0.20,
}


class SourceDocument(BaseModel):
    """One document a search can return."""

    document_id: str
    title: str
    source_name: str
    source_type: SourceType
    published_date: str            # YYYY-MM-DD
    url: str
    body: str
    topics: list[str] = Field(default_factory=list)

    def credibility(self) -> float:
        return CREDIBILITY_BY_SOURCE_TYPE.get(self.source_type.value, 0.3)


class SearchHit(BaseModel):
    """A document a search matched, with how well it matched."""

    document: SourceDocument
    relevance: float = 0.0          # rescaled against the best hit: 0.0 to 1.0
    absolute_score: float = 0.0     # the raw BM25 score, not rescaled
    matched_terms: list[str] = Field(default_factory=list)

    # WHY BOTH.
    # `relevance` is rescaled so scores are comparable between searches, which
    # matters because evidence from different sub-questions is ranked against
    # each other later. But rescaling means the best hit is ALWAYS 1.0 - even
    # when it is terrible - so a relative score can never say "nothing here
    # matches". Only `absolute_score` and `matched_terms` can answer that.


# =============================================================
#  Planning
# =============================================================

class SubQuestionStatus(str, Enum):
    PLANNED = "planned"
    RESEARCHED = "researched"
    SKIPPED_NO_BUDGET = "skipped_no_budget"
    NO_EVIDENCE = "no_evidence"
    FAILED = "failed"


class SubQuestion(BaseModel):
    """One piece of the original question."""

    sub_question_id: str
    text: str
    depth: int = 1
    parent_id: str = ""
    status: SubQuestionStatus = SubQuestionStatus.PLANNED
    note: str = ""

    # Filled in by the worker.
    evidence_ids: list[str] = Field(default_factory=list)
    searches_run: list[str] = Field(default_factory=list)
    worker_seconds: float = 0.0


class ResearchPlan(BaseModel):
    """How the question was broken up, and why."""

    question: str
    sub_questions: list[SubQuestion] = Field(default_factory=list)
    strategy: str = ""
    planner_note: str = ""
    created_at: str = Field(default_factory=now_utc_text)

    def researched_count(self) -> int:
        total = 0
        for sub_question in self.sub_questions:
            if sub_question.status == SubQuestionStatus.RESEARCHED:
                total = total + 1
        return total


# =============================================================
#  Evidence
# =============================================================

class Evidence(BaseModel):
    """
    One claim, from one place, with the sentence that supports it.

    `quote` is not decoration. Without it a reader cannot check the claim, and a
    "cited" report where the citation points at a whole document is barely
    better than no citation at all.
    """

    evidence_id: str
    sub_question_id: str
    claim: str                      # the finding, in one sentence
    quote: str                      # the words in the source that support it
    document_id: str
    title: str
    source_name: str
    source_type: SourceType
    published_date: str
    url: str

    relevance: float = 0.0          # how well it answers the sub-question
    credibility: float = 0.0        # from the source type
    recency: float = 0.0            # newer is worth more, gently
    score: float = 0.0              # the three combined

    duplicate_of: str = ""          # set when another item says the same thing
    corroborated_by: list[str] = Field(default_factory=list)

    def is_duplicate(self) -> bool:
        return self.duplicate_of != ""


class Conflict(BaseModel):
    """Two pieces of evidence that disagree about the same thing."""

    conflict_id: str
    topic: str
    evidence_id_a: str
    evidence_id_b: str
    claim_a: str
    claim_b: str
    source_a: str
    source_b: str
    explanation: str = ""

    def stronger_side(self, evidence_by_id: dict) -> str:
        """Which side carries more weight. Reported, never used to hide the other."""
        first = evidence_by_id.get(self.evidence_id_a)
        second = evidence_by_id.get(self.evidence_id_b)
        if first is None or second is None:
            return ""
        if first.score >= second.score:
            return self.evidence_id_a
        return self.evidence_id_b


# =============================================================
#  The report
# =============================================================

class ReportSection(BaseModel):
    """One sub-question, answered."""

    sub_question: str
    status: SubQuestionStatus
    findings: str = ""
    citation_markers: list[int] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    note: str = ""


class Citation(BaseModel):
    """One numbered source in the finished report."""

    marker: int
    evidence_id: str
    title: str
    source_name: str
    source_type: str
    published_date: str
    url: str
    quote: str
    credibility: float


class Report(BaseModel):
    """The deliverable."""

    question: str
    summary: str = ""
    sections: list[ReportSection] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)

    # Honesty fields. A run that ran out of budget must say so.
    partial: bool = False
    gaps: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    confidence_reason: str = ""

    created_at: str = Field(default_factory=now_utc_text)


# =============================================================
#  Tracing
# =============================================================

class StageTrace(BaseModel):
    """What one stage of the run did."""

    name: str
    seconds: float = 0.0
    model_calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    detail: str = ""


class RunTrace(BaseModel):
    """Everything that happened, for the UI and for debugging."""

    run_id: str
    question: str
    stages: list[StageTrace] = Field(default_factory=list)
    budget: dict = Field(default_factory=dict)
    documents_seen: int = 0
    evidence_collected: int = 0
    evidence_after_dedupe: int = 0
    duplicates_removed: int = 0
    conflicts_found: int = 0
    workers_run: int = 0
    workers_skipped: int = 0


# =============================================================
#  API shapes
# =============================================================

class ResearchRequest(BaseModel):
    """Ask a research question."""

    question: str
    max_subquestions: int | None = None
    max_seconds: float | None = None
    max_tokens: int | None = None

    # Tool calls are charged identically whether or not a model is configured,
    # so this is the budget that can be tested without an API key.
    max_tool_calls: int | None = None


class ResearchResponse(BaseModel):
    """The finished run."""

    run_id: str
    question: str
    plan: ResearchPlan
    report: Report
    trace: RunTrace
    seconds: float = 0.0
    cost_usd: float = 0.0
