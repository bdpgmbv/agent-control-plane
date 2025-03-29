"""
Reset the database to the demo data.

    python scripts/seed_demo.py

Useful because refunds issued by an earlier run change how the next one behaves:
an order that has already been refunded gives a completely different answer.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from support_agent.layer3_storage.database import get_database  # noqa: E402
from support_agent.layer3_storage.seed_data import seed  # noqa: E402


def main() -> None:
    database = get_database()
    database.reset_agent_data()
    counts = seed(database, fresh=True)

    print("")
    print("database reset and reseeded:")
    for name in counts:
        print("   %-16s %d" % (name, counts[name]))
    print("")
    print("orders you can try:")
    print("   ORD-10023  in transit            -> 'where is my order?'")
    print("   ORD-10024  delivered, $34.50     -> a refund the agent may issue")
    print("   ORD-10025  delivered, $899.00    -> a refund needing human approval")
    print("   ORD-10026  refund already running-> 'where is my refund?'")
    print("   ORD-10028  delivered 63 days ago -> outside the refund window")
    print("   ORD-20001  belongs to BOB        -> Alice must not be able to read it")
    print("")


if __name__ == "__main__":
    main()
