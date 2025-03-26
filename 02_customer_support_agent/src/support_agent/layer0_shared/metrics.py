"""
LAYER 0 - SHARED: METRICS
=========================
The numbers a support team actually watches:

    conversations, turns per conversation, resolution rate,
    escalation rate, tool-call success rate, refused tool calls,
    p50 / p95 latency, tokens and cost per turn, PII redactions.

Escalation rate is the one to watch. Zero means the agent is refusing to hand
over cases it cannot solve; very high means it is not solving anything. Neither
is visible from reading transcripts.
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
            if len(bucket) > 5000:
                del bucket[0 : len(bucket) - 5000]

    def counter(self, name: str) -> float:
        with self._lock:
            return self._counters.get(name, 0.0)

    def percentile(self, name: str, fraction: float) -> float:
        with self._lock:
            values = sorted(self._samples.get(name, []))
        if len(values) == 0:
            return 0.0
        position = int(round(fraction * (len(values) - 1)))
        return round(values[position], 3)

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
                "resolution_rate": self.divide_counters("resolved_total", "turns_total"),
                "escalation_rate": self.divide_counters("escalations_total", "conversations_total"),
                "tool_success_rate": self.divide_counters("tool_calls_ok_total", "tool_calls_total"),
                "tool_denied_rate": self.divide_counters("tool_calls_denied_total", "tool_calls_total"),
                "retry_rate": self.divide_counters("tool_retries_total", "tool_calls_total"),
            },
            "distributions": distributions,
        }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._samples.clear()


metrics = MetricsRegistry()
