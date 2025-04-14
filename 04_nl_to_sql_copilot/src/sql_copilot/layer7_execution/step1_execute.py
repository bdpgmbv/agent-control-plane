"""
LAYER 7 - EXECUTION: RUNNING THE QUERY IN A SANDBOX
===================================================
The query has passed validation. It can still ruin your afternoon.

A perfectly valid SELECT can join four tables with no usable condition and try to
materialise sixty million rows. Nothing in the SQL looks wrong. It is simply
expensive, and the only thing that stops it is a limit on the running query.

Three controls, all of them enforced while the query runs:

  READ-ONLY CONNECTION   the database refuses writes regardless of what the SQL
                         says. Opened in layer 3.

  A TIMEOUT              SQLite's progress handler runs every few thousand
                         virtual-machine instructions and can abort the query.
                         This is a real interrupt, not a wrapper that gives up
                         waiting while the query carries on burning the server.

  A ROW CAP              fetched one row past the limit, so we can honestly say
                         "there were more" rather than silently truncating.

The distinction between a repairable and a permanent error matters too. "no such
column: totl" is worth showing the model, which usually fixes it. "database is
locked" is not - retrying it just wastes a model call on something the model
cannot do anything about.
"""

import sqlite3
import time

from sql_copilot.layer0_shared.logging_setup import get_logger, log_event
from sql_copilot.layer0_shared.metrics import metrics
from sql_copilot.layer1_config.settings import settings
from sql_copilot.layer2_models.schemas import QueryResult

log = get_logger(__name__)

# Errors the model can plausibly fix if we show it the message.
REPAIRABLE_ERROR_FRAGMENTS = [
    "no such column",
    "no such table",
    "no such function",
    "syntax error",
    "ambiguous column name",
    "wrong number of arguments",
    "misuse of aggregate",
    "group by",
    "sub-select returns",
    "datatype mismatch",
]

# Errors that are about the environment, not the query.
PERMANENT_ERROR_FRAGMENTS = [
    "attempt to write a readonly database",
    "database is locked",
    "interrupted",
    "too many",
]


def error_is_repairable(message: str) -> bool:
    lowered = message.lower()

    for fragment in PERMANENT_ERROR_FRAGMENTS:
        if fragment in lowered:
            return False

    for fragment in REPAIRABLE_ERROR_FRAGMENTS:
        if fragment in lowered:
            return True

    # Unknown errors are worth one attempt. The repair budget is small.
    return True


class QueryTimeout:
    """
    Aborts a query that has run too long.

    SQLite calls the handler every `instructions` virtual-machine steps. Returning
    a non-zero value aborts the statement from inside the engine, which is the
    only way to stop a query that is already running - a timeout in Python would
    simply stop waiting while the query carried on.
    """

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.started = 0.0
        self.fired = False

    def start(self) -> None:
        self.started = time.monotonic()
        self.fired = False

    def handler(self) -> int:
        if time.monotonic() - self.started > self.seconds:
            self.fired = True
            return 1        # non-zero aborts
        return 0


def execute(database, sql: str, max_rows: int | None = None) -> QueryResult:
    """Run the query under a timeout and a row cap."""
    if max_rows is None:
        max_rows = settings.max_rows

    timeout = QueryTimeout(settings.query_timeout_seconds)
    started = time.perf_counter()

    connection = database.connection
    # Called roughly every few milliseconds of work.
    connection.set_progress_handler(timeout.handler, 2000)
    timeout.start()

    try:
        cursor = connection.execute(sql)

        column_names: list[str] = []
        if cursor.description is not None:
            for column in cursor.description:
                column_names.append(column[0])

        # One more than the cap, so truncation can be reported honestly.
        fetched = cursor.fetchmany(max_rows + 1)

        truncated = len(fetched) > max_rows
        if truncated:
            fetched = fetched[:max_rows]

        rows: list[list] = []
        for row in fetched:
            values: list = []
            for value in row:
                values.append(value)
            rows.append(values)

        seconds = round(time.perf_counter() - started, 4)

        metrics.increment("queries_executed_total")
        metrics.observe("query_seconds", seconds)
        metrics.observe("rows_returned", len(rows))

        log_event(
            log,
            "query.executed",
            seconds=seconds,
            rows=len(rows),
            truncated=truncated,
            sql=sql[:200],
        )

        return QueryResult(
            ok=True,
            columns=column_names,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            seconds=seconds,
        )

    except sqlite3.Error as error:
        seconds = round(time.perf_counter() - started, 4)
        message = str(error)

        if timeout.fired:
            message = (
                "The query was stopped after %.1f seconds. It is doing too much work - "
                "try narrowing it with a date range or a filter."
                % settings.query_timeout_seconds
            )
            metrics.increment("queries_timed_out_total")
            repairable = True
        else:
            metrics.increment("queries_failed_total")
            repairable = error_is_repairable(message)

        log_event(log, "query.failed", error=message[:200], seconds=seconds, sql=sql[:200])

        return QueryResult(
            ok=False,
            error=message,
            error_is_repairable=repairable,
            seconds=seconds,
        )

    finally:
        connection.set_progress_handler(None, 0)
