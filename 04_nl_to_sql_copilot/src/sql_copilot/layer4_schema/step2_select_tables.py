"""
LAYER 4 - SCHEMA, STEP 2: WHICH TABLES GO IN THE PROMPT
=======================================================
A warehouse has hundreds of tables. You cannot put them all in the prompt, and
sending the wrong six is worse than sending fewer - the model will write a query
against whatever it was given.

So the tables are scored against the question, and the best few are sent. It is
retrieval, exactly like project 01, with tables instead of documents.

------------------------------------------------------------------------------
THE PART THAT IS EASY TO GET WRONG: PULLING IN WHAT YOU NEED TO JOIN
------------------------------------------------------------------------------
"What is the total revenue by country?" scores `order_items` (revenue lives
there) and `customers` (country lives there) highly, and `orders` not at all -
the question never mentions orders.

But there is no way to join order_items to customers without going through
orders. Send the two obvious tables and the model writes a join that cannot work,
the query fails, the repair loop burns two more model calls, and it still cannot
work, because the table it needs was never offered.

So after scoring, any table needed to CONNECT the chosen ones is pulled in too.
"""

from sql_copilot.layer0_shared.logging_setup import get_logger, log_event
from sql_copilot.layer0_shared.text_tools import to_stems
from sql_copilot.layer2_models.schemas import DatabaseSchema, TableInfo

log = get_logger(__name__)


def score_table(table: TableInfo, question_stems: list[str]) -> float:
    """How relevant this table looks to the question."""
    searchable: list[str] = []

    for stem in to_stems(table.name):
        searchable.append(stem)
    for stem in to_stems(table.description):
        searchable.append(stem)
    for column in table.columns:
        for stem in to_stems(column.name):
            searchable.append(stem)
        for stem in to_stems(column.description):
            searchable.append(stem)

    table_terms = set(searchable)

    # The table's own name counts for more than a column buried inside it.
    name_terms = set(to_stems(table.name))

    score = 0.0
    counted: set[str] = set()

    for stem in question_stems:
        if stem in counted:
            continue
        counted.add(stem)

        if stem in name_terms:
            score = score + 2.0
        elif stem in table_terms:
            score = score + 1.0

    return score


def tables_needed_to_join(chosen: list[str], schema: DatabaseSchema) -> list[str]:
    """
    Tables that must come along so the chosen ones can actually be joined.

    Walks the foreign keys. If A points at C and B points at C, then C is needed
    to connect A and B even when nothing in the question mentions it.
    """
    chosen_set = set(chosen)
    extra: list[str] = []

    for table in schema.tables:
        if table.name not in chosen_set:
            continue

        for column in table.columns:
            if column.references == "":
                continue

            target = column.references.split(".")[0]
            if target not in chosen_set and target not in extra:
                extra.append(target)

    # And the other direction: a link table that points at two chosen tables.
    for table in schema.tables:
        if table.name in chosen_set or table.name in extra:
            continue

        pointed_at: list[str] = []
        for column in table.columns:
            if column.references == "":
                continue
            target = column.references.split(".")[0]
            if target in chosen_set and target not in pointed_at:
                pointed_at.append(target)

        if len(pointed_at) >= 2:
            extra.append(table.name)

    return extra


def select_tables(question: str, schema: DatabaseSchema, limit: int) -> list[TableInfo]:
    """The tables to describe in the prompt, best first."""
    question_stems = to_stems(question)

    scored: list[tuple[float, TableInfo]] = []
    for table in schema.tables:
        scored.append((score_table(table, question_stems), table))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    chosen: list[str] = []
    for score, table in scored:
        if len(chosen) >= limit:
            break
        if score <= 0:
            continue
        chosen.append(table.name)

    # A question that matched nothing still needs something to work with.
    if len(chosen) == 0:
        for _score, table in scored[:limit]:
            chosen.append(table.name)

    needed = tables_needed_to_join(chosen, schema)
    for name in needed:
        if name not in chosen:
            chosen.append(name)

    # Pulling in join tables can push past the limit. Allow a little headroom
    # rather than dropping a table the query cannot run without.
    selected: list[TableInfo] = []
    for name in chosen[: limit + 3]:
        found_table = schema.find_table(name)
        if found_table is not None:
            selected.append(found_table)

    log_event(
        log,
        "schema.selected",
        question=question[:80],
        tables=chosen[: limit + 3],
        pulled_in_for_joins=needed,
    )

    return selected


def schema_prompt_text(tables: list[TableInfo]) -> str:
    """The schema section of the prompt."""
    blocks: list[str] = []
    for table in tables:
        blocks.append(table.to_prompt_text())
    return "\n\n".join(blocks)
