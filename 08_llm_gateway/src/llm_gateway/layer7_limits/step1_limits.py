"""
LAYER 7 - LIMITS
================
Two of them, doing different jobs.

    RATE LIMIT   how fast. A token bucket per key: it refills steadily and
                 holds a burst, so a caller may arrive with ten at once and
                 then must slow to the sustained rate.

    BUDGET       how much. A hard daily cap on spending per key.

The budget is the one that matters at three in the morning. A rate limit stops a
caller overwhelming the providers; a budget stops a loop with a bug in it
spending four thousand pounds overnight. They are not substitutes: sixty
requests a minute against an expensive model is well within any sensible rate
limit and will still empty the account by breakfast.

The budget is checked BEFORE the call, against money already spent. It can
therefore be exceeded by at most one request - the one in flight when the last
one tipped it over. Making that impossible would mean reserving the cost before
knowing it, and the cost is not known until the response arrives. Being one
request over is the honest cost of not guessing, and it is worth saying out loud
rather than claiming a hard cap the design cannot deliver.
"""

import time
from dataclasses import dataclass


@dataclass
class LimitDecision:
    allowed: bool = True
    reason: str = ""
    retry_after_seconds: float = 0.0
    spent_today: float = 0.0
    budget: float = 0.0


class TokenBucket:
    """
    Refills steadily, holds a burst.

    A fixed window - "sixty per minute" counted per clock minute - lets a caller
    send sixty at 10:00:59 and sixty more at 10:01:00, which is a hundred and
    twenty in two seconds against a limit of sixty a minute. A bucket has no
    window to sit on the edge of.
    """

    def __init__(self, per_minute: float, burst: float) -> None:
        self.rate_per_second = per_minute / 60.0
        self.capacity = max(1.0, burst)
        self.tokens = self.capacity
        self.last_refill = time.monotonic()

    def refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.last_refill = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_second)

    def take(self, amount: float = 1.0) -> tuple[bool, float]:
        """Returns (allowed, seconds to wait if not)."""
        self.refill()
        if self.tokens >= amount:
            self.tokens = self.tokens - amount
            return (True, 0.0)
        missing = amount - self.tokens
        if self.rate_per_second <= 0:
            return (False, 60.0)
        return (False, round(missing / self.rate_per_second, 2))


class Limiter:
    def __init__(self, store, requests_per_minute: float, burst: float,
                 daily_budget_usd: float) -> None:
        self.store = store
        self.requests_per_minute = requests_per_minute
        self.burst = burst
        self.daily_budget_usd = daily_budget_usd
        self.buckets: dict[str, TokenBucket] = {}

    def bucket_for(self, owner: str) -> TokenBucket:
        if owner not in self.buckets:
            self.buckets[owner] = TokenBucket(self.requests_per_minute, self.burst)
        return self.buckets[owner]

    def check(self, owner: str) -> LimitDecision:
        spent = self.store.spend_today(owner)

        # Budget first. A caller who is out of money should be told that, not
        # told to slow down - one of those is fixable by waiting and the other
        # is not, and the wrong message sends them into a retry loop.
        if self.daily_budget_usd > 0 and spent >= self.daily_budget_usd:
            return LimitDecision(
                allowed=False,
                reason=("this key has spent $%.4f today, which is at or over its "
                        "$%.2f daily budget. It resets at midnight UTC."
                        % (spent, self.daily_budget_usd)),
                spent_today=spent, budget=self.daily_budget_usd)

        allowed, wait = self.bucket_for(owner).take(1.0)
        if not allowed:
            return LimitDecision(
                allowed=False,
                reason=("this key is over its rate limit of %.0f requests a minute. "
                        "Try again in %.1f seconds."
                        % (self.requests_per_minute, wait)),
                retry_after_seconds=wait, spent_today=spent,
                budget=self.daily_budget_usd)

        return LimitDecision(allowed=True, spent_today=spent,
                             budget=self.daily_budget_usd)
