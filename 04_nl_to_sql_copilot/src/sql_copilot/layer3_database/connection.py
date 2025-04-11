"""
LAYER 3 - DATABASE: CONNECTING, READ-ONLY
=========================================
Two connections, and the difference between them is the whole point of this file.

    build_writable_connection()   used ONCE, by the seed script, to create the
                                  warehouse. Never reachable from a request.

    build_readonly_connection()   used by everything else. The database itself
                                  refuses to write through it.

------------------------------------------------------------------------------
WHY BOTH THIS AND THE VALIDATOR
------------------------------------------------------------------------------
Layer 6 reads the SQL and refuses anything that is not a plain SELECT. This layer
opens the database in a mode where writes are impossible regardless.

They are not redundant. They fail differently:

    the validator   is a few hundred lines of my reasoning about SQL grammar.
                    If I have missed a syntax, a dialect quirk, or a function
                    with a side effect, it lets that through.

    read-only mode  is enforced by SQLite and PostgreSQL, which have had rather
                    more scrutiny than my tokeniser.

The validator exists to give a clear, early, explainable refusal. Read-only mode
exists because the validator might be wrong. Neither is a reason to skip the
other, and anyone proposing to drop one should be asked which failure they are
confident cannot happen.
"""

import sqlite3
from pathlib import Path

from sql_copilot.layer0_shared.logging_setup import get_logger, log_event
from sql_copilot.layer1_config.settings import settings

log = get_logger(__name__)


def build_writable_connection(path: Path) -> sqlite3.Connection:
    """
    A writable connection. Used only by the seed script.

    Deliberately awkward to reach: nothing in the request path imports it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    return connection


def build_readonly_connection(path: Path) -> sqlite3.Connection:
    """
    A connection SQLite will not let anything write through.

    `mode=ro` in the URI is the real control. An INSERT on this connection raises
    "attempt to write a readonly database" from SQLite itself, not from any code
    in this project.
    """
    if not path.exists():
        raise FileNotFoundError(
            "The analytics database does not exist at %s. Run: make seed" % path
        )

    uri = "file:" + str(path) + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
    connection.row_factory = sqlite3.Row

    # Belt and braces: ask SQLite to refuse writes at the statement level too.
    try:
        connection.execute("PRAGMA query_only = ON")
    except sqlite3.Error:
        # Older builds may not have it. mode=ro is the control that matters.
        pass

    return connection


class ReadOnlyDatabase:
    """The only database handle anything above layer 3 ever sees."""

    def __init__(self, connection: sqlite3.Connection, name: str = "sqlite") -> None:
        self.connection = connection
        self.name = name

    def list_table_names(self) -> list[str]:
        rows = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()

        names: list[str] = []
        for row in rows:
            names.append(row["name"])
        return names

    def describe_table(self, table_name: str) -> list[dict]:
        """Column information straight from the database."""
        rows = self.connection.execute("PRAGMA table_info(%s)" % table_name).fetchall()

        columns: list[dict] = []
        for row in rows:
            columns.append(
                {
                    "name": row["name"],
                    "data_type": row["type"],
                    "is_primary_key": row["pk"] == 1,
                }
            )
        return columns

    def foreign_keys(self, table_name: str) -> dict[str, str]:
        """Column name -> "table.column" it points at."""
        rows = self.connection.execute("PRAGMA foreign_key_list(%s)" % table_name).fetchall()

        references: dict[str, str] = {}
        for row in rows:
            references[row["from"]] = row["table"] + "." + row["to"]
        return references

    def count_rows(self, table_name: str) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS total FROM %s" % table_name).fetchone()
        return row["total"]

    def close(self) -> None:
        self.connection.close()


_database: ReadOnlyDatabase | None = None


def get_database() -> ReadOnlyDatabase:
    global _database
    if _database is None:
        connection = build_readonly_connection(settings.sqlite_file())
        _database = ReadOnlyDatabase(connection)
        log_event(log, "database.opened", path=str(settings.sqlite_file()), mode="read-only")
    return _database


def set_database(database: ReadOnlyDatabase | None) -> None:
    """Used by the tests."""
    global _database
    _database = database
