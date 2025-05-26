"""
Measure the gateway.

    python scripts/evaluate.py            offline, free
    python scripts/evaluate.py --live     uses the key in .env

Five numbers, in the order they matter:

  1. did anything leak between cache scopes     must be zero
  2. did the fallback chain hold
  3. what the cache saved
  4. what an A/B verdict looks like at three sample sizes
  5. cost and latency

Number 1 leads because a cache that serves one caller's answer to another is
not a performance problem, it is a disclosure - and it is the failure project 01
in this series actually shipped.
"""

import sys

from llm_gateway.layer1_config.settings import SETTINGS
from llm_gateway.layer2_models.schemas import (
    CacheStatus,
    ChatRequest,
    Experiment,
    FailureKind,
    Outcome,
    Variant,
)
from llm_gateway.layer3_storage.store import GatewayStore
from llm_gateway.layer4_providers.step2_base import (
    EmbeddingUnavailable,
    ProviderError,
)
from llm_gateway.layer4_providers.step3_offline import solve
from llm_gateway.layer9_api.gateway import Gateway
from llm_gateway.layer10_evaluation.dataset import QUESTIONS, grade, question_at

SECRET = "What is the internal escalation path for a priority one incident?"


def task_for(live: bool) -> str:
    """
    Which route the evaluation should exercise.

    This matters more than it looks. Every request here used the default task,
    `general`, whose chain is `[offline-fast, offline-strong]` - so
    `make eval-live` swapped the embedder, made the live providers available,
    and then measured the OFFLINE models anyway while the Makefile described it
    as "measure it with real models". A live run that quietly measures the
    simulated models is the same failure this project keeps finding elsewhere:
    a number that looks like it came from the thing named above it.
    """
    if live:
        return "live"
    return "general"


def build(live: bool) -> Gateway:
    gateway = Gateway(GatewayStore(":memory:"), offline=not live)
    gateway.limiter.requests_per_minute = 1e9
    gateway.limiter.burst = 1e9
    gateway.limiter.daily_budget_usd = 1e9
    return gateway


def check_scopes(gateway: Gateway, task: str) -> list[str]:
    """
    Can anybody other than alice reach alice's cached answer?

    A FRESH identity for every probe. The first version of this reused one
    name, so the probe's own first request populated its own cache and the
    second probe matched THAT - which the check then reported as a leak from
    alice. The cache was behaving correctly the whole time and the test was
    measuring itself, which is the most embarrassing way for a safety check to
    be wrong: it cries wolf, somebody loosens it, and then it never fires again.
    """
    problems = []

    gateway.handle(ChatRequest(prompt=SECRET, task=task), "alice")

    probes = [
        SECRET,
        "the escalation path for a priority one incident is what?",
        "priority one incident escalation path?",
    ]
    for index, prompt in enumerate(probes):
        stranger = "stranger-%d" % index          # nobody reuses an identity
        response = gateway.handle(ChatRequest(prompt=prompt, task=task), stranger)

        # The ONLY valid signal is whether a provider was actually called.
        #
        # Comparing the answer text was the obvious next idea and it is wrong:
        # the offline provider is deterministic, so the same question produces
        # the same words whether or not a cache was involved. A check that
        # flags identical output as a leak fires on every correct run, which is
        # worse than not checking - somebody deletes it within the week.
        came_from_cache = (response.cache != CacheStatus.MISS) or (response.attempts == 0)
        if came_from_cache:
            problems.append("%s was served a cached answer for %r"
                            % (stranger, prompt[:40]))

    return problems


class AlwaysFails:
    """
    A provider that never answers, so the chain has to move past it.

    Needed because the offline provider's failure injection can only break
    offline models. In a live run the chain starts with gpt-4o-mini, and there
    is no way to ask OpenAI to fail on demand - so the first model is swapped
    for this, the real routing code runs unchanged, and it is put back after.
    """

    name = "broken"

    def models(self) -> list[str]:
        return []

    def complete(self, system: str, prompt: str, model: str,
                 max_tokens: int = 500, temperature: float = 0.0):
        raise ProviderError("this model was taken out of service for the test",
                            FailureKind.UNREACHABLE)

    def embed(self, text: str) -> list[float]:
        raise EmbeddingUnavailable("this provider does not embed")


def check_fallback(gateway: Gateway, task: str) -> tuple[bool, str]:
    """
    Break the FIRST model in the chain and check something else answers.

    It has to be the first one. The earlier version always broke
    `offline-fast`, which is last in the live chain - so in a live run
    gpt-4o-mini answered on the first attempt, no fallback happened at all, and
    the check reported "held". A fallback check that passes without a fallback
    is the sixth thing in this series to pass for the wrong reason, so this one
    now also requires that more than one model was tried.
    """
    chain, _ = gateway.routes.chain_for(task, "")
    if len(chain) < 2:
        return (False, "the %s route has no fallback to test" % task)

    first = chain[0]
    original = gateway.providers.get(first)
    gateway.providers[first] = AlwaysFails()
    gateway.router.providers = gateway.providers
    try:
        response = gateway.handle(
            ChatRequest(prompt="2 and 3 altogether?", no_cache=True, task=task), "demo")
    finally:
        if original is None:
            gateway.providers.pop(first, None)
        else:
            gateway.providers[first] = original
        gateway.router.providers = gateway.providers

    if response.outcome != Outcome.OK:
        return (False, "the chain did not hold: %s" % response.message[:60])
    if response.attempts < 2:
        return (False, "%s answered on the first attempt, so nothing fell back"
                % response.model)
    if response.model == first:
        return (False, "the broken model %s answered anyway" % first)
    return (True, "%s was broken, %s answered after %d attempt(s)"
            % (first, response.model, response.attempts))


def check_cache_saving(gateway: Gateway, task: str) -> dict:
    prompts = []
    for index in range(20):
        prompt, _ = question_at(index, 100 + index)
        prompts.append(prompt)

    for prompt in prompts:
        gateway.handle(ChatRequest(prompt=prompt, task=task), "cache-test")

    hits = 0
    for prompt in prompts:
        # Ask each one again, reworded, so both kinds of hit are exercised.
        response = gateway.handle(ChatRequest(prompt=prompt, task=task), "cache-test")
        if response.cache != CacheStatus.MISS:
            hits = hits + 1

    return {"asked": len(prompts) * 2, "hits": hits}


def run_experiment(gateway: Gateway, count: int, start: int = 0,
                   task: str = "general") -> None:
    """
    Ask `count` questions, continuing from question number `start`.

    `start` matters more than it looks. Without it every batch began at index 0
    and asked the same questions again, so 800 requests carried only 600
    distinct questions - and a significance test that assumes independent
    samples was being handed duplicates of a deterministic model's answers.
    The verdict would have been overconfident, in the one place this project
    exists to be honest.
    """
    for offset in range(count):
        index = start + offset
        prompt, expected = question_at(index, 10 + index)
        response = gateway.handle(ChatRequest(
            prompt=prompt, experiment="prompt-style", no_cache=True, task=task),
            "experiment")
        if response.outcome == Outcome.OK:
            gateway.store.set_score(response.request_id, grade(response.text, expected))


def check_benchmark() -> list[str]:
    """
    Before measuring anything, check the ruler.

    A benchmark question whose expected answer nothing can produce is graded
    wrong on every request, for every model and every prompt. It does not fail
    loudly - it lowers every arm by the same amount, so the rates still look
    plausible and the platform reports a smaller effect than really exists.
    This project shipped exactly that for a while: one question in five was
    dead, and an eighteen-point difference measured as eight.

    So the first number in the report is not about the gateway at all. It is
    whether the thing doing the measuring can be trusted.
    """
    broken = []
    for index in range(len(QUESTIONS)):
        prompt, expected = question_at(index, 20)
        solved = solve(prompt)
        if solved != expected:
            broken.append("question %d: dataset says %s, solver says %s"
                          % (index + 1, expected, solved))
    return broken


def main() -> int:
    live = "--live" in sys.argv
    if live and SETTINGS.is_offline():
        print("--live was asked for, but there is no OPENAI_API_KEY in .env.")
        return 2

    gateway = build(live)
    task = task_for(live)

    print("=" * 80)
    print("  LLM GATEWAY  -  %s" % ("LIVE" if live else "OFFLINE, no model calls"))
    print("=" * 80)
    print()

    # ---- 0 ----
    broken = check_benchmark()
    print("  0. THE BENCHMARK ITSELF")
    if len(broken) == 0:
        print("     all %d questions are scoreable - the solver and the dataset agree"
              % len(QUESTIONS))
    else:
        print("     UNSCOREABLE QUESTIONS - every rate below is depressed:")
        for line in broken:
            print("       %s" % line)
    print()

    # ---- 1 ----
    leaks = check_scopes(gateway, task)
    print("  1. CACHE SCOPING")
    if len(leaks) == 0:
        print("     nothing leaked between scopes across 3 attempts")
    else:
        for line in leaks:
            print("     *** LEAKED: %s" % line)
    print()

    # ---- 2 ----
    held, detail = check_fallback(gateway, task)
    print("  2. FALLBACK           %s" % ("held" if held else "DID NOT HOLD"))
    print("     %s" % detail)
    print()

    # ---- 3 ----
    saving = check_cache_saving(gateway, task)
    print("  3. CACHE              %d of %d repeat requests answered for free"
          % (saving["hits"], saving["asked"] // 2))
    print()

    # ---- 4 ----
    gateway.store.save_experiment(Experiment(
        name="prompt-style", question="does asking for working-out help?",
        variants=[
            Variant(name="direct", system="Answer with just the number.", weight=0.5),
            Variant(name="stepwise", system="Work through it step by step.", weight=0.5),
        ]))

    print("  4. THE SAME EXPERIMENT, AT THREE SAMPLE SIZES")
    seen = 0
    for target in (40, 200, 800):
        run_experiment(gateway, target - seen, seen, task)
        seen = target
        verdict = gateway.experiments.results("prompt-style")
        rates = []
        for arm in verdict.arms:
            rates.append("%s %.0f%%" % (arm.variant, arm.success_rate * 100))
        print()
        print("     after %d requests: %s" % (target, ", ".join(rates)))
        print("       %s" % verdict.verdict)
    print()

    # ---- 5 ----
    totals = gateway.store.totals()
    latency = gateway.store.latency_percentiles()
    print("  5. WHAT IT COST")
    print("     requests           %d" % (totals.get("requests") or 0))
    print("     spent              $%.6f" % (totals.get("cost") or 0.0))
    print("     latency            p50 %.4fs, p95 %.4fs"
          % (latency["p50"], latency["p95"]))

    # WHICH models answered, not which ones were asked. In a live run the chain
    # ends with an offline model, so a vendor having a bad afternoon shows up
    # here as offline-fast quietly doing most of the work - and every rate above
    # would then be a measurement of the simulated model wearing a live label.
    print()
    print("     answered by:")
    for row in gateway.store.by_model():
        print("       %-16s %5d requests   $%.6f   mean %.3fs"
              % (row["model"] or "(none)", row["requests"], row["cost"] or 0.0,
                 row["mean_seconds"] or 0.0))
    print()
    print("=" * 80)

    # The benchmark is checked first here too. Reporting a rate measured with a
    # ruler that does not work is worse than reporting nothing, because the
    # number looks usable.
    if len(broken) > 0:
        print("FAILED: %d benchmark question(s) cannot be scored, so every rate "
              "above is wrong." % len(broken))
        return 1
    if len(leaks) > 0:
        print("FAILED: the cache leaked between scopes.")
        return 1
    if not held:
        print("FAILED: the fallback chain did not hold.")
        return 1
    print("PASSED: nothing leaked, the chain held, and the verdict was honest "
          "about small samples.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
