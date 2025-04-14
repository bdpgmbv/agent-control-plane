"""
TESTS FOR THE VALIDATOR.

Two halves, and both matter: dangerous SQL must be refused, and legitimate SQL
must be allowed. A validator that blocks everything is not secure, it is broken.
"""

from sql_copilot.layer2_models.schemas import RefusalReason
from sql_copilot.layer6_validation.step1_tokenise import tokenise
from sql_copilot.layer6_validation.step2_validate import (
    extract_cte_names,
    extract_tables_and_joins,
    validate,
)

# ---------------- things that must be refused ----------------

def test_a_stacked_statement_is_refused(allowed_tables):
    result = validate("SELECT * FROM orders; DROP TABLE orders", allowed_tables)
    assert result.allowed is False
    assert result.reason == RefusalReason.MULTIPLE_STATEMENTS


def test_anything_that_is_not_a_select_is_refused(allowed_tables):
    for sql in [
        "DELETE FROM orders",
        "UPDATE orders SET status = 'x'",
        "DROP TABLE orders",
        "INSERT INTO orders VALUES (1)",
        "ATTACH DATABASE '/etc/passwd' AS leak",
        "PRAGMA table_info(orders)",
        "CREATE TABLE evil (a INT)",
    ]:
        result = validate(sql, allowed_tables)
        assert result.allowed is False, sql


def test_select_into_is_refused(allowed_tables):
    """It creates a table in several dialects, so it is a write wearing a SELECT."""
    result = validate("SELECT * INTO copies FROM orders", allowed_tables)
    assert result.allowed is False
    assert result.reason == RefusalReason.FORBIDDEN_KEYWORD


def test_loading_an_extension_is_refused(allowed_tables):
    result = validate("SELECT load_extension('evil.so')", allowed_tables)
    assert result.reason == RefusalReason.FORBIDDEN_KEYWORD


def test_a_table_not_on_the_allowlist_is_refused(allowed_tables):
    """
    THE RULE THAT ACTUALLY HOLDS. employee_salaries exists and has rows. Nothing
    blocks it by name - it is refused because it is not on the list.
    """
    result = validate("SELECT * FROM employee_salaries", allowed_tables)
    assert result.allowed is False
    assert result.reason == RefusalReason.UNKNOWN_TABLE


def test_a_hidden_table_reached_through_union_is_refused(allowed_tables):
    result = validate(
        "SELECT order_id FROM orders UNION SELECT name FROM employee_salaries", allowed_tables
    )
    assert result.reason == RefusalReason.UNKNOWN_TABLE


def test_a_hidden_table_reached_through_a_join_is_refused(allowed_tables):
    result = validate(
        "SELECT o.order_id FROM orders o JOIN employee_salaries e ON 1=1", allowed_tables
    )
    assert result.reason == RefusalReason.UNKNOWN_TABLE


def test_a_hidden_table_inside_a_subquery_is_refused(allowed_tables):
    result = validate(
        "SELECT (SELECT MAX(salary) FROM employee_salaries) AS x FROM orders", allowed_tables
    )
    assert result.reason == RefusalReason.UNKNOWN_TABLE


def test_sqlite_internals_are_refused(allowed_tables):
    assert validate("SELECT sql FROM sqlite_master", allowed_tables).allowed is False


def test_an_unterminated_string_is_refused(allowed_tables):
    result = validate("SELECT * FROM orders WHERE x = 'open", allowed_tables)
    assert result.reason == RefusalReason.UNBALANCED


def test_too_many_joins_are_refused(allowed_tables):
    sql = "SELECT 1 FROM orders a JOIN orders b ON 1=1 JOIN orders c ON 1=1 JOIN orders d ON 1=1 " \
          "JOIN orders e ON 1=1 JOIN orders f ON 1=1 JOIN orders g ON 1=1 JOIN orders h ON 1=1"
    result = validate(sql, allowed_tables)
    assert result.reason == RefusalReason.TOO_MANY_JOINS


def test_empty_sql_is_refused(allowed_tables):
    assert validate("", allowed_tables).reason == RefusalReason.EMPTY
    assert validate("-- just a comment", allowed_tables).allowed is False


# ---------------- things that must be allowed ----------------

def test_a_column_whose_name_contains_a_keyword_is_allowed(allowed_tables):
    result = validate("SELECT updated_at FROM orders", allowed_tables)
    assert result.allowed is True


def test_a_semicolon_inside_a_string_is_allowed(allowed_tables):
    result = validate("SELECT * FROM order_notes WHERE note = 'paid; refunded'", allowed_tables)
    assert result.allowed is True


def test_a_common_table_expression_is_allowed(allowed_tables):
    """
    A CTE name is a table that exists only inside the query. Missing this refuses
    every legitimate query that uses one.
    """
    result = validate(
        "WITH recent AS (SELECT * FROM orders) SELECT COUNT(*) FROM recent", allowed_tables
    )
    assert result.allowed is True


def test_several_ctes_are_allowed(allowed_tables):
    sql = (
        "WITH a AS (SELECT * FROM orders), b AS (SELECT * FROM returns) "
        "SELECT COUNT(*) FROM a JOIN b ON a.order_id = b.order_id"
    )
    assert validate(sql, allowed_tables).allowed is True


def test_aliases_are_not_mistaken_for_tables(allowed_tables):
    result = validate(
        "SELECT o.order_id FROM orders o JOIN customers c ON o.customer_id = c.customer_id",
        allowed_tables,
    )
    assert result.allowed is True
    assert sorted(result.tables_used) == ["customers", "orders"]


def test_a_subquery_in_from_is_allowed(allowed_tables):
    result = validate(
        "SELECT AVG(t.total) FROM (SELECT SUM(quantity) AS total FROM order_items GROUP BY order_id) t",
        allowed_tables,
    )
    assert result.allowed is True


def test_a_comma_join_is_allowed_and_counted(allowed_tables):
    result = validate(
        "SELECT o.order_id FROM orders o, customers c WHERE o.customer_id = c.customer_id",
        allowed_tables,
    )
    assert result.allowed is True
    assert result.join_count >= 1


# ---------------- rewriting ----------------

def test_a_limit_is_added_when_missing(allowed_tables):
    result = validate("SELECT * FROM orders", allowed_tables, max_rows=50)
    assert result.limit_added is True
    assert result.sql.endswith("LIMIT 50")


def test_an_existing_limit_is_left_alone(allowed_tables):
    result = validate("SELECT * FROM orders LIMIT 7", allowed_tables, max_rows=500)
    assert result.limit_added is False
    assert "LIMIT 7" in result.sql
    assert "LIMIT 500" not in result.sql


def test_the_validated_sql_is_the_sql_that_runs(allowed_tables):
    """
    Validating one string and executing another is how a validator gets bypassed.
    The comment is gone from the text that will run, not just from the check.
    """
    result = validate("SELECT * FROM orders -- hidden", allowed_tables)
    assert result.allowed is True
    assert "hidden" not in result.sql
    assert "hidden" in result.original_sql


# ---------------- the extractors ----------------

def test_cte_names_are_found():
    tokens = tokenise("WITH a AS (SELECT 1), b AS (SELECT 2) SELECT * FROM a").tokens
    names = extract_cte_names(tokens)
    assert "a" in names
    assert "b" in names


def test_schema_qualified_names_resolve_to_the_table():
    tokens = tokenise("SELECT * FROM main.orders").tokens
    tables, joins = extract_tables_and_joins(tokens)
    assert tables == ["orders"]
