"""
LAYER 3 - THE STORE
===================
Traces, cache entries and experiments.

The method worth reading is `spend_today`. It is a SUM over traces rather than a
counter, which is slower and correct - and it runs on every request, which is
why the index exists. A gateway that gets measurably slower the longer it stays
up is one nobody leaves running.
"""

import json
import sqlite3
import uuid
from datetime import timedelta
from pathlib import Path

from llm_gateway.layer2_models.schemas import (
    Attempt,
    CacheStatus,
    Experiment,
    Outcome,
    Trace,
    Variant,
    now_text,
    now_utc,
)

SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"


def new_request_id() -> str:
    return "req_" + uuid.uuid4().hex[:14]


def to_json(value) -> str:
    return json.dumps(value)


def from_json(text: str, fallback):
    if text is None or text.strip() == "":
        return fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return fallback


class GatewayStore:
    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA_FILE.read_text())
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    # ================================================================
    #  Traces
    # ================================================================

    def record(self, trace: Trace) -> None:
        attempts = []
        for attempt in trace.attempt_list:
            attempts.append(attempt.model_dump(mode="json"))

        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO traces
                    (request_id, at, day, api_key_owner, task, route, prompt_hash,
                     prompt_preview, outcome, message, cache, provider, model,
                     attempts_json, experiment, variant, score, input_tokens,
                     output_tokens, cost_usd, seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (trace.request_id, trace.at, trace.at[:10], trace.api_key_owner,
                 trace.task, trace.route, trace.prompt_hash, trace.prompt_preview,
                 trace.outcome.value, trace.message, trace.cache.value,
                 trace.provider, trace.model, to_json(attempts),
                 trace.experiment, trace.variant, trace.score,
                 trace.input_tokens, trace.output_tokens, trace.cost_usd,
                 trace.seconds),
            )

    def trace_from_row(self, row) -> Trace:
        attempts = []
        for entry in from_json(row["attempts_json"], []):
            attempts.append(Attempt(**entry))

        return Trace(
            request_id=row["request_id"], at=row["at"],
            api_key_owner=row["api_key_owner"], task=row["task"], route=row["route"],
            prompt_hash=row["prompt_hash"], prompt_preview=row["prompt_preview"],
            outcome=Outcome(row["outcome"]), message=row["message"],
            cache=CacheStatus(row["cache"]), provider=row["provider"],
            model=row["model"], attempt_list=attempts,
            experiment=row["experiment"], variant=row["variant"], score=row["score"],
            input_tokens=row["input_tokens"], output_tokens=row["output_tokens"],
            cost_usd=row["cost_usd"], seconds=row["seconds"],
        )

    def get_trace(self, request_id: str) -> Trace | None:
        row = self.connection.execute(
            "SELECT * FROM traces WHERE request_id = ?", (request_id,)).fetchone()
        if row is None:
            return None
        return self.trace_from_row(row)

    def recent_traces(self, limit: int = 100, owner: str = "",
                      experiment: str = "") -> list[Trace]:
        clauses = []
        parameters: list = []
        if owner != "":
            clauses.append("api_key_owner = ?")
            parameters.append(owner)
        if experiment != "":
            clauses.append("experiment = ?")
            parameters.append(experiment)
        where = (" WHERE " + " AND ".join(clauses)) if len(clauses) > 0 else ""
        parameters.append(limit)

        rows = self.connection.execute(
            "SELECT * FROM traces" + where + " ORDER BY at DESC LIMIT ?",
            parameters).fetchall()
        traces = []
        for row in rows:
            traces.append(self.trace_from_row(row))
        return traces

    def set_score(self, request_id: str, score: float) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE traces SET score = ? WHERE request_id = ?", (score, request_id))
        return cursor.rowcount == 1

    def spend_today(self, owner: str) -> float:
        """
        What this key has spent today. Summed, never counted.

        Runs on every request, which is why traces_spend exists.
        """
        today = now_utc().isoformat()[:10]
        row = self.connection.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM traces "
            " WHERE api_key_owner = ? AND day = ?", (owner, today)).fetchone()
        return round(float(row["total"]), 8)

    def totals(self, since_hours: float = 24.0) -> dict:
        since = (now_utc() - timedelta(hours=since_hours)).isoformat()
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS requests,
                   COALESCE(SUM(cost_usd), 0) AS cost,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(AVG(seconds), 0) AS mean_seconds,
                   SUM(CASE WHEN cache != 'miss' AND cache != 'disabled'
                            AND cache != 'not_cacheable' THEN 1 ELSE 0 END) AS cache_hits,
                   SUM(CASE WHEN outcome = 'ok' THEN 1 ELSE 0 END) AS ok,
                   SUM(CASE WHEN outcome = 'refused' THEN 1 ELSE 0 END) AS refused,
                   SUM(CASE WHEN outcome = 'failed' THEN 1 ELSE 0 END) AS failed
              FROM traces WHERE at >= ?
            """, (since,)).fetchone()
        return dict(row)

    def latency_percentiles(self, since_hours: float = 24.0) -> dict:
        """
        p50 and p95, computed here rather than averaged.

        A mean latency hides the shape entirely: two seconds average could be
        everything taking two seconds, or 95% taking half a second and one
        request in twenty taking thirty. Only one of those is a problem, and
        the mean cannot tell you which you have.
        """
        since = (now_utc() - timedelta(hours=since_hours)).isoformat()
        rows = self.connection.execute(
            "SELECT seconds FROM traces WHERE at >= ? AND outcome = 'ok' "
            " ORDER BY seconds", (since,)).fetchall()

        values = []
        for row in rows:
            values.append(row["seconds"])

        if len(values) == 0:
            return {"count": 0, "p50": 0.0, "p95": 0.0, "max": 0.0}

        def at_percentile(fraction: float) -> float:
            position = int(round((len(values) - 1) * fraction))
            return round(values[position], 4)

        return {"count": len(values), "p50": at_percentile(0.5),
                "p95": at_percentile(0.95), "max": round(values[-1], 4)}

    def by_model(self, since_hours: float = 24.0) -> list[dict]:
        since = (now_utc() - timedelta(hours=since_hours)).isoformat()
        rows = self.connection.execute(
            """
            SELECT model, provider, COUNT(*) AS requests,
                   COALESCE(SUM(cost_usd), 0) AS cost,
                   COALESCE(AVG(seconds), 0) AS mean_seconds
              FROM traces WHERE at >= ? AND model != '' GROUP BY model, provider
             ORDER BY cost DESC
            """, (since,)).fetchall()
        found = []
        for row in rows:
            found.append(dict(row))
        return found

    def by_owner(self, since_hours: float = 24.0) -> list[dict]:
        since = (now_utc() - timedelta(hours=since_hours)).isoformat()
        rows = self.connection.execute(
            """
            SELECT api_key_owner AS owner, COUNT(*) AS requests,
                   COALESCE(SUM(cost_usd), 0) AS cost
              FROM traces WHERE at >= ? GROUP BY api_key_owner ORDER BY cost DESC
            """, (since,)).fetchall()
        found = []
        for row in rows:
            found.append(dict(row))
        return found

    # ================================================================
    #  Cache
    # ================================================================

    def put_cache(self, cache_key: str, scope: str, prompt: str, response_text: str,
                  model: str, provider: str, input_tokens: int, output_tokens: int,
                  embedding: list[float], ttl_seconds: float) -> None:
        expires_at = (now_utc() + timedelta(seconds=ttl_seconds)).isoformat()
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO cache_entries
                    (cache_key, scope, prompt, response_text, model, provider,
                     input_tokens, output_tokens, embedding_json, created_at,
                     expires_at, hits)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (cache_key, scope, prompt, response_text, model, provider,
                 input_tokens, output_tokens, to_json(embedding), now_text(),
                 expires_at))

    def get_cache(self, cache_key: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM cache_entries WHERE cache_key = ?", (cache_key,)).fetchone()
        if row is None:
            return None
        if row["expires_at"] != "" and row["expires_at"] <= now_text():
            return None
        return dict(row)

    def cache_candidates(self, scope: str, limit: int = 500) -> list[dict]:
        """
        Every live entry IN THIS SCOPE, for the semantic comparison.

        The scope is in the WHERE clause, not applied afterwards. Filtering after
        the comparison is how project 01 leaked one permission level's answers to
        another: the match was found first and the check came second, and a check
        that comes second is a check somebody will eventually forget.
        """
        rows = self.connection.execute(
            "SELECT * FROM cache_entries WHERE scope = ? "
            " AND (expires_at = '' OR expires_at > ?) ORDER BY created_at DESC LIMIT ?",
            (scope, now_text(), limit)).fetchall()
        found = []
        for row in rows:
            found.append(dict(row))
        return found

    def note_cache_hit(self, cache_key: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE cache_entries SET hits = hits + 1 WHERE cache_key = ?",
                (cache_key,))

    def purge_expired_cache(self) -> int:
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM cache_entries WHERE expires_at != '' AND expires_at <= ?",
                (now_text(),))
        return cursor.rowcount

    def cache_size(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS n FROM cache_entries").fetchone()
        return int(row["n"])

    # ================================================================
    #  Experiments
    # ================================================================

    def save_experiment(self, experiment: Experiment) -> None:
        variants = []
        for variant in experiment.variants:
            variants.append(variant.model_dump(mode="json"))
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO experiments
                    (name, question, variants_json, active, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (experiment.name, experiment.question, to_json(variants),
                 1 if experiment.active else 0, experiment.created_at))

    def get_experiment(self, name: str) -> Experiment | None:
        row = self.connection.execute(
            "SELECT * FROM experiments WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        variants = []
        for entry in from_json(row["variants_json"], []):
            variants.append(Variant(**entry))
        return Experiment(name=row["name"], question=row["question"],
                          variants=variants, active=bool(row["active"]),
                          created_at=row["created_at"])

    def list_experiments(self) -> list[Experiment]:
        rows = self.connection.execute(
            "SELECT name FROM experiments ORDER BY created_at DESC").fetchall()
        found = []
        for row in rows:
            experiment = self.get_experiment(row["name"])
            if experiment is not None:
                found.append(experiment)
        return found

    def experiment_samples(self, name: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT variant, score, cost_usd, seconds, outcome
              FROM traces WHERE experiment = ? AND variant != ''
            """, (name,)).fetchall()
        found = []
        for row in rows:
            found.append(dict(row))
        return found

    def clear(self) -> None:
        with self.connection:
            for table in ("traces", "cache_entries", "experiments"):
                self.connection.execute("DELETE FROM %s" % table)
