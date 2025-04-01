"""
LAYER 0 - SHARED: METRICS
=========================
What a team running this would watch:

    runs, runs that ran out of budget, sub-questions researched vs skipped,
    evidence collected, duplicates removed, conflicts found,
    p50 / p95 run time, tokens and cost per run, citation validity.

"Runs that ran out of budget" is the number to watch. Near zero means the budget
is generous and you are probably overspending. Near half means users are getting
partial answers and do not know it.
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
                "budget_exhausted_rate": self.divide_counters("runs_budget_exhausted_total", "runs_total"),
                "partial_report_rate": self.divide_counters("reports_partial_total", "runs_total"),
                "subquestion_skip_rate": self.divide_counters(
                    "subquestions_skipped_total", "subquestions_planned_total"
                ),
            },
            "distributions": distributions,
        }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._samples.clear()


metrics = MetricsRegistry()
