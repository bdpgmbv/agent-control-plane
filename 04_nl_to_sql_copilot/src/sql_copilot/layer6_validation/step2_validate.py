"""
LAYER 6 - VALIDATION, STEP 2: DECIDING WHETHER TO RUN IT
========================================================
Nine rules, in order. The query runs only if it passes all of them.

    1. it is not empty
    2. no unterminated string      (the rest of the statement is inside a quote)
    3. parentheses balance
    4. exactly one statement       (no stacked "; DROP TABLE ...")
    5. it starts with SELECT or WITH
    6. no forbidden keyword        (as a whole word, never a substring)
    7. every table is one we know  (an allowlist, not a blocklist)
    8. not more joins than allowed
    9. a LIMIT is present          (added if the model forgot)

------------------------------------------------------------------------------
AN ALLOWLIST, NOT A BLOCKLIST
------------------------------------------------------------------------------
Rule 6 blocks known-dangerous words, and it is the weaker of the two. Blocklists
are guesses about what an attacker will think of, and SQL has a very large
surface: ATTACH, PRAGMA, load_extension, SELECT ... INTO, and whatever the next
dialect adds.

Rule 7 is the one that actually holds. Every table named in the query must be a
table we deliberately exposed. A query that reaches for anything else is refused
whether or not we thought of it in advance. Blocklists fail open; allowlists fail
closed.

------------------------------------------------------------------------------
AND THIS IS STILL NOT THE LAST LINE OF DEFENCE
------------------------------------------------------------------------------
Layer 7 opens the database READ-ONLY. If everything here is wrong, the connection
still refuses to write. Two independent mechanisms, and the second does not trust
the first - because this file is a few hundred lines of my reasoning about SQL,
and the database's read-only mode is not.
"""

from sql_copilot.layer1_config.settings import settings
from sql_copilot.layer2_models.schemas import RefusalReason, ValidationResult
from sql_copilot.layer6_validation.step1_tokenise import (
    TokenKind,
    count_statements,
    parentheses_are_balanced,
    strip_comments,
    tokenise,
)

# Words that must never appear as a bare token in a read-only query.
# Compared whole, so "updated_at" is not "update".
FORBIDDEN_KEYWORDS = {
    # writing
    "insert", "update", "delete", "merge", "upsert", "replace", "truncate",
    # schema changes
    "create", "alter", "drop", "rename", "comment",
    # SELECT ... INTO creates a table in several dialects
    "into",
    # permissions
    "grant", "revoke",
    # transactions and session state
    "begin", "commit", "rollback", "savepoint", "set",
    # database-level and file-level access
    "attach", "detach", "pragma", "vacuum", "reindex", "load_extension",
    "readfile", "writefile", "copy", "outfile", "dumpfile",
    # running other things
    "exec", "execute", "call", "do", "prepare", "deallocate",
}

# Keywords after which the next identifier names a table.
TABLE_INTRODUCERS = {"from", "join"}

# Words that end a FROM clause, so a comma after them is not another table.
CLAUSE_ENDERS = {
    "where", "group", "order", "having", "limit", "offset", "union",
    "intersect", "except", "window", "returning",
}

JOIN_WORDS = {"join"}


def extract_cte_names(tokens) -> list[str]:
    """
    Names defined by a WITH clause.

    A CTE is a table name that exists only inside this query, so it must be
    allowed even though it is in no allowlist. Missing this refuses every
    legitimate query that uses one.
    """
    names: list[str] = []

    position = 0
    while position < len(tokens):
        token = tokens[position]

        if token.kind == TokenKind.WORD and token.word() == "with":
            # WITH name AS ( ... ) [, name AS ( ... )]*
            scan = position + 1

            while scan < len(tokens):
                if tokens[scan].kind == TokenKind.WORD and tokens[scan].word() == "recursive":
                    scan = scan + 1
                    continue

                if tokens[scan].kind != TokenKind.WORD:
                    break

                candidate = tokens[scan].text
                scan = scan + 1

                # An optional column list: name (a, b) AS ( ... )
                if scan < len(tokens) and tokens[scan].text == "(":
                    depth = 0
                    while scan < len(tokens):
                        if tokens[scan].text == "(":
                            depth = depth + 1
                        elif tokens[scan].text == ")":
                            depth = depth - 1
                            if depth == 0:
                                scan = scan + 1
                                break
                        scan = scan + 1

                if scan < len(tokens) and tokens[scan].kind == TokenKind.WORD and tokens[scan].word() == "as":
                    names.append(candidate.lower())
                    scan = scan + 1

                    # Skip the body of the CTE.
                    if scan < len(tokens) and tokens[scan].text == "(":
                        depth = 0
                        while scan < len(tokens):
                            if tokens[scan].text == "(":
                                depth = depth + 1
                            elif tokens[scan].text == ")":
                                depth = depth - 1
                                if depth == 0:
                                    scan = scan + 1
                                    break
                            scan = scan + 1

                    # Another CTE follows only after a comma.
                    if scan < len(tokens) and tokens[scan].text == ",":
                        scan = scan + 1
                        continue
                break

            position = scan
            continue

        position = position + 1

    return names


def extract_tables_and_joins(tokens) -> tuple[list[str], int]:
    """
    Which tables the query reads, and how many joins it uses.

    Three things that are easy to get wrong:
      * `FROM orders o` - `o` is an alias, not a second table
      * `FROM (SELECT ...) t` - a subquery, so there is no table name here
      * `FROM a, b` - an old-style comma join, which is still a join
    """
    tables: list[str] = []
    joins = 0

    position = 0
    while position < len(tokens):
        token = tokens[position]

        if token.kind != TokenKind.WORD:
            position = position + 1
            continue

        word = token.word()

        if word in JOIN_WORDS:
            joins = joins + 1

        if word not in TABLE_INTRODUCERS:
            position = position + 1
            continue

        # Read the table reference (or references, for a comma join).
        scan = position + 1
        reading = True

        while reading and scan < len(tokens):
            reading = False

            if tokens[scan].text == "(":
                # A subquery. Skip to its matching bracket.
                depth = 0
                while scan < len(tokens):
                    if tokens[scan].text == "(":
                        depth = depth + 1
                    elif tokens[scan].text == ")":
                        depth = depth - 1
                        if depth == 0:
                            scan = scan + 1
                            break
                    scan = scan + 1
            elif tokens[scan].kind == TokenKind.WORD:
                # A possibly dotted name: schema.table
                name = tokens[scan].text
                scan = scan + 1

                while scan + 1 < len(tokens) and tokens[scan].text == ".":
                    name = tokens[scan + 1].text
                    scan = scan + 2

                if name.lower() not in tables:
                    tables.append(name.lower())
            else:
                break

            # Skip an alias, with or without AS.
            if scan < len(tokens) and tokens[scan].kind == TokenKind.WORD:
                if tokens[scan].word() == "as":
                    scan = scan + 2
                elif tokens[scan].word() not in CLAUSE_ENDERS and tokens[scan].word() not in (
                    "on", "using", "inner", "left", "right", "full", "cross", "natural", "join"
                ):
                    scan = scan + 1

            # A comma here means another table in the same FROM clause.
            if scan < len(tokens) and tokens[scan].text == ",":
                joins = joins + 1
                scan = scan + 1
                reading = True

        position = scan

    return (tables, joins)


def has_limit(tokens) -> bool:
    for token in tokens:
        if token.kind == TokenKind.WORD and token.word() == "limit":
            return True
    return False


def refuse(reason: RefusalReason, message: str, original_sql: str) -> ValidationResult:
    return ValidationResult(
        allowed=False, reason=reason, message=message, original_sql=original_sql
    )


def validate(sql: str, allowed_tables: list[str], max_rows: int | None = None) -> ValidationResult:
    """
    Decide whether this SQL may run, and return the exact text that will run.

    The returned `sql` is what gets executed - comments removed, LIMIT added.
    Validating one string and executing a different one is how a validator gets
    bypassed, so they are the same string by construction.
    """
    if max_rows is None:
        max_rows = settings.max_rows

    original = sql.strip()

    # --- 1. empty ---
    if original == "":
        return refuse(RefusalReason.EMPTY, "No SQL was produced.", original)

    parsed = tokenise(original)

    # --- 2. unterminated string ---
    if parsed.unterminated_string:
        return refuse(
            RefusalReason.UNBALANCED,
            "The query contains an unclosed quote, so part of it is hidden inside a string.",
            original,
        )

    if len(parsed.tokens) == 0:
        return refuse(RefusalReason.EMPTY, "The query contained nothing but comments.", original)

    # --- 3. parentheses ---
    if not parentheses_are_balanced(parsed.tokens):
        return refuse(RefusalReason.UNBALANCED, "The parentheses in the query do not balance.", original)

    # --- 4. one statement only ---
    if count_statements(parsed.tokens) > 1:
        return refuse(
            RefusalReason.MULTIPLE_STATEMENTS,
            "The query contains more than one statement. Only a single SELECT is allowed.",
            original,
        )

    # --- 5. it must be a read ---
    first_word = ""
    for token in parsed.tokens:
        if token.kind == TokenKind.WORD:
            first_word = token.word()
            break

    if first_word not in ("select", "with"):
        return refuse(
            RefusalReason.NOT_A_SELECT,
            "Only SELECT queries are allowed. This one starts with '%s'." % first_word.upper(),
            original,
        )

    # --- 6. forbidden keywords, as whole words ---
    for token in parsed.tokens:
        if token.kind != TokenKind.WORD:
            continue
        if token.word() in FORBIDDEN_KEYWORDS:
            return refuse(
                RefusalReason.FORBIDDEN_KEYWORD,
                "The query uses '%s', which is not allowed in a read-only query."
                % token.text.upper(),
                original,
            )

    # --- 7. every table must be one we exposed ---
    cte_names = extract_cte_names(parsed.tokens)
    tables, joins = extract_tables_and_joins(parsed.tokens)

    allowed_lower: list[str] = []
    for name in allowed_tables:
        allowed_lower.append(name.lower())

    unknown: list[str] = []
    real_tables: list[str] = []

    for table in tables:
        if table in cte_names:
            continue        # defined by the query itself
        if table in allowed_lower:
            real_tables.append(table)
        else:
            unknown.append(table)

    if len(unknown) > 0:
        return refuse(
            RefusalReason.UNKNOWN_TABLE,
            "The query refers to %s, which is not a table you can query. Available tables: %s"
            % (", ".join(unknown), ", ".join(sorted(allowed_lower))),
            original,
        )

    # --- 8. join count ---
    if joins > settings.max_joins:
        return refuse(
            RefusalReason.TOO_MANY_JOINS,
            "The query joins %d times, more than the limit of %d."
            % (joins, settings.max_joins),
            original,
        )

    # --- 9. rebuild the exact text that will run ---
    rewrites: list[str] = []

    runnable = strip_comments(original)
    if len(parsed.comments) > 0:
        rewrites.append("removed %d comment(s)" % len(parsed.comments))

    while runnable.endswith(";"):
        runnable = runnable[:-1].rstrip()

    limit_added = False
    if not has_limit(parsed.tokens):
        runnable = runnable + " LIMIT " + str(max_rows)
        limit_added = True
        rewrites.append("added LIMIT %d" % max_rows)

    return ValidationResult(
        allowed=True,
        sql=runnable,
        original_sql=original,
        tables_used=real_tables,
        join_count=joins,
        limit_added=limit_added,
        rewrites=rewrites,
    )
