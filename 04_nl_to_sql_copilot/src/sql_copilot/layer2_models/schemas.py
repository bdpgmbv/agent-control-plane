"""
LAYER 2 - DATA SHAPES
=====================
The objects every layer agrees on.

`ValidationResult` is the one to read. It carries not just "is this allowed?"
but WHICH rule refused and why, because a query that is blocked has to be
explainable to the person who asked - and because a validator you cannot debug
gets loosened until it stops working.
"""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


def now_utc_text() -> str:
    return datetime.now(UTC).isoformat()


# =============================================================
#  The schema of the database being queried
# =============================================================

class ColumnInfo(BaseModel):
    name: str
    data_type: str
    description: str = ""
    is_primary_key: bool = False
    references: str = ""            # "table.column" when it is a foreign key


class TableInfo(BaseModel):
    name: str
    description: str = ""
    columns: list[ColumnInfo] = Field(default_factory=list)
    row_count: int = 0

    def column_names(self) -> list[str]:
        names: list[str] = []
        for column in self.columns:
            names.append(column.name)
        return names

    def to_prompt_text(self) -> str:
        """How this table is described to the model."""
        lines = ["TABLE " + self.name]
        if self.description != "":
            lines.append("  -- " + self.description)

        for column in self.columns:
            parts = ["  " + column.name + " " + column.data_type]
            if column.is_primary_key:
                parts.append("PRIMARY KEY")
            if column.references != "":
                parts.append("REFERENCES " + column.references)
            if column.description != "":
                parts.append("-- " + column.description)
            lines.append(" ".join(parts))

        return "\n".join(lines)


class DatabaseSchema(BaseModel):
    tables: list[TableInfo] = Field(default_factory=list)

    def table_names(self) -> list[str]:
        names: list[str] = []
        for table in self.tables:
            names.append(table.name)
        return names

    def find_table(self, name: str) -> TableInfo | None:
        for table in self.tables:
            if table.name.lower() == name.lower():
                return table
        return None


# =============================================================
#  Validation
# =============================================================

class RefusalReason(str, Enum):
    """Why a query was refused. Each one is a separate rule with its own test."""

    EMPTY = "empty"
    NOT_A_SELECT = "not_a_select"
    MULTIPLE_STATEMENTS = "multiple_statements"
    FORBIDDEN_KEYWORD = "forbidden_keyword"
    UNKNOWN_TABLE = "unknown_table"
    TOO_MANY_JOINS = "too_many_joins"
    UNBALANCED = "unbalanced_syntax"
    SUSPICIOUS_COMMENT = "suspicious_comment"


class ValidationResult(BaseModel):
    """The verdict on one piece of generated SQL."""

    allowed: bool
    sql: str = ""                   # the SQL as it will actually be run
    original_sql: str = ""          # what the model produced
    reason: RefusalReason | None = None
    message: str = ""               # for the person who asked

    tables_used: list[str] = Field(default_factory=list)
    join_count: int = 0
    limit_added: bool = False
    rewrites: list[str] = Field(default_factory=list)

    def short(self) -> str:
        if self.allowed:
            return "allowed"
        return "refused (%s): %s" % (self.reason.value if self.reason else "?", self.message)


# =============================================================
#  Execution
# =============================================================

class QueryResult(BaseModel):
    """What came back from the database."""

    ok: bool
    columns: list[str] = Field(default_factory=list)
    rows: list[list] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False         # more rows existed than we returned
    seconds: float = 0.0
    error: str = ""
    error_is_repairable: bool = False

    def to_markdown(self, limit: int = 20) -> str:
        """A small table, for the explanation prompt and the trace."""
        if not self.ok or len(self.columns) == 0:
            return ""

        lines = [" | ".join(self.columns)]
        lines.append(" | ".join(["---"] * len(self.columns)))

        for row in self.rows[:limit]:
            cells: list[str] = []
            for value in row:
                if value is None:
                    cells.append("")
                else:
                    cells.append(str(value))
            lines.append(" | ".join(cells))

        return "\n".join(lines)


class AttemptTrace(BaseModel):
    """One attempt at answering: generate, validate, execute."""

    attempt: int
    sql: str = ""
    validation: ValidationResult | None = None
    executed: bool = False
    error: str = ""
    seconds: float = 0.0
    repaired_from: str = ""


# =============================================================
#  API shapes
# =============================================================

class AskRequest(BaseModel):
    question: str
    max_rows: int | None = None
    explain: bool = True


class UsageReport(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    model_calls: int = 0
    latency_ms: int = 0
    by_stage: dict = Field(default_factory=dict)


class AskResponse(BaseModel):
    """The answer to one question."""

    question: str
    answered: bool
    answer: str = ""                # the explanation in words
    sql: str = ""                   # the SQL that actually ran
    result: QueryResult | None = None

    refused: bool = False
    refusal_reason: str = ""
    refusal_message: str = ""

    tables_used: list[str] = Field(default_factory=list)
    schema_tables_offered: list[str] = Field(default_factory=list)
    attempts: list[AttemptTrace] = Field(default_factory=list)
    repaired: bool = False

    usage: UsageReport = Field(default_factory=UsageReport)
    request_id: str = ""
    created_at: str = Field(default_factory=now_utc_text)
