"""
LAYER 2 - DATA SHAPES
=====================
The objects every layer agrees on.

`Trace` is the one that matters. Every request through this gateway produces
exactly one, and it records not only what happened but what ALMOST happened:
which provider was tried first, why it was abandoned, whether the answer came
from a cache, how long each attempt took, and what it cost.

That is the difference between a gateway and a proxy. A proxy forwards requests.
A gateway is the one place in a system where you can answer "what is this
actually costing us, and is the new prompt better" - and you can only answer
those from records that were kept at the time. Nobody reconstructs a p95 latency
afterwards from application logs.
"""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_text() -> str:
    return now_utc().isoformat()


class Outcome(str, Enum):
    OK = "ok"
    REFUSED = "refused"            # a limit said no before anything was called
    FAILED = "failed"              # every provider was tried and none worked


class CacheStatus(str, Enum):
    MISS = "miss"
    EXACT = "exact"
    SEMANTIC = "semantic"
    DISABLED = "disabled"
    NOT_CACHEABLE = "not_cacheable"


class FailureKind(str, Enum):
    """
    Why a provider call failed, which decides what happens next.

    This is the same distinction project 07 draws about steps, and it matters
    here for the same reason: retrying a failure that cannot succeed costs the
    caller latency to arrive at the same answer more slowly. "No credit" will
    still be true in two seconds. "Rate limited" probably will not.
    """

    RATE_LIMITED = "rate_limited"       # retry, or fall back
    UNREACHABLE = "unreachable"         # retry, or fall back
    TIMEOUT = "timeout"                 # retry, or fall back
    NO_CREDIT = "no_credit"             # fall back, never retry
    BAD_KEY = "bad_key"                 # fall back, never retry
    BAD_REQUEST = "bad_request"         # neither - the request itself is wrong
    UNKNOWN = "unknown"

    def worth_retrying(self) -> bool:
        return self in (FailureKind.RATE_LIMITED, FailureKind.UNREACHABLE,
                        FailureKind.TIMEOUT, FailureKind.UNKNOWN)

    def worth_falling_back(self) -> bool:
        """A bad request will be just as bad at the next provider."""
        return self != FailureKind.BAD_REQUEST


class Attempt(BaseModel):
    """One call to one provider. A request may have several."""

    provider: str
    model: str
    ok: bool = False
    failure_kind: str = ""
    error: str = ""
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class ChatRequest(BaseModel):
    """What a caller sends."""

    prompt: str
    system: str = ""
    task: str = "general"          # what the routing rules match on
    model: str = ""                # an explicit choice, which overrides routing
    max_tokens: int = 500
    temperature: float = 0.0
    experiment: str = ""           # opt into an A/B test by name
    no_cache: bool = False
    scope: str = ""                # who may see a cached answer. See layer 6.


class ChatResponse(BaseModel):
    """What a caller gets back."""

    request_id: str
    text: str = ""
    outcome: Outcome = Outcome.OK
    message: str = ""

    provider: str = ""
    model: str = ""
    cache: CacheStatus = CacheStatus.MISS
    experiment: str = ""
    variant: str = ""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    attempts: int = 0


class Trace(BaseModel):
    """
    The record of one request, kept whether it worked or not.

    A gateway that only records successes cannot tell you why last Tuesday was
    expensive.
    """

    request_id: str
    at: str = Field(default_factory=now_text)
    api_key_owner: str = ""
    task: str = ""
    route: str = ""

    prompt_hash: str = ""
    prompt_preview: str = ""

    outcome: Outcome = Outcome.OK
    message: str = ""
    cache: CacheStatus = CacheStatus.MISS

    provider: str = ""
    model: str = ""
    attempt_list: list[Attempt] = Field(default_factory=list)

    experiment: str = ""
    variant: str = ""
    score: float | None = None      # filled in later, when someone grades it

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0

    def attempts_made(self) -> int:
        return len(self.attempt_list)

    def fell_back(self) -> bool:
        """
        Did the answer come from something other than the first choice?

        Compared by MODEL, not by provider. Falling back from gpt-4o-mini to
        A stronger model is a fallback - it costs many times more - and both are
        OpenAI, so comparing providers would have reported that nothing
        happened. The whole reason a dashboard shows this is to notice an
        expensive silent degradation, and comparing the wrong field made it
        invisible in exactly the case worth seeing.
        """
        if len(self.attempt_list) < 2:
            return False
        return self.attempt_list[0].model != self.model


class ModelPrice(BaseModel):
    """What a model costs, per million tokens, so the numbers stay readable."""

    name: str
    provider: str
    usd_per_million_input: float
    usd_per_million_output: float
    context_tokens: int = 128000

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        cost = (input_tokens / 1_000_000.0) * self.usd_per_million_input
        cost = cost + (output_tokens / 1_000_000.0) * self.usd_per_million_output
        return round(cost, 8)


class Route(BaseModel):
    """
    Which models to try, in order, for a kind of work.

    An ordered list rather than one model, because the second entry is the
    answer to "what happens when the first one is down" - and a gateway whose
    answer to that is "the request fails" is not buying you anything a direct
    SDK call does not.
    """

    name: str
    task: str
    chain: list[str] = Field(default_factory=list)
    reason: str = ""


class Variant(BaseModel):
    """One arm of an experiment."""

    name: str
    system: str = ""
    model: str = ""
    weight: float = 0.5


class Experiment(BaseModel):
    """Two ways of doing the same thing, and traffic split between them."""

    name: str
    question: str = ""
    variants: list[Variant] = Field(default_factory=list)
    active: bool = True
    created_at: str = Field(default_factory=now_text)


class ArmResult(BaseModel):
    """What one arm of an experiment achieved."""

    variant: str
    samples: int = 0
    scored: int = 0
    successes: int = 0
    success_rate: float = 0.0
    mean_cost_usd: float = 0.0
    mean_seconds: float = 0.0
    p95_seconds: float = 0.0


class ExperimentVerdict(BaseModel):
    """
    The answer, including "not yet".

    `winner` stays empty until the difference is larger than the noise. That is
    the whole point of this object: a platform that names a winner from twenty
    samples is a coin flip with extra steps.
    """

    experiment: str
    arms: list[ArmResult] = Field(default_factory=list)
    winner: str = ""
    confident: bool = False
    verdict: str = ""
    difference: float = 0.0
    confidence_interval: list[float] = Field(default_factory=list)
    samples_needed_per_arm: int = 0
