"""
TESTS FOR the read-only sandbox, schema retrieval, and the whole copilot.
"""

import sqlite3

from sql_copilot.layer0_shared.llm_client import LlmResult, ModelUnavailable
from sql_copilot.layer2_models.schemas import AskRequest
from sql_copilot.layer4_schema.step1_introspect import get_schema
from sql_copilot.layer4_schema.step2_select_tables import select_tables
from sql_copilot.layer5_generation.step1_generate import CANNOT_ANSWER, clean_sql_reply
from sql_copilot.layer7_execution.step1_execute import error_is_repairable, execute
from sql_copilot.layer8_answer.step1_explain import looks_like_an_injection_attempt
from sql_copilot.layer9_api.copilot_service import CopilotService

# ---------------- the read-only connection ----------------

def test_writes_fail_at_the_database_even_with_no_validation(database):
    """
    THE SECOND LINE OF DEFENCE, TESTED ON ITS OWN.

    Every statement here goes straight to the connection - the validator is not
    involved at all. They fail because SQLite refuses, which is the point: if the
    validator is ever wrong, this still holds.
    """
    for statement in [
        "INSERT INTO orders (order_id, customer_id, order_date, status, channel, updated_at) "
        "VALUES ('X', 'Y', '2025-01-01', 'placed', 'web', '2025-01-01')",
        "DELETE FROM orders",
        "UPDATE orders SET status = 'cancelled'",
        "DROP TABLE orders",
        "CREATE TABLE evil (a INT)",
    ]:
        raised = False
        try:
            database.connection.execute(statement)
        except sqlite3.Error as error:
            raised = True
            assert "readonly" in str(error).lower()
        assert raised is True, statement


def test_reads_still_work(database):
    row = database.connection.execute("SELECT COUNT(*) AS n FROM orders").fetchone()
    assert row["n"] > 0


# ---------------- execution limits ----------------

def test_the_row_cap_reports_truncation_honestly(database):
    result = execute(database, "SELECT * FROM order_items", max_rows=10)
    assert result.ok is True
    assert result.row_count == 10
    assert result.truncated is True


def test_a_small_result_is_not_marked_truncated(database):
    result = execute(database, "SELECT COUNT(*) FROM orders", max_rows=10)
    assert result.truncated is False


def test_an_expensive_query_is_stopped(database):
    """
    A valid SELECT can still try to materialise millions of rows. Nothing in the
    SQL looks wrong; it is simply expensive.
    """
    result = execute(
        database,
        "SELECT COUNT(*) FROM order_items a, order_items b, order_items c, order_items d",
    )
    assert result.ok is False
    assert "stopped after" in result.error


def test_a_typo_is_reported_as_repairable(database):
    result = execute(database, "SELECT totl FROM orders LIMIT 1")
    assert result.ok is False
    assert result.error_is_repairable is True


def test_a_write_is_not_reported_as_repairable(database):
    """Retrying it cannot help, so spending a model call on it is waste."""
    result = execute(database, "DELETE FROM orders")
    assert result.ok is False
    assert result.error_is_repairable is False


def test_error_classification():
    assert error_is_repairable("no such column: totl") is True
    assert error_is_repairable("syntax error near SELECT") is True
    assert error_is_repairable("attempt to write a readonly database") is False
    assert error_is_repairable("database is locked") is False


# ---------------- schema retrieval ----------------

def test_the_hidden_table_is_never_described(database):
    schema = get_schema(database)
    assert schema.find_table("employee_salaries") is None
    assert "employee_salaries" not in schema.table_names()


def test_tables_needed_for_a_join_are_pulled_in(database):
    """
    "Revenue by country" needs order_items and customers - and orders, which the
    question never mentions, to join them. Offering only the two obvious tables
    produces a query that cannot work.
    """
    schema = get_schema(database)
    chosen = []
    for table in select_tables("What is the total revenue by country?", schema, 3):
        chosen.append(table.name)

    assert "order_items" in chosen
    assert "customers" in chosen
    assert "orders" in chosen


# ---------------- cleaning up the model's reply ----------------

def test_markdown_fences_are_removed():
    assert clean_sql_reply("```sql\nSELECT 1\n```") == "SELECT 1"


def test_a_preamble_is_removed():
    assert clean_sql_reply("Here is the query:\nSELECT 1 FROM orders") == "SELECT 1 FROM orders"


def test_a_trailing_explanation_is_removed():
    cleaned = clean_sql_reply("SELECT 1 FROM orders\n\nThis counts the orders.")
    assert cleaned == "SELECT 1 FROM orders"


def test_a_trailing_semicolon_is_removed():
    assert clean_sql_reply("SELECT 1;") == "SELECT 1"


def test_cannot_answer_is_recognised():
    assert clean_sql_reply("CANNOT_ANSWER") == CANNOT_ANSWER
    assert clean_sql_reply("I'm afraid CANNOT_ANSWER applies here") == CANNOT_ANSWER


# ---------------- injection in the data ----------------

def test_instruction_like_text_in_a_result_is_flagged(database):
    """
    order_notes contains a row telling the reader to drop a table. It is data,
    it is shown to the user unchanged, and it is flagged so a strange-looking
    explanation has an explanation of its own.
    """
    result = execute(database, "SELECT note FROM order_notes", max_rows=100)
    signals = looks_like_an_injection_attempt(result)
    assert len(signals) > 0


def test_ordinary_rows_are_not_flagged(database):
    result = execute(database, "SELECT channel FROM orders LIMIT 20")
    assert looks_like_an_injection_attempt(result) == []


# ---------------- the whole copilot ----------------

def test_a_normal_question_is_answered(service):
    response = service.ask(AskRequest(question="How many orders are there in total?"))

    assert response.answered is True
    assert response.result is not None
    assert response.result.row_count == 1
    assert response.result.rows[0][0] > 0
    assert "LIMIT" in response.sql


def test_a_question_about_a_hidden_table_is_refused(service):
    response = service.ask(AskRequest(question="What does each employee get paid?"))

    assert response.answered is False
    assert response.refused is True
    assert response.refusal_reason == "unknown_table"
    # The SQL it tried is kept, so the refusal can be understood.
    assert "employee_salaries" in response.sql


def test_a_refusal_is_not_repaired(service):
    """
    A refusal is not a bug to fix. The query asked for something it is not
    allowed to have, and asking the model again will not change that - it just
    costs another call.
    """
    response = service.ask(AskRequest(question="What does each employee get paid?"))
    assert len(response.attempts) == 1


def test_an_unanswerable_question_says_so(service):
    response = service.ask(AskRequest(question="What is the airspeed of an unladen swallow?"))
    assert response.answered is False
    assert response.refused is False
    assert "could not write a query" in response.answer


def test_a_repaired_query_is_validated_again(database):
    """
    THE MISTAKE THIS PREVENTS.

    If a repair skipped validation, the error message would become an attack
    surface: whatever the model wrote in response to a failure would run. Here
    the model "repairs" a typo by returning a DROP TABLE, and it is refused
    exactly as a first attempt would be.
    """

    class ModelThatRepairsWithSomethingDangerous:
        is_live = True
        model = "test-double"

        def __init__(self):
            self.calls = 0

        def complete(self, system_prompt, user_prompt, task="", max_tokens=700):
            self.calls = self.calls + 1
            if self.calls == 1:
                text = "SELECT totl FROM orders"       # a typo, so it fails
            else:
                text = "DROP TABLE orders"             # the "repair"
            return LlmResult(text=text, model=self.model, prompt_tokens=10, completion_tokens=5)

    model = ModelThatRepairsWithSomethingDangerous()
    service = CopilotService(database=database, model=model)
    response = service.ask(AskRequest(question="How many orders?"))

    assert response.refused is True
    assert response.refusal_reason == "not_a_select"

    # And the table is still there.
    row = database.connection.execute("SELECT COUNT(*) AS n FROM orders").fetchone()
    assert row["n"] > 0


def test_a_repair_that_works_is_recorded_as_one(database):
    class ModelThatFixesItsTypo:
        is_live = True
        model = "test-double"

        def __init__(self):
            self.calls = 0

        def complete(self, system_prompt, user_prompt, task="", max_tokens=700):
            self.calls = self.calls + 1
            if self.calls == 1:
                text = "SELECT totl FROM orders"
            elif self.calls == 2:
                text = "SELECT COUNT(*) AS n FROM orders"
            else:
                text = "The query counted the orders."
            return LlmResult(text=text, model=self.model, prompt_tokens=10, completion_tokens=5)

    service = CopilotService(database=database, model=ModelThatFixesItsTypo())
    response = service.ask(AskRequest(question="How many orders?"))

    assert response.answered is True
    assert response.repaired is True
    assert len(response.attempts) == 2


def test_a_model_that_cannot_be_reached_produces_a_clear_message(database):
    """
    An empty account is not a failed query. Reporting it as one sends the person
    to debug something that is not broken.
    """

    class ModelWithNoCredit:
        is_live = True
        model = "test-double"

        def complete(self, system_prompt, user_prompt, task="", max_tokens=700):
            raise ModelUnavailable("The OpenAI account has no credits remaining.", kind="no_credit")

    service = CopilotService(database=database, model=ModelWithNoCredit())
    response = service.ask(AskRequest(question="How many orders?"))

    assert response.answered is False
    assert "no credits" in response.answer


def test_the_row_cap_from_the_request_is_respected(service):
    response = service.ask(AskRequest(question="What is the total revenue by channel?", max_rows=2))
    assert response.result is not None
    assert response.result.row_count <= 2
