"""
LAYER 0 - SHARED: METRICS
=========================
The numbers that tell you whether the system is healthy:

    requests, failures, p50 / p95 latency, tokens per request,
    cost per request, cache hit rate, abstain rate.

Kept in memory on purpose - it is small, has no dependencies, and is enough to
show the idea. In a real deployment you would export these to Prometheus.
"""

import threading


class MetricsRegistry:
    """A tiny thread-safe collector of counters and value lists."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._samples: dict[str, list[float]] = {}

    # ---------- writing ----------

    def increment(self, name: str, amount: float = 1.0) -> None:
        """Add to a counter, for example 'requests_total'."""
        with self._lock:
            if name not in self._counters:
                self._counters[name] = 0.0
            self._counters[name] = self._counters[name] + amount

    def observe(self, name: str, value: float) -> None:
        """Record one measurement, for example a latency in milliseconds."""
        with self._lock:
            if name not in self._samples:
                self._samples[name] = []
            bucket = self._samples[name]
            bucket.append(value)
            # Keep memory bounded: remember the most recent 5000 samples.
            if len(bucket) > 5000:
                del bucket[0 : len(bucket) - 5000]

    # ---------- reading ----------

    def counter(self, name: str) -> float:
        with self._lock:
            return self._counters.get(name, 0.0)

    def percentile(self, name: str, fraction: float) -> float:
        """
        The value below which `fraction` of samples fall.
        percentile('latency_ms', 0.95) is the p95 latency.
        """
        with self._lock:
            if name not in self._samples:
                return 0.0
            values = sorted(self._samples[name])

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
        return round(total / len(values), 3)

    def snapshot(self) -> dict:
        """Everything, shaped for the /metrics endpoint and the UI dashboard."""
        with self._lock:
            counters = dict(self._counters)
            sample_names = list(self._samples.keys())

        requests_total = counters.get("requests_total", 0.0)
        cache_hits = counters.get("cache_hits_total", 0.0)
        abstains = counters.get("abstain_total", 0.0)
        failures = counters.get("failures_total", 0.0)

        if requests_total > 0:
            cache_hit_rate = round(cache_hits / requests_total, 4)
            abstain_rate = round(abstains / requests_total, 4)
            failure_rate = round(failures / requests_total, 4)
        else:
            cache_hit_rate = 0.0
            abstain_rate = 0.0
            failure_rate = 0.0

        latency = {}
        for name in sample_names:
            latency[name] = {
                "p50": self.percentile(name, 0.50),
                "p95": self.percentile(name, 0.95),
                "avg": self.average(name),
                "count": len(self._samples.get(name, [])),
            }

        return {
            "counters": counters,
            "rates": {
                "cache_hit_rate": cache_hit_rate,
                "abstain_rate": abstain_rate,
                "failure_rate": failure_rate,
            },
            "distributions": latency,
        }

    def reset(self) -> None:
        """Only used by tests."""
        with self._lock:
            self._counters.clear()
            self._samples.clear()


# One shared registry for the whole process.
metrics = MetricsRegistry()
