"""
LAYER 4 - SCHEMA, STEP 1: READING THE SCHEMA
============================================
Builds the description of the database that the model is shown.

Two things worth noticing:

  THE ALLOWLIST IS APPLIED HERE, FIRST.
      A table that is not on it is not introspected, not described and not put in
      any prompt. The model is never told `employee_salaries` exists. That is not
      the security control - layer 6 is - but a model cannot ask for a table it
      has never heard of, and most accidents are not attacks.

  THE DESCRIPTIONS MATTER MORE THAN THE TYPES.
      A model given `status TEXT` invents plausible values: 'complete',
      'fulfilled', 'CANCELLED'. A model given "status is one of 'placed',
      'shipped', 'delivered', 'cancelled'" uses those. Most wrong-but-valid SQL
      comes from guessing at values, and the fix is in the schema description
      rather than the prompt.
"""

from sql_copilot.layer0_shared.logging_setup import get_logger
from sql_copilot.layer2_models.schemas import ColumnInfo, DatabaseSchema, TableInfo
from sql_copilot.layer3_database.schema_sql import (
    ALLOWED_TABLES,
    COLUMN_DESCRIPTIONS,
    TABLE_DESCRIPTIONS,
)

log = get_logger(__name__)


def introspect(database) -> DatabaseSchema:
    """Read the schema of every allowed table."""
    tables: list[TableInfo] = []

    present = database.list_table_names()

    for table_name in ALLOWED_TABLES:
        if table_name not in present:
            continue

        references = database.foreign_keys(table_name)

        columns: list[ColumnInfo] = []
        for column in database.describe_table(table_name):
            key = table_name + "." + column["name"]
            columns.append(
                ColumnInfo(
                    name=column["name"],
                    data_type=column["data_type"],
                    description=COLUMN_DESCRIPTIONS.get(key, ""),
                    is_primary_key=column["is_primary_key"],
                    references=references.get(column["name"], ""),
                )
            )

        tables.append(
            TableInfo(
                name=table_name,
                description=TABLE_DESCRIPTIONS.get(table_name, ""),
                columns=columns,
                row_count=database.count_rows(table_name),
            )
        )

    return DatabaseSchema(tables=tables)


_schema: DatabaseSchema | None = None


def get_schema(database) -> DatabaseSchema:
    """Read once. The schema does not change while the process runs."""
    global _schema
    if _schema is None:
        _schema = introspect(database)
    return _schema


def set_schema(schema: DatabaseSchema | None) -> None:
    global _schema
    _schema = schema
