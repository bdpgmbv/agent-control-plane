"""
LAYER 5 - GENERATION, STEP 1: WRITING THE SQL
=============================================
Ask the model for SQL and get a usable string back.

Most of this file is cleaning up the reply, which is not glamorous but is where
a surprising share of NL-to-SQL failures actually live. A model told "reply with
only the SQL" will still, some of the time:

    wrap it in ```sql fences
    add "Here is the query:" first
    add an explanation after it
    end it with a semicolon
    return CANNOT_ANSWER wrapped in prose

Handling those here means the validator sees SQL rather than markdown, and a
refusal for "does not start with SELECT" means what it says instead of meaning
"the model added a sentence".
"""

import re

from sql_copilot.layer0_shared.llm_client import TASK_GENERATE, TASK_REPAIR
from sql_copilot.layer0_shared.logging_setup import get_logger, log_event
from sql_copilot.layer5_generation.prompts import (
    REPAIR_SYSTEM_PROMPT,
    SQL_SYSTEM_PROMPT,
    build_generation_prompt,
    build_repair_prompt,
)

log = get_logger(__name__)

CANNOT_ANSWER = "CANNOT_ANSWER"

FENCE_PATTERN = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
SQL_START_PATTERN = re.compile(r"\b(SELECT|WITH)\b", re.IGNORECASE)


def clean_sql_reply(text: str) -> str:
    """Pull the SQL out of whatever the model actually sent."""
    stripped = text.strip()
    if stripped == "":
        return ""

    if CANNOT_ANSWER in stripped.upper():
        return CANNOT_ANSWER

    # A fenced block, if there is one.
    fenced = FENCE_PATTERN.search(stripped)
    if fenced is not None:
        stripped = fenced.group(1).strip()

    # Drop any preamble before the first SELECT or WITH.
    start = SQL_START_PATTERN.search(stripped)
    if start is not None and start.start() > 0:
        stripped = stripped[start.start() :]

    # Drop a trailing explanation. A blank line after the statement is the usual
    # separator; anything after it that does not look like SQL is commentary.
    parts = stripped.split("\n\n")
    if len(parts) > 1:
        first = parts[0].strip()
        if SQL_START_PATTERN.search(first) is not None:
            stripped = first

    while stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()

    return stripped.strip()


def generate_sql(question: str, schema_text: str, model, usage) -> str:
    """Ask for SQL. Returns "" when nothing usable came back."""
    result = model.complete(
        system_prompt=SQL_SYSTEM_PROMPT,
        user_prompt=build_generation_prompt(question, schema_text),
        task=TASK_GENERATE,
        max_tokens=600,
    )
    usage.add("generate", result)

    sql = clean_sql_reply(result.text)
    log_event(log, "sql.generated", question=question[:80], sql=sql[:200])
    return sql


def repair_sql(question: str, schema_text: str, failed_sql: str, error: str, model, usage) -> str:
    """Show the model its own error and ask for a fix."""
    result = model.complete(
        system_prompt=REPAIR_SYSTEM_PROMPT,
        user_prompt=build_repair_prompt(question, schema_text, failed_sql, error),
        task=TASK_REPAIR,
        max_tokens=600,
    )
    usage.add("repair", result)

    sql = clean_sql_reply(result.text)
    log_event(log, "sql.repair_attempt", error=error[:120], sql=sql[:200])
    return sql
