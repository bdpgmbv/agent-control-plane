"""
LAYER 3 - STORAGE
=================
One SQLite file holding two very different kinds of thing:

  THE BUSINESS DATA the tools read
      customers, orders, refunds, help articles.
      In a real company these live behind other teams' APIs. Here they are
      tables, so the project runs with no external dependencies - but the tools
      in layer 4 treat them exactly as they would treat a remote service.

  THE AGENT'S OWN RECORDS
      conversations, messages, tickets, approvals, idempotency keys, audit log.

Three tables deserve attention:

  idempotency_keys
      Stops a retried refund from paying twice. The single most expensive bug an
      agent that can move money is capable of.

  approvals
      A held action waiting for a human. The agent cannot proceed past one.

  audit_log
      Append-only. Every tool call, allowed or denied, forever.
"""

import json
import sqlite3
from pathlib import Path

SCHEMA_STATEMENTS = [
    # ---------- business data ----------
    """
    CREATE TABLE IF NOT EXISTS customers (
        customer_id TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        email       TEXT NOT NULL,
        phone       TEXT NOT NULL,
        tier        TEXT NOT NULL DEFAULT 'standard',
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        order_id          TEXT PRIMARY KEY,
        customer_id       TEXT NOT NULL,
        status            TEXT NOT NULL,
        items_json        TEXT NOT NULL,
        total_amount      REAL NOT NULL,
        currency          TEXT NOT NULL DEFAULT 'USD',
        placed_at         TEXT NOT NULL,
        expected_delivery TEXT NOT NULL DEFAULT '',
        delivered_at      TEXT NOT NULL DEFAULT '',
        tracking_number   TEXT NOT NULL DEFAULT '',
        carrier           TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS refunds (
        refund_id    TEXT PRIMARY KEY,
        order_id     TEXT NOT NULL,
        customer_id  TEXT NOT NULL,
        amount       REAL NOT NULL,
        status       TEXT NOT NULL,
        reason       TEXT NOT NULL DEFAULT '',
        created_at   TEXT NOT NULL,
        completed_at TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS help_articles (
        article_id TEXT PRIMARY KEY,
        title      TEXT NOT NULL,
        body       TEXT NOT NULL,
        tags       TEXT NOT NULL DEFAULT ''
    )
    """,
    # ---------- the agent's own records ----------
    """
    CREATE TABLE IF NOT EXISTS conversations (
        conversation_id TEXT PRIMARY KEY,
        customer_id     TEXT NOT NULL,
        started_at      TEXT NOT NULL,
        last_active_at  TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'open',
        turn_count      INTEGER NOT NULL DEFAULT 0,
        tool_failures   INTEGER NOT NULL DEFAULT 0,
        summary         TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        message_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL,
        position        INTEGER NOT NULL,
        role            TEXT NOT NULL,
        content         TEXT NOT NULL DEFAULT '',
        tool_calls_json TEXT NOT NULL DEFAULT '[]',
        tool_call_id    TEXT NOT NULL DEFAULT '',
        tool_name       TEXT NOT NULL DEFAULT '',
        created_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tickets (
        ticket_id       TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        customer_id     TEXT NOT NULL,
        category        TEXT NOT NULL,
        priority        TEXT NOT NULL DEFAULT 'normal',
        summary         TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'open',
        assigned_to     TEXT NOT NULL DEFAULT 'unassigned',
        created_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS approvals (
        approval_id     TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        customer_id     TEXT NOT NULL,
        tool_name       TEXT NOT NULL,
        arguments_json  TEXT NOT NULL,
        reason          TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'pending',
        requested_at    TEXT NOT NULL,
        decided_at      TEXT NOT NULL DEFAULT '',
        decided_by      TEXT NOT NULL DEFAULT '',
        note            TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS idempotency_keys (
        idempotency_key TEXT PRIMARY KEY,
        tool_name       TEXT NOT NULL,
        request_hash    TEXT NOT NULL,
        response_json   TEXT NOT NULL,
        created_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        audit_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL DEFAULT '',
        actor           TEXT NOT NULL DEFAULT '',
        customer_id     TEXT NOT NULL DEFAULT '',
        action          TEXT NOT NULL,
        tool_name       TEXT NOT NULL DEFAULT '',
        risk            TEXT NOT NULL DEFAULT '',
        allowed         INTEGER NOT NULL DEFAULT 1,
        outcome         TEXT NOT NULL DEFAULT '',
        detail          TEXT NOT NULL DEFAULT '',
        created_at      TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id)",
    "CREATE INDEX IF NOT EXISTS idx_refunds_order ON refunds(order_id)",
    "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_audit_conversation ON audit_log(conversation_id)",
    "CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status)",
]


class Database:
    """Every read and write the application makes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def initialise(self) -> None:
        cursor = self.connection.cursor()
        for statement in SCHEMA_STATEMENTS:
            cursor.execute(statement)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    # ------------------------------------------------------------------
    #  Business data (what the tools read)
    # ------------------------------------------------------------------

    def get_customer(self, customer_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_order(self, order_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM orders WHERE order_id = ?", (order_id.upper(),)
        ).fetchone()
        if row is None:
            return None

        order = dict(row)
        order["items"] = json.loads(order.pop("items_json"))
        return order

    def list_orders_for_customer(self, customer_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM orders WHERE customer_id = ? ORDER BY placed_at DESC", (customer_id,)
        ).fetchall()

        orders: list[dict] = []
        for row in rows:
            order = dict(row)
            order["items"] = json.loads(order.pop("items_json"))
            orders.append(order)
        return orders

    def get_refund_for_order(self, order_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM refunds WHERE order_id = ? ORDER BY created_at DESC LIMIT 1",
            (order_id.upper(),),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def insert_refund(self, refund: dict) -> None:
        self.connection.execute(
            """
            INSERT INTO refunds
                (refund_id, order_id, customer_id, amount, status, reason, created_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                refund["refund_id"],
                refund["order_id"],
                refund["customer_id"],
                refund["amount"],
                refund["status"],
                refund.get("reason", ""),
                refund["created_at"],
                refund.get("completed_at", ""),
            ),
        )
        self.connection.commit()

    def count_refunds(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS total FROM refunds").fetchone()
        return row["total"]

    def search_help_articles(self, query_words: list[str], limit: int = 3) -> list[dict]:
        """Score articles by how many of the query's words they contain."""
        rows = self.connection.execute("SELECT * FROM help_articles").fetchall()

        scored: list[tuple[float, dict]] = []
        for row in rows:
            article = dict(row)
            haystack = (article["title"] + " " + article["body"] + " " + article["tags"]).lower()

            hits = 0
            for word in query_words:
                if word in haystack:
                    hits = hits + 1

            if hits > 0:
                scored.append((hits / max(1, len(query_words)), article))

        scored.sort(key=lambda pair: pair[0], reverse=True)

        results: list[dict] = []
        for score, article in scored[:limit]:
            article["score"] = round(score, 3)
            results.append(article)
        return results

    # ------------------------------------------------------------------
    #  Conversations and messages
    # ------------------------------------------------------------------

    def create_conversation(self, conversation_id: str, customer_id: str, when: str) -> None:
        self.connection.execute(
            """
            INSERT INTO conversations (conversation_id, customer_id, started_at, last_active_at)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, customer_id, when, when),
        )
        self.connection.commit()

    def get_conversation(self, conversation_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def update_conversation(
        self,
        conversation_id: str,
        when: str,
        turn_count: int | None = None,
        tool_failures: int | None = None,
        status: str | None = None,
        summary: str | None = None,
    ) -> None:
        current = self.get_conversation(conversation_id)
        if current is None:
            return

        if turn_count is None:
            turn_count = current["turn_count"]
        if tool_failures is None:
            tool_failures = current["tool_failures"]
        if status is None:
            status = current["status"]
        if summary is None:
            summary = current["summary"]

        self.connection.execute(
            """
            UPDATE conversations
            SET last_active_at = ?, turn_count = ?, tool_failures = ?, status = ?, summary = ?
            WHERE conversation_id = ?
            """,
            (when, turn_count, tool_failures, status, summary, conversation_id),
        )
        self.connection.commit()

    def append_message(self, conversation_id: str, message) -> None:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(position), -1) AS last FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        position = row["last"] + 1

        calls: list[dict] = []
        for call in message.tool_calls:
            calls.append(call.model_dump())

        self.connection.execute(
            """
            INSERT INTO messages
                (conversation_id, position, role, content, tool_calls_json,
                 tool_call_id, tool_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                position,
                message.role,
                message.content,
                json.dumps(calls),
                message.tool_call_id,
                message.tool_name,
                message.created_at,
            ),
        )
        self.connection.commit()

    def read_messages(self, conversation_id: str) -> list:
        from support_agent.layer2_models.schemas import Message, ToolCall

        rows = self.connection.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY position",
            (conversation_id,),
        ).fetchall()

        messages: list = []
        for row in rows:
            calls: list[ToolCall] = []
            for raw in json.loads(row["tool_calls_json"]):
                calls.append(ToolCall(**raw))

            messages.append(
                Message(
                    role=row["role"],
                    content=row["content"],
                    tool_calls=calls,
                    tool_call_id=row["tool_call_id"],
                    tool_name=row["tool_name"],
                    created_at=row["created_at"],
                )
            )
        return messages

    # ------------------------------------------------------------------
    #  Tickets
    # ------------------------------------------------------------------

    def insert_ticket(self, ticket: dict) -> None:
        self.connection.execute(
            """
            INSERT INTO tickets
                (ticket_id, conversation_id, customer_id, category, priority,
                 summary, status, assigned_to, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket["ticket_id"],
                ticket["conversation_id"],
                ticket["customer_id"],
                ticket["category"],
                ticket.get("priority", "normal"),
                ticket["summary"],
                ticket.get("status", "open"),
                ticket.get("assigned_to", "unassigned"),
                ticket["created_at"],
            ),
        )
        self.connection.commit()

    def list_tickets(self, customer_id: str = "") -> list[dict]:
        if customer_id == "":
            rows = self.connection.execute(
                "SELECT * FROM tickets ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM tickets WHERE customer_id = ? ORDER BY created_at DESC",
                (customer_id,),
            ).fetchall()

        tickets: list[dict] = []
        for row in rows:
            tickets.append(dict(row))
        return tickets

    def count_tickets(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS total FROM tickets").fetchone()
        return row["total"]

    # ------------------------------------------------------------------
    #  Approvals
    # ------------------------------------------------------------------

    def insert_approval(self, approval: dict) -> None:
        self.connection.execute(
            """
            INSERT INTO approvals
                (approval_id, conversation_id, customer_id, tool_name,
                 arguments_json, reason, status, requested_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                approval["approval_id"],
                approval["conversation_id"],
                approval["customer_id"],
                approval["tool_name"],
                json.dumps(approval["arguments"]),
                approval["reason"],
                approval["requested_at"],
            ),
        )
        self.connection.commit()

    def get_approval(self, approval_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        if row is None:
            return None

        approval = dict(row)
        approval["arguments"] = json.loads(approval.pop("arguments_json"))
        return approval

    def decide_approval(self, approval_id: str, approved: bool, decided_by: str, when: str, note: str) -> None:
        if approved:
            status = "approved"
        else:
            status = "rejected"

        self.connection.execute(
            """
            UPDATE approvals
            SET status = ?, decided_at = ?, decided_by = ?, note = ?
            WHERE approval_id = ?
            """,
            (status, when, decided_by, note, approval_id),
        )
        self.connection.commit()

    def list_approvals(self, status: str = "") -> list[dict]:
        if status == "":
            rows = self.connection.execute(
                "SELECT * FROM approvals ORDER BY requested_at DESC"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM approvals WHERE status = ? ORDER BY requested_at DESC", (status,)
            ).fetchall()

        approvals: list[dict] = []
        for row in rows:
            approval = dict(row)
            approval["arguments"] = json.loads(approval.pop("arguments_json"))
            approvals.append(approval)
        return approvals

    # ------------------------------------------------------------------
    #  Idempotency
    # ------------------------------------------------------------------

    def read_idempotent_response(self, key: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM idempotency_keys WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def store_idempotent_response(
        self, key: str, tool_name: str, request_hash: str, response_json: str, when: str
    ) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO idempotency_keys
                (idempotency_key, tool_name, request_hash, response_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (key, tool_name, request_hash, response_json, when),
        )
        self.connection.commit()

    # ------------------------------------------------------------------
    #  Audit log (append only)
    # ------------------------------------------------------------------

    def append_audit(self, record) -> None:
        if record.allowed:
            allowed_flag = 1
        else:
            allowed_flag = 0

        self.connection.execute(
            """
            INSERT INTO audit_log
                (conversation_id, actor, customer_id, action, tool_name,
                 risk, allowed, outcome, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.conversation_id,
                record.actor,
                record.customer_id,
                record.action,
                record.tool_name,
                record.risk,
                allowed_flag,
                record.outcome,
                record.detail,
                record.created_at,
            ),
        )
        self.connection.commit()

    def read_audit(self, conversation_id: str = "", limit: int = 200) -> list[dict]:
        if conversation_id == "":
            rows = self.connection.execute(
                "SELECT * FROM audit_log ORDER BY audit_id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM audit_log WHERE conversation_id = ? ORDER BY audit_id LIMIT ?",
                (conversation_id, limit),
            ).fetchall()

        records: list[dict] = []
        for row in rows:
            record = dict(row)
            record["allowed"] = record["allowed"] == 1
            records.append(record)
        return records

    def count_audit(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS total FROM audit_log").fetchone()
        return row["total"]

    # ------------------------------------------------------------------
    #  Housekeeping
    # ------------------------------------------------------------------

    def reset_agent_data(self) -> None:
        """Clear conversations and everything the agent wrote. Business data stays."""
        for table in ["messages", "conversations", "tickets", "approvals",
                      "idempotency_keys", "audit_log"]:
            self.connection.execute("DELETE FROM " + table)
        self.connection.commit()

    def reset_everything(self) -> None:
        for table in ["messages", "conversations", "tickets", "approvals",
                      "idempotency_keys", "audit_log", "refunds", "orders",
                      "customers", "help_articles"]:
            self.connection.execute("DELETE FROM " + table)
        self.connection.commit()


_database: Database | None = None


def get_database() -> Database:
    """The shared database. Opened once."""
    global _database
    if _database is None:
        from support_agent.layer1_config.settings import settings

        _database = Database(settings.sqlite_file())
        _database.initialise()
    return _database


def set_database(database: Database | None) -> None:
    """Replace the shared database. Used by the tests."""
    global _database
    _database = database
