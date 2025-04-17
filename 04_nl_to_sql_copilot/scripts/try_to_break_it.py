"""
Attack the copilot, and show exactly what stopped each attempt.

    python scripts/try_to_break_it.py

The interesting part is the second half. Every attack is run TWICE:

    through the validator   which refuses it, with a reason
    straight at the database, with the validator bypassed entirely

The second run is the one worth watching. It is what happens if the validator is
wrong - if I missed a dialect quirk, a function with a side effect, or a syntax I
did not think of. The database refuses anyway, because the connection cannot
write, and that refusal comes from SQLite rather than from anything I wrote.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sql_copilot.layer3_database.connection import get_database  # noqa: E402
from sql_copilot.layer3_database.schema_sql import ALLOWED_TABLES  # noqa: E402
from sql_copilot.layer6_validation.step2_validate import validate  # noqa: E402

# Each attack is tagged with what it is trying to do, because the two defences
# stop different things and the distinction is the whole lesson.
#
#   "write"  changes or destroys data. Both layers stop these.
#   "read"   reads something it should not be able to see. ONLY the allowlist
#            stops these - a read-only connection has no opinion about which
#            tables you are allowed to read.
ATTACKS = [
    ("write", "stacked statement", "SELECT * FROM orders; DROP TABLE orders"),
    ("write", "plain delete", "DELETE FROM orders WHERE 1=1"),
    ("write", "plain update", "UPDATE orders SET status = 'cancelled'"),
    ("write", "drop a table", "DROP TABLE customers"),
    ("write", "SELECT ... INTO", "SELECT * INTO copies FROM orders"),
    ("write", "attach another database", "ATTACH DATABASE '/etc/passwd' AS leak"),
    ("write", "load an extension", "SELECT load_extension('evil.so')"),
    ("write", "statement hidden in a comment", "SELECT * FROM orders /* */; DELETE FROM orders"),
    ("read", "read a hidden table", "SELECT * FROM employee_salaries"),
    ("read", "hidden table via UNION", "SELECT order_id FROM orders UNION SELECT name FROM employee_salaries"),
    ("read", "hidden table via subquery", "SELECT (SELECT MAX(salary) FROM employee_salaries) FROM orders"),
    ("read", "read sqlite internals", "SELECT sql FROM sqlite_master"),
]

# Legitimate SQL, to prove the validator is not simply refusing everything.
LEGITIMATE = [
    ("a column called updated_at", "SELECT updated_at FROM orders LIMIT 1"),
    ("a semicolon inside a string", "SELECT * FROM order_notes WHERE note = 'paid; refunded'"),
    ("a common table expression",
     "WITH recent AS (SELECT * FROM orders) SELECT COUNT(*) FROM recent"),
    ("a three-table join",
     "SELECT c.country FROM order_items oi JOIN orders o ON oi.order_id = o.order_id "
     "JOIN customers c ON o.customer_id = c.customer_id LIMIT 1"),
]


def main() -> None:
    database = get_database()

    before: dict[str, int] = {}
    for table in ALLOWED_TABLES + ["employee_salaries"]:
        before[table] = database.count_rows(table)

    print("")
    print("=" * 78)
    print(" TRYING TO BREAK THE SQL COPILOT")
    print("=" * 78)
    print("")
    print(" LAYER 1: the validator")
    print("")

    blocked = 0
    for kind, label, sql in ATTACKS:
        verdict = validate(sql, ALLOWED_TABLES)

        if verdict.allowed:
            print("   %-6s %-32s *** ALLOWED - THIS WOULD HAVE RUN ***" % (kind, label))
        else:
            blocked = blocked + 1
            print("   %-6s %-32s refused: %s" % (kind, label, verdict.reason.value))

    print("")
    print("   %d of %d refused." % (blocked, len(ATTACKS)))
    print("")

    print(" LAYER 2: the database, with the validator bypassed entirely")
    print("")
    print("   Each statement below goes straight to the connection. This is what")
    print("   happens if the validator is wrong about something.")
    print("")

    writes_accepted = 0
    reads_accepted = 0

    for kind, label, sql in ATTACKS:
        try:
            database.connection.execute(sql)
            if kind == "write":
                writes_accepted = writes_accepted + 1
                print("   %-6s %-32s *** ACCEPTED - THE DATA IS AT RISK ***" % (kind, label))
            else:
                reads_accepted = reads_accepted + 1
                print("   %-6s %-32s accepted (see the note below)" % (kind, label))
        except Exception as error:
            message = str(error)
            if len(message) > 38:
                message = message[:38]
            print("   %-6s %-32s refused by SQLite: %s" % (kind, label, message))

    print("")
    print("   writes accepted by the database: %d" % writes_accepted)
    print("   reads  accepted by the database: %d" % reads_accepted)
    print("")
    print("   THIS IS THE POINT, AND IT IS NOT A BUG.")
    print("")
    print("   A read-only connection stops WRITES. It has no opinion whatsoever")
    print("   about which tables you may READ - as far as SQLite is concerned,")
    print("   employee_salaries is just another table in the file.")
    print("")
    print("   So the two defences are not two copies of the same protection:")
    print("")
    print("      destroying data      stopped by BOTH the validator and the database")
    print("      reading a hidden     stopped ONLY by the allowlist (rule 7)")
    print("      table")
    print("")
    print("   That is why rule 7 is written as an allowlist rather than a list of")
    print("   forbidden words, and why a PostgreSQL deployment should ALSO use a")
    print("   role with grants on exactly these tables - so unauthorised reads get")
    print("   a second layer too, which SQLite cannot give them.")
    print("")

    print(" AND THE VALIDATOR IS NOT JUST REFUSING EVERYTHING")
    print("")
    for label, sql in LEGITIMATE:
        verdict = validate(sql, ALLOWED_TABLES)
        if verdict.allowed:
            print("   %-32s allowed" % label)
        else:
            print("   %-32s *** WRONGLY REFUSED: %s ***" % (label, verdict.reason.value))
    print("")

    after: dict[str, int] = {}
    for table in before:
        after[table] = database.count_rows(table)

    changed: list[str] = []
    for table in before:
        if before[table] != after[table]:
            changed.append("%s: %d -> %d" % (table, before[table], after[table]))

    print("=" * 78)
    if len(changed) == 0:
        print(" Every table has exactly the same number of rows as when this started,")
        print(" including employee_salaries, which the copilot cannot even see.")
    else:
        print(" *** THE DATABASE CHANGED ***")
        for line in changed:
            print("   ! %s" % line)
    print("=" * 78)
    print("")


if __name__ == "__main__":
    main()
