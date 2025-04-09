"""
LAYER 0 - SHARED: THE MODEL CLIENT
==================================
Two implementations of one interface.

    OpenAiChatClient     real calls.
    OfflineSqlGenerator  no network. Writes SQL from keyword templates.

------------------------------------------------------------------------------
BE CLEAR ABOUT WHAT THE OFFLINE ONE IS AND IS NOT
------------------------------------------------------------------------------
It is NOT a natural-language-to-SQL system. It matches phrases against a handful
of templates written for this schema, and it will not answer a question nobody
anticipated.

What it IS for: running the part of this project that actually matters with no
API key and no bill. Validation, the read-only sandbox, timeouts, row caps, the
repair loop, the refusal paths and the whole safety test suite all need SQL to
operate on - they do not care where it came from. A wrong query is as useful for
testing a validator as a right one, and rather more useful for testing a repair
loop.

So: the offline generator tests the safety machinery, and a real model tests
whether the answers are right. The evaluation suite reports the two separately.
"""

import re
import time

from sql_copilot.layer0_shared.cost import chat_cost_usd, rough_token_count

TASK_GENERATE = "generate_sql"
TASK_REPAIR = "repair_sql"
TASK_EXPLAIN = "explain_result"


class ModelUnavailable(Exception):
    """
    The model could not be reached at all.

    Kept separate from "the model produced something unusable", because the two
    need completely different handling. Bad output can be validated, repaired or
    refused. No output at all means the question cannot be answered by any amount
    of retrying, and the person deserves to be told which of the two happened -
    "the query failed" is a misleading thing to say when the real problem is an
    empty account or an expired key.
    """

    def __init__(self, message: str, kind: str = "unavailable") -> None:
        super().__init__(message)
        self.kind = kind


class LlmResult:
    def __init__(
        self,
        text: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: int = 0,
    ) -> None:
        self.text = text
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cost_usd = cost_usd
        self.latency_ms = latency_ms

    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class OpenAiChatClient:
    is_live = True

    def __init__(self, api_key: str, model: str, temperature: float) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, timeout=60.0, max_retries=2)
        self.model = model
        self.temperature = temperature

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        task: str = "",
        max_tokens: int = 700,
    ) -> LlmResult:
        import openai

        started = time.perf_counter()

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except openai.RateLimitError as error:
            # Two very different problems share this status code. An empty
            # account will never succeed however long you wait; a rate limit
            # will. Saying "rate limited, try again" to someone with no credit
            # sends them to wait for something that is not going to happen.
            message = str(error)
            if "insufficient_quota" in message or "credit" in message.lower():
                raise ModelUnavailable(
                    "The OpenAI account has no credits remaining, so no query "
                    "could be generated. Add credits, or run with "
                    "LLM_PROVIDER=offline.",
                    kind="no_credit",
                ) from None
            raise ModelUnavailable(
                "The model is rate limited right now. Try again shortly.",
                kind="rate_limited",
            ) from None
        except openai.AuthenticationError:
            raise ModelUnavailable(
                "The OpenAI API key was rejected. Check OPENAI_API_KEY in .env.",
                kind="bad_key",
            ) from None
        except openai.APIConnectionError:
            raise ModelUnavailable(
                "Could not reach the OpenAI API. Check the network connection.",
                kind="unreachable",
            ) from None
        except openai.APIStatusError as error:
            raise ModelUnavailable(
                "The OpenAI API returned an error (%s)." % error.status_code,
                kind="api_error",
            ) from None

        choice = response.choices[0]

        text = choice.message.content
        if text is None:
            text = ""

        prompt_tokens = 0
        completion_tokens = 0
        if response.usage is not None:
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens

        return LlmResult(
            text=text.strip(),
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=chat_cost_usd(self.model, prompt_tokens, completion_tokens),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


# The revenue expression, written once. Getting it wrong in one template and
# right in another is how a benchmark produces numbers nobody can reconcile.
REVENUE = "ROUND(SUM(oi.quantity * oi.unit_price * (1 - oi.discount)), 2)"

YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")


class OfflineSqlGenerator:
    """Writes SQL from keyword templates. See the note at the top of the file."""

    is_live = False
    model = "offline-sql-templates"

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        task: str = "",
        max_tokens: int = 700,
    ) -> LlmResult:
        started = time.perf_counter()

        if task == TASK_GENERATE:
            text = self.generate(user_prompt)
        elif task == TASK_REPAIR:
            text = self.repair(user_prompt)
        elif task == TASK_EXPLAIN:
            text = self.explain(user_prompt)
        else:
            text = ""

        return LlmResult(
            text=text,
            model=self.model,
            prompt_tokens=rough_token_count(system_prompt) + rough_token_count(user_prompt),
            completion_tokens=rough_token_count(text),
            cost_usd=0.0,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    # ------------------------------------------------------------------

    def question_from(self, user_prompt: str) -> str:
        """The prompt carries the question after a QUESTION: marker."""
        for line in user_prompt.splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("QUESTION:"):
                return stripped[len("QUESTION:") :].strip()
        return user_prompt.strip()

    def year_filter(self, question: str, column: str) -> str:
        found = YEAR_PATTERN.search(question)
        if found is None:
            return ""
        return " WHERE %s >= '%s-01-01' AND %s <= '%s-12-31'" % (
            column, found.group(1), column, found.group(1)
        )

    def generate(self, user_prompt: str) -> str:
        question = self.question_from(user_prompt).lower()

        # --- the table that is not on the allowlist ---
        # Written on purpose, so the refusal path is exercised with no API key.
        if "salary" in question or "salaries" in question or "paid" in question:
            return "SELECT name, role, salary FROM employee_salaries ORDER BY salary DESC"

        # ORDER MATTERS HERE, and getting it wrong is a real bug I made.
        # "How many customers are in each segment?" matched the plain customer
        # count first and returned a single number. "Total marketing spend by
        # channel" matched a generic "by channel" rule and counted orders. The
        # most distinctive phrase has to be checked first - the same first-match
        # mistake as the tool rules in project 03.

        # --- the most specific subjects first ---
        if "marketing" in question or "spend" in question:
            return (
                "SELECT channel, ROUND(SUM(amount), 2) AS total_spend FROM marketing_spend "
                "GROUP BY channel ORDER BY total_spend DESC"
            )

        if "segment" in question:
            return (
                "SELECT segment, COUNT(*) AS customer_count FROM customers "
                "GROUP BY segment ORDER BY customer_count DESC"
            )

        if "return reason" in question or "reasons" in question:
            return (
                "SELECT reason, COUNT(*) AS return_count FROM returns "
                "GROUP BY reason ORDER BY return_count DESC"
            )

        # --- counts ---
        if "how many customers" in question or "number of customers" in question:
            return "SELECT COUNT(*) AS customer_count FROM customers"

        if "how many orders" in question or "number of orders" in question or "count of orders" in question:
            where = self.year_filter(question, "order_date")

            for status in ["cancelled", "delivered", "shipped", "placed"]:
                if status in question:
                    if where == "":
                        where = " WHERE status = '%s'" % status
                    else:
                        where = where + " AND status = '%s'" % status
                    break

            return "SELECT COUNT(*) AS order_count FROM orders" + where

        if "how many returns" in question or "number of returns" in question:
            return "SELECT COUNT(*) AS return_count FROM returns"

        if "how many products" in question:
            return "SELECT COUNT(*) AS product_count FROM products"

        # --- grouped revenue ---
        if "revenue" in question or "sales" in question:
            year_clause = self.year_filter(question, "o.order_date")

            if "by channel" in question:
                return (
                    "SELECT o.channel, %s AS revenue FROM order_items oi "
                    "JOIN orders o ON oi.order_id = o.order_id%s "
                    "GROUP BY o.channel ORDER BY revenue DESC" % (REVENUE, year_clause)
                )

            if "by country" in question:
                return (
                    "SELECT c.country, %s AS revenue FROM order_items oi "
                    "JOIN orders o ON oi.order_id = o.order_id "
                    "JOIN customers c ON o.customer_id = c.customer_id%s "
                    "GROUP BY c.country ORDER BY revenue DESC" % (REVENUE, year_clause)
                )

            if "by category" in question:
                return (
                    "SELECT cat.name AS category, %s AS revenue FROM order_items oi "
                    "JOIN products p ON oi.product_id = p.product_id "
                    "JOIN categories cat ON p.category_id = cat.category_id "
                    "JOIN orders o ON oi.order_id = o.order_id%s "
                    "GROUP BY cat.name ORDER BY revenue DESC" % (REVENUE, year_clause)
                )

            if "by month" in question:
                return (
                    "SELECT substr(o.order_date, 1, 7) AS month, %s AS revenue "
                    "FROM order_items oi JOIN orders o ON oi.order_id = o.order_id%s "
                    "GROUP BY month ORDER BY month" % (REVENUE, year_clause)
                )

            if "top" in question and "product" in question:
                return (
                    "SELECT p.name, %s AS revenue FROM order_items oi "
                    "JOIN products p ON oi.product_id = p.product_id "
                    "JOIN orders o ON oi.order_id = o.order_id%s "
                    "GROUP BY p.name ORDER BY revenue DESC LIMIT 5" % (REVENUE, year_clause)
                )

            if "top" in question and "customer" in question:
                return (
                    "SELECT c.name, %s AS revenue FROM order_items oi "
                    "JOIN orders o ON oi.order_id = o.order_id "
                    "JOIN customers c ON o.customer_id = c.customer_id%s "
                    "GROUP BY c.name ORDER BY revenue DESC LIMIT 5" % (REVENUE, year_clause)
                )

            return (
                "SELECT %s AS revenue FROM order_items oi "
                "JOIN orders o ON oi.order_id = o.order_id%s" % (REVENUE, year_clause)
            )

        # --- averages ---
        if "average order value" in question or "average order" in question:
            return (
                "SELECT ROUND(AVG(order_total), 2) AS average_order_value FROM ("
                "SELECT oi.order_id, SUM(oi.quantity * oi.unit_price * (1 - oi.discount)) AS order_total "
                "FROM order_items oi GROUP BY oi.order_id) t"
            )

        # --- breakdowns that are not about money ---
        if "orders by channel" in question or "by channel" in question:
            return "SELECT channel, COUNT(*) AS order_count FROM orders GROUP BY channel ORDER BY order_count DESC"

        # Nothing matched. Saying so is better than inventing a query.
        return ""

    def repair(self, user_prompt: str) -> str:
        """
        The template generator cannot repair its own SQL - it has no idea why the
        database complained. Returning nothing lets the repair loop record an
        honest failure, which is itself worth testing.
        """
        return ""

    def explain(self, user_prompt: str) -> str:
        """Describe the result table without interpreting it."""
        rows_seen = 0
        for line in user_prompt.splitlines():
            if "|" in line and not line.strip().startswith("---"):
                rows_seen = rows_seen + 1

        if rows_seen <= 1:
            return "The query returned no rows."

        return (
            "The query returned %d rows. The table above shows the result exactly "
            "as the database returned it." % (rows_seen - 2)
        )


def build_chat_client():
    from sql_copilot.layer1_config.settings import settings

    if settings.using_real_llm():
        return OpenAiChatClient(
            api_key=settings.openai_api_key,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
        )
    return OfflineSqlGenerator()
