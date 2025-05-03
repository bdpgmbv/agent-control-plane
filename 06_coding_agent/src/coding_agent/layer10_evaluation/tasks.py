"""
LAYER 10 - THE BENCHMARK
========================
Eight bugs, each introduced by an exact string replacement in a repository whose
test suite is green to begin with.

Building the benchmark this way rather than shipping eight broken copies of the
repository has one property that matters: the FIX is not stored anywhere. Each
task says how to break the code and which test should then fail, and nothing in
the harness knows what the correct patch looks like. The agent is graded on
whether the suite goes green and stays honest, never on whether it wrote the same
lines a person would have. Storing a reference patch and diffing against it would
measure imitation instead.

Every bug here is one somebody has actually shipped:

    a sign flipped in a total            undercharges every order
    a loop that misses its last element  the best customers get the worst rate
    truncation where rounding was meant  a penny short, every time, forever
    a mutable default argument           the second user sees the first one's data
    a missing None branch                crashes on the most common input
    division without a zero check        crashes on an empty basket
    > where >= was meant                 refuses to sell the last one in stock
    numbers sorted as strings            "100" comes before "9"

RECORDED PLANS
--------------
Each task carries a plan: the replies a model would have given. Offline runs
replay them, which exercises the whole harness without a key. Two of the plans
deliberately start with a step that goes wrong - one asks to edit a test file,
one quotes ambiguous text - so that the refusal paths are exercised on every
offline run rather than only when something breaks.
"""

import json

from coding_agent.layer2_models.schemas import Task


def reply(thinking, edits=None, read_files=None, done=False) -> str:
    """Build one recorded model reply."""
    return json.dumps({
        "thinking": thinking,
        "read_files": read_files or [],
        "edits": edits or [],
        "done": done,
    })


def edit(path, old_text, new_text, reason="") -> dict:
    return {"path": path, "old_text": old_text, "new_text": new_text, "reason": reason}


# =====================================================================
#  The tasks
# =====================================================================

BENCHMARK = [
    # ---------------------------------------------------------------- 1
    {
        "task_id": "tax_sign",
        "title": "Tax is being subtracted instead of added",
        "issue": (
            "Every order is coming out cheaper than it should. A 100.00 order "
            "with 20% tax is being charged 80.00 instead of 120.00, so we are "
            "undercharging by the whole tax amount on every sale.\n\n"
            "total_with_tax in the pricing module looks wrong."
        ),
        "target_tests": ["shopkit/tests/test_pricing.py::test_tax_is_added_not_subtracted"],
        "hint_paths": ["shopkit/pricing.py"],
        "break": [
            ("shopkit/pricing.py",
             "    return round_money(subtotal + tax)",
             "    return round_money(subtotal - tax)"),
        ],
        "plan": [
            reply("The tax is being subtracted from the subtotal rather than added.",
                  [edit("shopkit/pricing.py",
                        "    return round_money(subtotal - tax)",
                        "    return round_money(subtotal + tax)",
                        "tax is added to the subtotal, not taken off it")]),
        ],
    },

    # ---------------------------------------------------------------- 2
    {
        "task_id": "top_tier_skipped",
        "title": "Biggest orders get the second-best discount",
        "issue": (
            "Customers spending over 500 are getting 15% off instead of the 20% "
            "the top tier promises. Smaller orders seem to get the right rate, "
            "so it looks like the highest band is never being considered.\n\n"
            "tiered_discount is the function that picks the band."
        ),
        "target_tests": ["shopkit/tests/test_pricing.py::test_the_highest_tier_is_not_skipped"],
        "hint_paths": ["shopkit/pricing.py"],
        "break": [
            ("shopkit/pricing.py",
             "    best_percentage = 0.0\n    for tier in tiers:",
             "    best_percentage = 0.0\n    for tier in tiers[:-1]:"),
        ],
        # This plan's first move is refused: it quotes text that appears twice.
        "plan": [
            reply("The loop looks like it is skipping a tier.",
                  [edit("shopkit/pricing.py",
                        "        return 0.0",
                        "        return 0.0  # checked",
                        "marking the early return")]),
            reply("That was ambiguous. The real problem is the slice on the loop, "
                  "which drops the last tier.",
                  [edit("shopkit/pricing.py",
                        "    for tier in tiers[:-1]:",
                        "    for tier in tiers:",
                        "every tier must be considered, including the last")]),
        ],
    },

    # ---------------------------------------------------------------- 3
    {
        "task_id": "rounding_truncates",
        "title": "Money is being truncated instead of rounded",
        "issue": (
            "Totals are coming out a penny short. 9.999 is being stored as 9.99 "
            "rather than 10.00, and 2.675 comes back as 2.67 instead of 2.68.\n\n"
            "Every amount in the system goes through round_money, so this is "
            "costing us a penny on a large fraction of orders."
        ),
        "target_tests": [
            "shopkit/tests/test_money.py::test_does_not_truncate",
            "shopkit/tests/test_money.py::test_rounds_half_away_from_zero_not_to_even",
        ],
        "hint_paths": ["shopkit/money.py"],
        "break": [
            ("shopkit/money.py",
             "    if scaled >= 0:\n        rounded = int(scaled + 0.5)\n"
             "    else:\n        rounded = int(scaled - 0.5)",
             "    if scaled >= 0:\n        rounded = int(scaled)\n"
             "    else:\n        rounded = int(scaled)"),
        ],
        "plan": [
            reply("int() truncates towards zero, so the half is being dropped. "
                  "Adding or subtracting a half before truncating rounds away "
                  "from zero, which is what a till does.",
                  [edit("shopkit/money.py",
                        "    if scaled >= 0:\n        rounded = int(scaled)\n"
                        "    else:\n        rounded = int(scaled)",
                        "    if scaled >= 0:\n        rounded = int(scaled + 0.5)\n"
                        "    else:\n        rounded = int(scaled - 0.5)",
                        "round half away from zero instead of truncating")]),
        ],
    },

    # ---------------------------------------------------------------- 4
    {
        "task_id": "mutable_default",
        "title": "A new cart already has someone else's items in it",
        "issue": (
            "Customers are seeing items they never added. Creating a fresh Cart "
            "sometimes returns one that already contains products from an "
            "earlier session.\n\n"
            "This is a data leak between customers and needs fixing urgently."
        ),
        "target_tests": ["shopkit/tests/test_cart.py::test_two_carts_do_not_share_items"],
        "hint_paths": ["shopkit/cart.py"],
        "break": [
            ("shopkit/cart.py",
             "    def __init__(self, items=None):",
             "    def __init__(self, items=[]):"),
            ("shopkit/cart.py",
             "        if items is None:\n            items = []\n        self.items = list(items)",
             "        self.items = items"),
        ],
        "plan": [
            reply("The default argument is a list, so every Cart built without "
                  "arguments shares the same one. The default must be None and "
                  "a fresh list made inside.",
                  [edit("shopkit/cart.py",
                        "    def __init__(self, items=[]):",
                        "    def __init__(self, items=None):",
                        "a mutable default is shared between every call"),
                   edit("shopkit/cart.py",
                        "        self.items = items",
                        "        if items is None:\n            items = []\n"
                        "        self.items = list(items)",
                        "make a fresh list, and copy what was passed in")]),
        ],
    },

    # ---------------------------------------------------------------- 5
    {
        "task_id": "coupon_none_crash",
        "title": "Checkout crashes when there is no coupon",
        "issue": (
            "Most orders do not have a coupon, and those orders are now failing "
            "at checkout with a TypeError from apply_coupon.\n\n"
            "Orders that do have a coupon are fine."
        ),
        "target_tests": ["shopkit/tests/test_pricing.py::test_a_missing_coupon_is_normal"],
        "hint_paths": ["shopkit/pricing.py"],
        "break": [
            ("shopkit/pricing.py",
             "    if coupon is None:\n        return round_money(subtotal)\n\n"
             "    code = str(coupon).strip().upper()",
             "    code = coupon.strip().upper()"),
        ],
        # This plan starts by reading a file, exercising the explore path.
        "plan": [
            reply("I want to see how apply_coupon is called before changing it.",
                  [], ["shopkit/cart.py"]),
            reply("apply_coupon calls .strip() on the coupon without checking "
                  "for None first. A missing coupon is the normal case, so it "
                  "should return the subtotal unchanged.",
                  [edit("shopkit/pricing.py",
                        "    code = coupon.strip().upper()",
                        "    if coupon is None:\n        return round_money(subtotal)\n\n"
                        "    code = str(coupon).strip().upper()",
                        "no coupon is an ordinary order, not an error")]),
        ],
    },

    # ---------------------------------------------------------------- 6
    {
        "task_id": "empty_cart_divide",
        "title": "Empty basket crashes the pricing page",
        "issue": (
            "Loading the basket page with nothing in it raises "
            "ZeroDivisionError from average_item_price.\n\n"
            "An empty basket is a completely normal state and should not be an "
            "error."
        ),
        "target_tests": [
            "shopkit/tests/test_pricing.py::test_average_of_an_empty_cart_is_zero_not_a_crash",
        ],
        "hint_paths": ["shopkit/pricing.py"],
        "break": [
            ("shopkit/pricing.py",
             "    count = cart.count()\n    if count == 0:\n        return 0.0\n\n"
             "    return round_money(cart.subtotal() / count)",
             "    count = cart.count()\n    return round_money(cart.subtotal() / count)"),
        ],
        "plan": [
            reply("average_item_price divides by the item count without checking "
                  "whether it is zero.",
                  [edit("shopkit/pricing.py",
                        "    count = cart.count()\n    return round_money(cart.subtotal() / count)",
                        "    count = cart.count()\n    if count == 0:\n        return 0.0\n\n"
                        "    return round_money(cart.subtotal() / count)",
                        "an empty cart has no average; return zero rather than raising")]),
        ],
    },

    # ---------------------------------------------------------------- 7
    {
        "task_id": "stock_boundary",
        "title": "Cannot buy the last one in stock",
        "issue": (
            "If we have exactly 10 of something and a customer orders 10, the "
            "order is refused as out of stock. Ordering 9 works.\n\n"
            "We are turning away sales on anything that is nearly sold out."
        ),
        "target_tests": ["shopkit/tests/test_inventory.py::test_exactly_enough_stock_is_enough"],
        "hint_paths": ["shopkit/inventory.py"],
        "break": [
            ("shopkit/inventory.py",
             "        return self.stock_of(sku) >= quantity",
             "        return self.stock_of(sku) > quantity"),
        ],
        "plan": [
            reply("in_stock uses a strict greater-than, so having exactly as "
                  "many as were asked for counts as not enough.",
                  [edit("shopkit/inventory.py",
                        "        return self.stock_of(sku) > quantity",
                        "        return self.stock_of(sku) >= quantity",
                        "having exactly enough is enough")]),
        ],
    },

    # ---------------------------------------------------------------- 8
    {
        "task_id": "sort_as_string",
        "title": "Cheapest-first ordering is wrong",
        "issue": (
            "The 'sort by price' view puts a 100.00 item before a 9.00 one. It "
            "looks like the prices are being compared as text rather than as "
            "numbers, so '100' sorts before '9'.\n\n"
            "items_by_price on the Cart is what the view calls."
        ),
        "target_tests": ["shopkit/tests/test_cart.py::test_items_sorted_by_price_numerically"],
        "hint_paths": ["shopkit/cart.py"],
        "break": [
            ("shopkit/cart.py",
             "        ordered.sort(key=lambda item: item.unit_price)",
             "        ordered.sort(key=lambda item: str(item.unit_price))"),
        ],
        # This plan's first move is refused: it tries to edit the test.
        "plan": [
            reply("The test expects numeric ordering. I will adjust the test to "
                  "match the current behaviour.",
                  [edit("shopkit/tests/test_cart.py",
                        "    assert prices == [9.00, 10.00, 100.00]",
                        "    assert prices == [10.00, 100.00, 9.00]",
                        "match what the code does")]),
            reply("That is not allowed and it was the wrong instinct - the test "
                  "is the specification. The sort key converts the price to a "
                  "string, so it compares lexically.",
                  [edit("shopkit/cart.py",
                        "        ordered.sort(key=lambda item: str(item.unit_price))",
                        "        ordered.sort(key=lambda item: item.unit_price)",
                        "compare prices as numbers, not as text")]),
        ],
    },
]


def all_task_ids() -> list[str]:
    # str() because BENCHMARK is a list of plain dicts whose values are a mix of
    # strings, lists and nested tuples. The key really does hold a string; the
    # cast is what says so to anything reading the types rather than the data.
    ids: list[str] = []
    for entry in BENCHMARK:
        ids.append(str(entry["task_id"]))
    return ids


def find(task_id: str) -> dict | None:
    for entry in BENCHMARK:
        if entry["task_id"] == task_id:
            return entry
    return None


def to_task(entry: dict, repo_path: str = "") -> Task:
    return Task(
        task_id=entry["task_id"],
        title=entry["title"],
        issue=entry["issue"],
        repo_path=repo_path,
        target_tests=list(entry["target_tests"]),
        hint_paths=list(entry.get("hint_paths", [])),
    )


class BreakFailed(Exception):
    """The replacement that introduces the bug did not match."""


def apply_break(workspace, entry: dict) -> list[str]:
    """
    Introduce the bug.

    Every replacement is checked for being present exactly once and asserted to
    have changed the file. A break that silently matches nothing would leave the
    repository green, the agent with nothing to do, and the benchmark reporting
    a solve it never earned - which is the single easiest way for this whole
    project to start lying to itself.
    """
    touched = []

    for path, old_text, new_text in entry["break"]:
        source = workspace.read(path)
        occurrences = source.count(old_text)

        if occurrences == 0:
            raise BreakFailed(
                "task %r: the text it breaks is not in %s. The benchmark "
                "repository has changed and this task needs updating."
                % (entry["task_id"], path))
        if occurrences > 1:
            raise BreakFailed(
                "task %r: the text it breaks appears %d times in %s, so the "
                "bug would land somewhere unintended."
                % (entry["task_id"], occurrences, path))

        updated = source.replace(old_text, new_text, 1)
        if updated == source:
            raise BreakFailed("task %r: the replacement changed nothing" % entry["task_id"])

        workspace.write(path, updated)
        if path not in touched:
            touched.append(path)

    return touched
