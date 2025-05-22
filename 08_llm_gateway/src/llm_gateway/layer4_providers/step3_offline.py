"""
LAYER 4, STEP 3 - THE OFFLINE PROVIDERS
=======================================
Deterministic stand-ins, so that every path through this gateway - routing,
fallback, caching, limits, cost, and the whole A/B machinery - can be measured
with no key and no spending.

They are stand-ins, not stubs. Two things make them useful rather than merely
present:

1. THEY DIFFER FROM EACH OTHER. `offline-fast` is quick and gets a
   straightforward question right most of the time. `offline-strong` is slower
   and gets more right. Without that difference, routing between them would be
   a choice with no consequences and an A/B test would have nothing to find.

2. THEY ARE AFFECTED BY THE PROMPT. A system prompt that asks for working-out
   raises the hit rate. That is the thing an A/B test is FOR, and if the offline
   provider ignored the prompt then the experiment machinery could only ever be
   tested against noise.

Correctness is pseudo-random but deterministic: hashing the question, the system
prompt and the model gives a number in [0, 1), which is compared against that
combination's skill. The same question asked the same way always gets the same
answer, so a measurement taken twice is the same measurement - and a test that
is flaky for reasons of its own is worse than no test.
"""

import hashlib
import re
import time

from llm_gateway.layer2_models.schemas import FailureKind
from llm_gateway.layer4_providers.step2_base import (
    Provider,
    ProviderError,
    ProviderReply,
)

# How good each model is at a question it can answer, before the prompt helps.
BASE_SKILL = {
    "offline-fast": 0.55,
    "offline-strong": 0.75,
}

# What a system prompt asking for reasoning is worth. This is the effect an A/B
# test is meant to find, and it is set to something REAL but not enormous -
# eighteen points - because an effect you can see in twenty samples would not
# teach anybody anything about significance.
REASONING_BONUS = 0.18
REASONING_WORDS = ("step by step", "step-by-step", "show your working",
                   "think it through", "reason carefully")

# Roughly how long each model takes, so latency percentiles have a shape.
BASE_SECONDS = {
    "offline-fast": 0.004,
    "offline-strong": 0.012,
}


def unit_hash(*parts: str) -> float:
    """A stable number in [0, 1) from any strings."""
    digest = hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(16 ** 12)


def count_tokens(text: str) -> int:
    """
    A rough token count: about four characters each.

    Rough on purpose. The gateway uses the provider's own count whenever it is
    given one, and only falls back to this when there is nothing better - so a
    number that is approximately right and obviously approximate beats a
    tokeniser that is exactly right for one vendor and wrong for the next.
    """
    if text == "":
        return 0
    return max(1, len(text) // 4)


class OfflineProvider(Provider):
    name = "offline"

    def __init__(self) -> None:
        # Failure injection, so fallback and retry can be exercised on demand.
        # Set by the caller; the gateway itself never touches these.
        self.fail_times: dict[str, int] = {}
        self.failures_used: dict[str, int] = {}
        self.fail_kind = FailureKind.UNREACHABLE

    def models(self) -> list[str]:
        return ["offline-fast", "offline-strong"]

    def break_model(self, model: str, times: int,
                    kind: FailureKind = FailureKind.UNREACHABLE) -> None:
        self.fail_times[model] = times
        self.failures_used[model] = 0
        self.fail_kind = kind

    def mend(self) -> None:
        self.fail_times = {}
        self.failures_used = {}

    def should_fail(self, model: str) -> bool:
        budget = self.fail_times.get(model, 0)
        if budget <= 0:
            return False
        used = self.failures_used.get(model, 0)
        if used >= budget:
            return False
        self.failures_used[model] = used + 1
        return True

    def skill_for(self, model: str, system: str) -> float:
        skill = BASE_SKILL.get(model, 0.5)
        lowered = system.lower()
        for phrase in REASONING_WORDS:
            if phrase in lowered:
                skill = skill + REASONING_BONUS
                break
        if skill > 0.98:
            skill = 0.98
        return skill

    def complete(self, system: str, prompt: str, model: str,
                 max_tokens: int = 500, temperature: float = 0.0) -> ProviderReply:
        if self.should_fail(model):
            raise ProviderError(
                "the offline provider was told to fail for %s" % model,
                self.fail_kind)

        if model not in self.models():
            raise ProviderError("this provider has no model called %r" % model,
                                FailureKind.BAD_REQUEST)

        time.sleep(BASE_SECONDS.get(model, 0.005))

        answer = self.answer(system, prompt, model)
        return ProviderReply(
            text=answer, model=model,
            input_tokens=count_tokens(system) + count_tokens(prompt),
            output_tokens=count_tokens(answer))

    def answer(self, system: str, prompt: str, model: str) -> str:
        """
        Solve the question, or get it wrong in a plausible way.

        The benchmark questions are small arithmetic word problems, so an answer
        is checkable - which is what lets the A/B machinery be tested against a
        real success rate rather than against a judgement call.
        """
        correct = solve(prompt)
        if correct is None:
            return "I do not have enough information to answer that."

        roll = unit_hash(prompt, system, model)
        if roll < self.skill_for(model, system):
            value = correct
        else:
            # A wrong answer that looks like an answer. Off by a plausible
            # amount, because an obviously silly one would be caught by
            # anything and would make the grader look better than it is.
            value = correct + (1 if roll < 0.5 else -1) * max(1, int(correct * 0.1))

        lowered = system.lower()
        shows_working = False
        for phrase in REASONING_WORDS:
            if phrase in lowered:
                shows_working = True
                break

        if shows_working:
            return ("Working through it: the quantities combine as described.\n"
                    "The answer is %d." % value)
        return "The answer is %d." % value

    def embed(self, text: str) -> list[float]:
        return hashing_embedding(text)


NUMBER_PATTERN = re.compile(r"-?\d+")


def solve(prompt: str) -> int | None:
    """
    Work out the answer to one of the benchmark questions.

    Deliberately narrow: it understands the shapes the dataset uses and nothing
    else. A general arithmetic parser would be a second thing to get wrong, and
    this exists only so the offline provider has something true to be right or
    wrong about.
    """
    lowered = prompt.lower()
    numbers = []
    for match in NUMBER_PATTERN.finditer(prompt):
        numbers.append(int(match.group(0)))
    if len(numbers) < 2:
        return None

    if "each" in lowered and ("total" in lowered or "altogether" in lowered
                              or "how much" in lowered or "how many" in lowered):
        product = numbers[0] * numbers[1]
        if len(numbers) >= 3 and ("less" in lowered or "discount" in lowered
                                  or "returns" in lowered or "gives away" in lowered):
            return product - numbers[2]
        return product

    if "less" in lowered or "fewer" in lowered or "spends" in lowered \
            or "gives away" in lowered or "left" in lowered:
        total = numbers[0]
        for value in numbers[1:]:
            total = total - value
        return total

    total = 0
    for value in numbers:
        total = total + value
    return total


EMBEDDING_SIZE = 256


def hashing_embedding(text: str) -> list[float]:
    """
    A cheap embedding for the semantic cache, with no model.

    Word-level hashing into a fixed number of buckets, then normalised. It knows
    nothing about meaning - "cheap" and "inexpensive" land in different buckets -
    so it finds rewordings and reorderings and not synonyms. That limitation is
    worth stating plainly rather than letting the offline threshold be mistaken
    for the live one: `scripts/tune_threshold.py` measures each separately.
    """
    vector = [0.0] * EMBEDDING_SIZE
    words = re.findall(r"[a-z0-9]+", text.lower())
    if len(words) == 0:
        return vector

    for word in words:
        bucket = int(hashlib.sha256(word.encode("utf-8")).hexdigest()[:8], 16) % EMBEDDING_SIZE
        vector[bucket] = vector[bucket] + 1.0

    total = 0.0
    for value in vector:
        total = total + value * value
    length = total ** 0.5
    if length == 0:
        return vector

    normalised = []
    for value in vector:
        normalised.append(value / length)
    return normalised


def cosine_similarity(first: list[float], second: list[float]) -> float:
    if len(first) != len(second) or len(first) == 0:
        return 0.0
    total = 0.0
    for index in range(len(first)):
        total = total + first[index] * second[index]
    if total < -1.0:
        return -1.0
    if total > 1.0:
        return 1.0
    return total
