"""
LAYER 8, STEP 1 - WHERE RESULTS LIVE
====================================
SQLite, three tables, and one index that is the whole point of the file.

    documents     one row per processed document, the full result as JSON
    reviews       the queue of documents waiting for a person
    audit         append-only record of every decision, human or automatic

The index on (supplier, reference) is what makes the duplicate check in layer 6
fast enough to run on every document. Without it the check still works and still
finds duplicates - it just reads every row that has ever been processed, and a
check that gets slower as the business grows is a check somebody will eventually
switch off.

The audit table is append-only on purpose. It is the only place that can answer
"who approved this payment, when, and what did the pipeline think at the time?",
and a record you can edit cannot answer that question.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from doc_intelligence.layer2_models.schemas import ProcessResult, ReviewItem
from doc_intelligence.layer6_validate.step5_duplicates import SeenDocument

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id     TEXT PRIMARY KEY,
    filename        TEXT NOT NULL DEFAULT '',
    document_type   TEXT NOT NULL DEFAULT '',
    reference       TEXT NOT NULL DEFAULT '',
    supplier        TEXT NOT NULL DEFAULT '',
    total           TEXT NOT NULL DEFAULT '',
    document_date   TEXT NOT NULL DEFAULT '',
    iban            TEXT NOT NULL DEFAULT '',
    confidence      REAL NOT NULL DEFAULT 0,
    decision        TEXT NOT NULL DEFAULT '',
    error_count     INTEGER NOT NULL DEFAULT 0,
    warning_count   INTEGER NOT NULL DEFAULT 0,
    result_json     TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS documents_by_reference
    ON documents (supplier, reference);

CREATE INDEX IF NOT EXISTS documents_by_decision
    ON documents (decision);

CREATE TABLE IF NOT EXISTS reviews (
    review_id       TEXT PRIMARY KEY,
    document_id     TEXT NOT NULL,
    filename        TEXT NOT NULL DEFAULT '',
    document_type   TEXT NOT NULL DEFAULT '',
    confidence      REAL NOT NULL DEFAULT 0,
    reasons_json    TEXT NOT NULL DEFAULT '[]',
    issue_count     INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL,
    decided_at      TEXT NOT NULL DEFAULT '',
    decided_by      TEXT NOT NULL DEFAULT '',
    corrections_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS reviews_by_status ON reviews (status);

CREATE TABLE IF NOT EXISTS audit (
    audit_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    happened_at     TEXT NOT NULL,
    document_id     TEXT NOT NULL DEFAULT '',
    actor           TEXT NOT NULL DEFAULT '',
    action          TEXT NOT NULL DEFAULT '',
    detail_json     TEXT NOT NULL DEFAULT '{}'
);
"""


def now_text() -> str:
    return datetime.now(UTC).isoformat()


class DocumentStore:
    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    # ---------------- documents ----------------

    def save_result(self, result: ProcessResult, reference: str, supplier: str,
                    document_date: str) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO documents
                (document_id, filename, document_type, reference, supplier,
                 total, document_date, iban, confidence, decision, error_count,
                 warning_count, result_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.document_id,
                result.filename,
                result.document_type.value,
                reference,
                supplier,
                result.value_of("total_amount") or result.value_of("contract_value"),
                document_date,
                result.value_of("iban"),
                result.confidence,
                result.decision.value,
                len(result.errors()),
                len(result.warnings()),
                json.dumps(result.model_dump(mode="json")),
                result.created_at,
            ),
        )
        self.connection.commit()

    def load_result(self, document_id: str) -> ProcessResult | None:
        row = self.connection.execute(
            "SELECT result_json FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        if row is None:
            return None
        return ProcessResult(**json.loads(row["result_json"]))

    def all_seen(self, limit: int = 5000) -> list[SeenDocument]:
        """
        Everything already processed, for the duplicate check.

        Rejected documents are excluded: a file that could not be read was never
        really processed, and treating it as "already seen" would block the
        corrected version of the same document from ever getting through.
        """
        rows = self.connection.execute(
            """
            SELECT document_id, filename, reference, supplier, total,
                   document_date, iban, decision
            FROM documents
            WHERE decision != 'reject'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        seen: list[SeenDocument] = []
        for row in rows:
            seen.append(SeenDocument(
                document_id=row["document_id"],
                filename=row["filename"],
                reference=row["reference"],
                supplier=row["supplier"],
                total=row["total"],
                document_date=row["document_date"],
                iban=row["iban"],
                decision=row["decision"],
            ))
        return seen

    def recent_documents(self, limit: int = 50) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT document_id, filename, document_type, reference, supplier,
                   total, confidence, decision, error_count, warning_count,
                   created_at
            FROM documents
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        documents: list[dict] = []
        for row in rows:
            documents.append(dict(row))
        return documents

    def counts_by_decision(self) -> dict:
        rows = self.connection.execute(
            "SELECT decision, COUNT(*) AS how_many FROM documents GROUP BY decision"
        ).fetchall()
        counts = {}
        for row in rows:
            counts[row["decision"]] = row["how_many"]
        return counts

    def clear(self) -> None:
        """Wipe everything. Used by the demo reset button and by the tests."""
        self.connection.execute("DELETE FROM documents")
        self.connection.execute("DELETE FROM reviews")
        self.connection.execute("DELETE FROM audit")
        self.connection.commit()

    # ---------------- review queue ----------------

    def add_review(self, item: ReviewItem) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO reviews
                (review_id, document_id, filename, document_type, confidence,
                 reasons_json, issue_count, status, created_at, decided_at,
                 decided_by, corrections_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.review_id, item.document_id, item.filename,
                item.document_type, item.confidence,
                json.dumps(item.reasons), item.issue_count, item.status,
                item.created_at, item.decided_at, item.decided_by,
                json.dumps(item.corrections),
            ),
        )
        self.connection.commit()

    def review_from_row(self, row) -> ReviewItem:
        return ReviewItem(
            review_id=row["review_id"],
            document_id=row["document_id"],
            filename=row["filename"],
            document_type=row["document_type"],
            confidence=row["confidence"],
            reasons=json.loads(row["reasons_json"]),
            issue_count=row["issue_count"],
            status=row["status"],
            created_at=row["created_at"],
            decided_at=row["decided_at"],
            decided_by=row["decided_by"],
            corrections=json.loads(row["corrections_json"]),
        )

    def get_review(self, review_id: str) -> ReviewItem | None:
        row = self.connection.execute(
            "SELECT * FROM reviews WHERE review_id = ?", (review_id,),
        ).fetchone()
        if row is None:
            return None
        return self.review_from_row(row)

    def pending_reviews(self, limit: int = 100) -> list[ReviewItem]:
        rows = self.connection.execute(
            """
            SELECT * FROM reviews WHERE status = 'pending'
            ORDER BY created_at ASC LIMIT ?
            """,
            (limit,),
        ).fetchall()

        items: list[ReviewItem] = []
        for row in rows:
            items.append(self.review_from_row(row))
        return items

    def update_review(self, item: ReviewItem) -> None:
        self.add_review(item)

    # ---------------- audit ----------------

    def record_audit(self, document_id: str, actor: str, action: str,
                     detail: dict | None = None) -> None:
        if detail is None:
            detail = {}
        self.connection.execute(
            """
            INSERT INTO audit (happened_at, document_id, actor, action, detail_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (now_text(), document_id, actor, action, json.dumps(detail)),
        )
        self.connection.commit()

    def audit_for(self, document_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT happened_at, actor, action, detail_json FROM audit
            WHERE document_id = ? ORDER BY audit_id ASC
            """,
            (document_id,),
        ).fetchall()

        entries: list[dict] = []
        for row in rows:
            entries.append({
                "happened_at": row["happened_at"],
                "actor": row["actor"],
                "action": row["action"],
                "detail": json.loads(row["detail_json"]),
            })
        return entries
