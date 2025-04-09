"""
LAYER 0 - SHARED: METRICS
=========================
What a team running this would watch:

    questions asked, queries refused (and by which rule), queries that failed,
    repairs attempted and repairs that worked, rows returned, p50/p95 latency,
    tokens and cost per question.

"Refused by rule" is the interesting one. A spike in `unknown_table` usually
means the schema retrieval is offering the wrong tables, not that anyone is
attacking you.
"""

import threading


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._samples: dict[str, list[float]] = {}

    def increment(self, name: str, amount: float = 1.0) -> None:
        with self._lock:
            if name not in self._counters:
                self._counters[name] = 0.0
            self._counters[name] = self._counters[name] + amount

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            if name not in self._samples:
                self._samples[name] = []
            bucket = self._samples[name]
            bucket.append(value)
            if len(bucket) > 2000:
                del bucket[0 : len(bucket) - 2000]

    def counter(self, name: str) -> float:
        with self._lock:
            return self._counters.get(name, 0.0)

    def percentile(self, name: str, fraction: float) -> float:
        with self._lock:
            values = sorted(self._samples.get(name, []))
        if len(values) == 0:
            return 0.0
        return round(values[int(round(fraction * (len(values) - 1)))], 3)

    def average(self, name: str) -> float:
        with self._lock:
            values = list(self._samples.get(name, []))
        if len(values) == 0:
            return 0.0
        total = 0.0
        for value in values:
            total = total + value
        return round(total / len(values), 4)

    def divide_counters(self, top: str, bottom: str) -> float:
        denominator = self.counter(bottom)
        if denominator == 0:
            return 0.0
        return round(self.counter(top) / denominator, 4)

    def snapshot(self) -> dict:
        with self._lock:
            counters = dict(self._counters)
            sample_names = list(self._samples.keys())

        distributions = {}
        for name in sample_names:
            distributions[name] = {
                "p50": self.percentile(name, 0.50),
                "p95": self.percentile(name, 0.95),
                "avg": self.average(name),
                "count": len(self._samples.get(name, [])),
            }

        return {
            "counters": counters,
            "rates": {
                "refusal_rate": self.divide_counters("queries_refused_total", "questions_total"),
                "answer_rate": self.divide_counters("questions_answered_total", "questions_total"),
                "repair_rate": self.divide_counters("repairs_attempted_total", "questions_total"),
                "repair_success_rate": self.divide_counters(
                    "repairs_succeeded_total", "repairs_attempted_total"
                ),
            },
            "distributions": distributions,
        }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._samples.clear()


metrics = MetricsRegistry()
