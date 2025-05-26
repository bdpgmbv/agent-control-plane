"""
LAYER 9 - THE GATEWAY
=====================
Every layer, in the order a request meets them:

    who is calling      -> layer 7, limits
    which variant       -> layer 8, experiments
    which models        -> layer 5, routing
    have we answered    -> layer 6, cache
    ask somebody        -> layer 4, providers, through the router
    write it down       -> layer 3, always

Reading `handle` top to bottom is the fastest way to understand this project.
The order is not arbitrary: limits come first because a refused request should
cost nothing, and the cache comes before the providers because a hit should cost
nothing either. Checking the cache before the rate limit would let a caller
hammer the gateway for free, which sounds harmless until the hammering is a
retry loop that never stops.

A TRACE IS WRITTEN WHATEVER HAPPENS
-----------------------------------
Refused, failed, cached, or fine - every request produces exactly one trace. A
gateway that records only successes cannot tell you why yesterday was expensive,
and it cannot tell you that a third of your traffic is bouncing off a rate limit
nobody has looked at since it was set.
"""

import hashlib
import time

from llm_gateway.layer1_config.settings import SETTINGS
from llm_gateway.layer2_models.schemas import (
    CacheStatus,
    ChatRequest,
    ChatResponse,
    Outcome,
    Trace,
)
from llm_gateway.layer3_storage.store import GatewayStore, new_request_id
from llm_gateway.layer4_providers.step1_prices import price_for
from llm_gateway.layer4_providers.step3_offline import OfflineProvider, hashing_embedding
from llm_gateway.layer5_routing.step1_routes import RouteTable
from llm_gateway.layer5_routing.step2_router import NoProviderAnswered, Router
from llm_gateway.layer6_cache.step1_cache import ResponseCache, build_scope_key
from llm_gateway.layer7_limits.step1_limits import Limiter
from llm_gateway.layer8_experiments.step2_experiments import ExperimentRunner


def build_providers(offline: bool) -> tuple[dict, object]:
    """
    Which model is served by whom.

    The offline provider is ALWAYS present, even when a key is configured, so
    that a route can end with an offline model as its last resort. A gateway
    whose fallback chain runs out is a gateway that returns 503 when the vendor
    has a bad afternoon.
    """
    offline_provider = OfflineProvider()
    providers: dict = {}
    for model in offline_provider.models():
        providers[model] = offline_provider

    if not offline:
        from llm_gateway.layer4_providers.step4_openai import OpenAIProvider

        live = OpenAIProvider(SETTINGS.openai_api_key, SETTINGS.openai_timeout_seconds)
        for model in live.models():
            providers[model] = live

    return (providers, offline_provider)


class Gateway:
    def __init__(self, store: GatewayStore | None = None,
                 offline: bool | None = None) -> None:
        self.store = store or GatewayStore(SETTINGS.sqlite_path)

        if offline is None:
            offline = SETTINGS.is_offline()
        self.offline = offline

        self.providers, self.offline_provider = build_providers(offline)
        self.routes = RouteTable()
        self.router = Router(self.providers,
                             SETTINGS.max_attempts_per_provider,
                             SETTINGS.retry_base_seconds)

        embedder = self.build_embedder()
        self.cache = ResponseCache(
            self.store, embedder, SETTINGS.cache_enabled,
            SETTINGS.semantic_cache_enabled, SETTINGS.semantic_threshold(),
            SETTINGS.cache_ttl_seconds)

        self.limiter = Limiter(self.store, SETTINGS.requests_per_minute,
                               SETTINGS.burst, SETTINGS.daily_budget_usd)
        self.experiments = ExperimentRunner(self.store, SETTINGS.confidence_level,
                                            SETTINGS.minimum_samples_per_arm)

    def build_embedder(self):
        """
        Embeddings for the semantic cache.

        The offline hashing embedder unless there is a real key, and the two are
        NOT interchangeable: the hashing one finds rewordings, the real one
        finds meanings. Their thresholds are measured separately, because a
        number tuned against one of them says nothing about the other.
        """
        if self.offline:
            return hashing_embedding

        live = self.providers.get("gpt-4o-mini")
        if live is None:
            return hashing_embedding

        def embed(text: str) -> list[float]:
            return live.embed(text)

        return embed

    # ---------------------------------------------------------------- the loop

    def handle(self, request: ChatRequest, api_key_owner: str) -> ChatResponse:
        started = time.time()
        request_id = new_request_id()

        prompt_hash = hashlib.sha256(
            ("%s|%s" % (request.system, request.prompt)).encode("utf-8")).hexdigest()[:16]

        trace = Trace(
            request_id=request_id, api_key_owner=api_key_owner, task=request.task,
            prompt_hash=prompt_hash, prompt_preview=request.prompt[:160])

        # ---- 1. is this caller allowed to spend anything at all? ----
        decision = self.limiter.check(api_key_owner)
        if not decision.allowed:
            trace.outcome = Outcome.REFUSED
            trace.message = decision.reason
            trace.seconds = round(time.time() - started, 4)
            self.store.record(trace)
            return ChatResponse(request_id=request_id, outcome=Outcome.REFUSED,
                                message=decision.reason,
                                seconds=trace.seconds)

        # ---- 2. which variant, if this is an experiment? ----
        system = request.system
        chosen_model = request.model
        if request.experiment != "":
            variant = self.experiments.pick(request.experiment, request_id)
            if variant is not None:
                trace.experiment = request.experiment
                trace.variant = variant.name
                if variant.system != "":
                    system = variant.system
                if variant.model != "":
                    chosen_model = variant.model

        # ---- 3. which models to try ----
        chain, route_name = self.routes.chain_for(request.task, chosen_model)
        trace.route = route_name

        # ---- 4. have we answered this already? ----
        scope = build_scope_key(api_key_owner, request.scope)
        first_model = chain[0] if len(chain) > 0 else ""

        if request.no_cache:
            lookup_status = CacheStatus.MISS
            lookup = None
        else:
            lookup = self.cache.look_up(scope, system, request.prompt, first_model,
                                        request.temperature, request.max_tokens)
            lookup_status = lookup.status

        if lookup is not None and lookup_status in (CacheStatus.EXACT, CacheStatus.SEMANTIC):
            trace.outcome = Outcome.OK
            trace.cache = lookup_status
            trace.provider = lookup.provider
            trace.model = lookup.model
            trace.input_tokens = 0          # a cache hit costs no tokens
            trace.output_tokens = 0
            trace.cost_usd = 0.0
            trace.seconds = round(time.time() - started, 4)
            trace.message = ("answered from the cache (%s, similarity %.3f)"
                             % (lookup_status.value, lookup.similarity))
            self.store.record(trace)

            return ChatResponse(
                request_id=request_id, text=lookup.text, outcome=Outcome.OK,
                message=trace.message, provider=lookup.provider, model=lookup.model,
                cache=lookup_status, experiment=trace.experiment,
                variant=trace.variant, seconds=trace.seconds, attempts=0)

        # ---- 5. ask somebody ----
        try:
            reply, attempts = self.router.call(chain, system, request.prompt,
                                               request.max_tokens, request.temperature)
        except NoProviderAnswered as error:
            trace.outcome = Outcome.FAILED
            trace.message = str(error)
            trace.attempt_list = error.attempts
            trace.seconds = round(time.time() - started, 4)
            for attempt in error.attempts:
                trace.cost_usd = trace.cost_usd + attempt.cost_usd
            self.store.record(trace)
            return ChatResponse(request_id=request_id, outcome=Outcome.FAILED,
                                message=str(error), seconds=trace.seconds,
                                attempts=len(error.attempts),
                                experiment=trace.experiment, variant=trace.variant)

        price = price_for(reply.model)
        cost = price.cost(reply.input_tokens, reply.output_tokens)

        # Failed attempts can cost money too - a provider that timed out after
        # generating tokens still bills for them. Summing every attempt rather
        # than only the successful one is the difference between a cost figure
        # and a cost estimate.
        for attempt in attempts:
            if not attempt.ok:
                cost = cost + attempt.cost_usd

        provider_name = ""
        for attempt in attempts:
            if attempt.ok:
                provider_name = attempt.provider

        trace.outcome = Outcome.OK
        trace.cache = lookup_status if lookup is not None else CacheStatus.MISS
        trace.provider = provider_name
        trace.model = reply.model
        trace.attempt_list = attempts
        trace.input_tokens = reply.input_tokens
        trace.output_tokens = reply.output_tokens
        trace.cost_usd = round(cost, 8)
        trace.seconds = round(time.time() - started, 4)
        self.store.record(trace)

        # ---- 6. remember it ----
        if not request.no_cache:
            self.cache.store_answer(scope, system, request.prompt, reply.model,
                                    request.temperature, request.max_tokens,
                                    provider_name, reply.text,
                                    reply.input_tokens, reply.output_tokens)

        return ChatResponse(
            request_id=request_id, text=reply.text, outcome=Outcome.OK,
            provider=provider_name, model=reply.model, cache=trace.cache,
            experiment=trace.experiment, variant=trace.variant,
            input_tokens=reply.input_tokens, output_tokens=reply.output_tokens,
            cost_usd=trace.cost_usd, seconds=trace.seconds, attempts=len(attempts))
