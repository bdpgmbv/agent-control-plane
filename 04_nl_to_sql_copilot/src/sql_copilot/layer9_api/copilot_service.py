"""
LAYER 9 - THE COPILOT SERVICE
=============================
One question in, one answer out. The order of operations is the design:

     1. SCHEMA     pick the tables relevant to the question
     2. GENERATE   ask the model for SQL
     3. VALIDATE   refuse anything that is not a plain SELECT over those tables
     4. EXECUTE    run it read-only, under a timeout and a row cap
     5. REPAIR     on a fixable error, show the model the message and try again
     6. EXPLAIN    describe the result, treating rows as data

Steps 3 and 4 are the reason this project exists, and the order between them is
not negotiable: nothing reaches the database that has not been validated, and
nothing the validator lets through can write, because the connection cannot.

------------------------------------------------------------------------------
THE REPAIR LOOP RE-VALIDATES. EVERY TIME.
------------------------------------------------------------------------------
The easy mistake is to validate the first query, and then - because the repair
is "just a fix" - hand the corrected SQL straight to the database. That turns the
error message into an attack surface: whatever the model writes in response to a
failure would run unchecked.

So a repaired query goes through exactly the same validation as the first one. A
model that responds to "no such column" with a DROP TABLE is refused identically.
"""

import time

from sql_copilot.layer0_shared.llm_client import ModelUnavailable
from sql_copilot.layer0_shared.logging_setup import (
    current_request_id,
    get_logger,
    log_event,
    new_request_id,
)
from sql_copilot.layer0_shared.metrics import metrics
from sql_copilot.layer0_shared.usage_tracker import UsageAccumulator
from sql_copilot.layer1_config.settings import settings
from sql_copilot.layer2_models.schemas import AskRequest, AskResponse, AttemptTrace, QueryResult
from sql_copilot.layer3_database.schema_sql import ALLOWED_TABLES
from sql_copilot.layer4_schema.step1_introspect import get_schema
from sql_copilot.layer4_schema.step2_select_tables import schema_prompt_text, select_tables
from sql_copilot.layer5_generation.step1_generate import CANNOT_ANSWER, generate_sql, repair_sql
from sql_copilot.layer6_validation.step2_validate import validate
from sql_copilot.layer7_execution.step1_execute import execute
from sql_copilot.layer8_answer.step1_explain import explain

log = get_logger(__name__)


class CopilotService:
    """Answers analytics questions with SQL."""

    def __init__(self, database=None, model=None) -> None:
        if database is None:
            from sql_copilot.layer3_database.connection import get_database

            database = get_database()
        self.database = database

        if model is None:
            from sql_copilot.layer0_shared.llm_client import build_chat_client

            model = build_chat_client()
        self.model = model

    def ask(self, request: AskRequest) -> AskResponse:
        request_id = new_request_id()
        token = current_request_id.set(request_id)
        started = time.perf_counter()

        try:
            return self.run(request, request_id, started)
        except ModelUnavailable as error:
            # The model could not be reached at all. Say which problem it is,
            # rather than reporting it as a failed query - the person can fix an
            # empty account, and cannot fix a query that was never written.
            metrics.increment("model_unavailable_total")
            metrics.increment("model_unavailable_" + error.kind + "_total")
            log_event(log, "model.unavailable", kind=error.kind, detail=str(error))

            return AskResponse(
                question=request.question,
                answered=False,
                answer=str(error),
                refused=False,
                schema_tables_offered=[],
                usage=UsageAccumulator().to_report(
                    int((time.perf_counter() - started) * 1000)
                ),
                request_id=request_id,
            )
        finally:
            current_request_id.reset(token)

    def run(self, request: AskRequest, request_id: str, started: float) -> AskResponse:
        metrics.increment("questions_total")
        usage = UsageAccumulator()

        question = request.question.strip()
        max_rows = request.max_rows
        if max_rows is None:
            max_rows = settings.max_rows

        # ---------- 1. which tables ----------
        schema = get_schema(self.database)
        tables = select_tables(question, schema, settings.schema_tables_in_prompt)
        schema_text = schema_prompt_text(tables)

        offered: list[str] = []
        for table in tables:
            offered.append(table.name)

        log_event(log, "question.received", question=question[:120], tables_offered=offered)

        attempts: list[AttemptTrace] = []

        # ---------- 2. generate ----------
        sql = generate_sql(question, schema_text, self.model, usage)

        if sql == "" or sql == CANNOT_ANSWER:
            metrics.increment("questions_unanswerable_total")
            return self.cannot_answer(
                question=question,
                offered=offered,
                attempts=attempts,
                usage=usage,
                started=started,
                request_id=request_id,
                message=(
                    "I could not write a query for that from the tables available. "
                    "The tables I can see are: " + ", ".join(sorted(ALLOWED_TABLES)) + "."
                ),
            )

        # ---------- 3 to 5. validate, execute, repair ----------
        attempt_number = 0
        repaired_from = ""
        last_result: QueryResult | None = None

        while attempt_number <= settings.max_repair_attempts:
            attempt_number = attempt_number + 1
            attempt_started = time.perf_counter()

            # VALIDATION RUNS ON EVERY ATTEMPT, including repairs.
            validation = validate(sql, ALLOWED_TABLES, max_rows)

            trace = AttemptTrace(
                attempt=attempt_number,
                sql=sql,
                validation=validation,
                repaired_from=repaired_from,
            )

            if not validation.allowed:
                metrics.increment("queries_refused_total")
                metrics.increment("refused_" + (validation.reason.value if validation.reason is not None else "unspecified") + "_total")
                trace.error = validation.message
                trace.seconds = round(time.perf_counter() - attempt_started, 4)
                attempts.append(trace)

                log_event(
                    log,
                    "query.refused",
                    reason=validation.reason.value if validation.reason is not None else "unspecified",
                    sql=sql[:200],
                    attempt=attempt_number,
                )

                # A refusal is not a bug to repair. The query asked for something
                # it is not allowed to have, and asking again will not change that.
                return self.refused(
                    question=question,
                    validation=validation,
                    offered=offered,
                    attempts=attempts,
                    usage=usage,
                    started=started,
                    request_id=request_id,
                )

            # ---------- execute ----------
            result = execute(self.database, validation.sql, max_rows)
            last_result = result
            trace.executed = True
            trace.seconds = round(time.perf_counter() - attempt_started, 4)

            if result.ok:
                attempts.append(trace)
                return self.answered(
                    question=question,
                    sql=validation.sql,
                    result=result,
                    validation=validation,
                    offered=offered,
                    attempts=attempts,
                    usage=usage,
                    started=started,
                    request_id=request_id,
                    repaired=attempt_number > 1,
                )

            trace.error = result.error
            attempts.append(trace)

            # ---------- repair ----------
            if not result.error_is_repairable:
                break
            if attempt_number > settings.max_repair_attempts:
                break

            metrics.increment("repairs_attempted_total")
            repaired_from = validation.sql

            sql = repair_sql(
                question=question,
                schema_text=schema_text,
                failed_sql=validation.sql,
                error=result.error,
                model=self.model,
                usage=usage,
            )

            if sql == "" or sql == CANNOT_ANSWER:
                break

        # ---------- ran out of attempts ----------
        metrics.increment("questions_failed_total")

        error_text = "the query could not be run"
        if last_result is not None:
            error_text = last_result.error

        return self.cannot_answer(
            question=question,
            offered=offered,
            attempts=attempts,
            usage=usage,
            started=started,
            request_id=request_id,
            message="The query failed and I could not fix it: " + error_text,
        )

    # ------------------------------------------------------------------

    def answered(
        self, question, sql, result, validation, offered, attempts, usage, started, request_id, repaired
    ) -> AskResponse:
        explanation = ""
        signals: list[str] = []

        if len(result.rows) >= 0:
            explanation, signals = explain(question, sql, result, self.model, usage)

        if repaired:
            metrics.increment("repairs_succeeded_total")
        metrics.increment("questions_answered_total")

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        metrics.observe("question_latency_ms", elapsed_ms)
        metrics.observe("cost_usd_per_question", usage.cost_usd)

        if len(signals) > 0:
            explanation = (
                explanation
                + "\n\nNote: one or more rows in this result contain text that reads like "
                "an instruction. It is data stored in the database, it is shown above "
                "unchanged, and nothing in this system acts on it."
            )

        log_event(
            log,
            "question.answered",
            rows=result.row_count,
            repaired=repaired,
            attempts=len(attempts),
            latency_ms=elapsed_ms,
            cost_usd=round(usage.cost_usd, 8),
        )

        return AskResponse(
            question=question,
            answered=True,
            answer=explanation,
            sql=sql,
            result=result,
            tables_used=validation.tables_used,
            schema_tables_offered=offered,
            attempts=attempts,
            repaired=repaired,
            usage=usage.to_report(elapsed_ms),
            request_id=request_id,
        )

    def refused(self, question, validation, offered, attempts, usage, started, request_id) -> AskResponse:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        metrics.observe("question_latency_ms", elapsed_ms)

        return AskResponse(
            question=question,
            answered=False,
            refused=True,
            refusal_reason=validation.reason.value if validation.reason is not None else "unspecified",
            refusal_message=validation.message,
            sql=validation.original_sql,
            schema_tables_offered=offered,
            attempts=attempts,
            usage=usage.to_report(elapsed_ms),
            request_id=request_id,
        )

    def cannot_answer(self, question, offered, attempts, usage, started, request_id, message) -> AskResponse:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        metrics.observe("question_latency_ms", elapsed_ms)

        return AskResponse(
            question=question,
            answered=False,
            answer=message,
            schema_tables_offered=offered,
            attempts=attempts,
            usage=usage.to_report(elapsed_ms),
            request_id=request_id,
        )
