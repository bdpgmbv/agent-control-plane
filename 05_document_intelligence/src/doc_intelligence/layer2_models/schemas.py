"""
LAYER 2 - DATA SHAPES
=====================
The objects every layer agrees on.

`ExtractedField` carries the design of this whole project. A field is never just
a value - it carries where the value came from, whether it appears verbatim in
the document, which component produced it, and how much it can be trusted.

Confidence built from those facts is worth something. Confidence obtained by
asking a model how sure it is describes how fluent its answer felt.
"""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


def now_utc_text() -> str:
    return datetime.now(UTC).isoformat()


class DocumentType(str, Enum):
    INVOICE = "invoice"
    RECEIPT = "receipt"
    PURCHASE_ORDER = "purchase_order"
    CONTRACT = "contract"
    UNKNOWN = "unknown"


class FieldSource(str, Enum):
    """
    Where a value came from. This is not bookkeeping - it decides how much the
    value is trusted, and it is the difference between a pipeline you can debug
    and one you cannot.
    """

    RULES = "rules"          # a pattern matched the document text
    MODEL = "model"          # a model read it
    COMPUTED = "computed"    # arithmetic, from other fields
    CORRECTED = "corrected"  # a human fixed it


class Severity(str, Enum):
    ERROR = "error"          # the document cannot be trusted as extracted
    WARNING = "warning"      # worth a look, not necessarily wrong
    INFO = "info"            # recorded, not a problem


class ExtractedField(BaseModel):
    """One field, and everything known about how it got here."""

    name: str
    value: str = ""                  # normalised: ISO dates, plain numbers
    raw_value: str = ""              # exactly as it appeared in the document
    source: FieldSource = FieldSource.MODEL
    confidence: float = 0.0
    evidence: str = ""               # the surrounding text it was read from
    found_verbatim: bool = False     # does the raw value actually appear in the document?
    note: str = ""

    def is_present(self) -> bool:
        return self.value.strip() != ""


class LineItem(BaseModel):
    """One row of an invoice or order."""

    description: str = ""
    quantity: float = 1.0
    unit_price: float = 0.0
    line_total: float = 0.0
    raw_line: str = ""

    def computed_total(self) -> float:
        return round(self.quantity * self.unit_price, 2)


class ValidationIssue(BaseModel):
    """Something a deterministic check found."""

    code: str
    severity: Severity
    message: str
    field: str = ""
    expected: str = ""
    actual: str = ""

    def short(self) -> str:
        return "[%s] %s" % (self.severity.value, self.message)


class Decision(str, Enum):
    AUTO_APPROVE = "auto_approve"
    NEEDS_REVIEW = "needs_review"
    REJECT = "reject"


class ProcessResult(BaseModel):
    """Everything the pipeline produced for one document."""

    document_id: str
    filename: str = ""
    document_type: DocumentType = DocumentType.UNKNOWN
    type_confidence: float = 0.0
    type_reason: str = ""

    fields: list[ExtractedField] = Field(default_factory=list)
    line_items: list[LineItem] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)

    confidence: float = 0.0
    confidence_explanation: list[str] = Field(default_factory=list)
    decision: Decision = Decision.NEEDS_REVIEW
    decision_reasons: list[str] = Field(default_factory=list)

    # The cleaned text the extraction actually ran on. Kept for two reasons: the
    # review screen shows it beside the extracted fields so a person can check a
    # value against the page without opening the original, and re-validating a
    # corrected document needs the text back (the duplicate check reads it).
    document_text: str = ""
    notes: list[str] = Field(default_factory=list)

    structured: dict = Field(default_factory=dict)   # the clean JSON output
    characters: int = 0
    seconds: float = 0.0
    model_calls: int = 0
    cost_usd: float = 0.0
    created_at: str = Field(default_factory=now_utc_text)

    def field_named(self, name: str) -> ExtractedField | None:
        for field in self.fields:
            if field.name == name:
                return field
        return None

    def value_of(self, name: str) -> str:
        field = self.field_named(name)
        if field is None:
            return ""
        return field.value

    def errors(self) -> list[ValidationIssue]:
        found: list[ValidationIssue] = []
        for issue in self.issues:
            if issue.severity == Severity.ERROR:
                found.append(issue)
        return found

    def warnings(self) -> list[ValidationIssue]:
        found: list[ValidationIssue] = []
        for issue in self.issues:
            if issue.severity == Severity.WARNING:
                found.append(issue)
        return found


# =============================================================
#  Human review
# =============================================================

class ReviewItem(BaseModel):
    """A document waiting for a person."""

    review_id: str
    document_id: str
    filename: str = ""
    document_type: str = ""
    confidence: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    issue_count: int = 0
    status: str = "pending"          # pending | approved | corrected | rejected
    created_at: str = Field(default_factory=now_utc_text)
    decided_at: str = ""
    decided_by: str = ""
    corrections: dict = Field(default_factory=dict)


class ReviewDecision(BaseModel):
    """What a person decided."""

    review_id: str
    action: str                       # approve | correct | reject
    corrections: dict = Field(default_factory=dict)
    note: str = ""


# =============================================================
#  API shapes
# =============================================================

class ProcessTextRequest(BaseModel):
    text: str
    filename: str = "pasted.txt"


class BatchSummary(BaseModel):
    """How a run of documents went. The numbers a finance team would watch."""

    processed: int = 0
    auto_approved: int = 0
    needs_review: int = 0
    rejected: int = 0
    by_type: dict = Field(default_factory=dict)
    total_errors: int = 0
    total_warnings: int = 0
    straight_through_rate: float = 0.0
