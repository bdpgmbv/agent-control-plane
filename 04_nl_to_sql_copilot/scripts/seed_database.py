"""
Create the analytics warehouse.

    python scripts/seed_database.py

This is the ONLY code in the project that opens a writable connection.
Everything else - the API, the copilot, the tests - opens the file read-only.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sql_copilot.layer1_config.settings import settings  # noqa: E402
from sql_copilot.layer3_database.connection import build_writable_connection  # noqa: E402
from sql_copilot.layer3_database.seed_data import build_everything  # noqa: E402


def main() -> None:
    path = settings.sqlite_file()
    connection = build_writable_connection(path)

    counts = build_everything(connection)
    connection.close()

    print("")
    print("Analytics warehouse created at %s" % path)
    print("")
    for table in sorted(counts.keys()):
        print("   %-20s %6d rows" % (table, counts[table]))
    print("")
    print("Note: employee_salaries has rows but is NOT on the allowlist.")
    print("Ask the copilot about salaries and watch it be refused.")
    print("")


if __name__ == "__main__":
    main()
