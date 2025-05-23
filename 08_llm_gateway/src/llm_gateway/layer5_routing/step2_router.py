"""
LAYER 5, STEP 2 - TRYING THE CHAIN
==================================
Work down the list of models until one answers, and record every attempt
including the ones that failed.

The loop has two nested decisions, and keeping them separate is the point:

    for each model in the chain:
        for each attempt at that model:
            if it fails and the failure is worth retrying -> try again
            if it fails and it is not -> stop attempting this model
        if the failure is worth falling back -> move to the next model
        if it is not -> stop entirely

Retrying and falling back answer different questions. Retrying asks "was that
just bad luck?"; falling back asks "is this provider usable at all?". A rate
limit is worth retrying AND worth falling back from. A malformed request is
worth neither - it will be just as malformed at the next provider, and trying
anyway turns one clear error into three slow ones.

Every attempt lands in the trace, successful or not. A gateway that records only
the call that worked cannot tell you that you are paying for the expensive model
because the cheap one has been timing out all afternoon.
"""

import time

from llm_gateway.layer2_models.schemas import Attempt, FailureKind
from llm_gateway.layer4_providers.step1_prices import price_for
from llm_gateway.layer4_providers.step2_base import ProviderError, ProviderReply


class NoProviderAnswered(Exception):
    """Every model in the chain was tried and none of them worked."""

    def __init__(self, message: str, attempts: list[Attempt],
                 last_kind: FailureKind = FailureKind.UNKNOWN) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_kind = last_kind


class Router:
    def __init__(self, providers: dict, max_attempts_per_provider: int = 2,
                 retry_base_seconds: float = 0.5) -> None:
        # model name -> provider that serves it
        self.providers = providers
        self.max_attempts_per_provider = max_attempts_per_provider
        self.retry_base_seconds = retry_base_seconds

    def provider_for(self, model: str):
        return self.providers.get(model)

    def call(self, chain: list[str], system: str, prompt: str,
             max_tokens: int, temperature: float) -> tuple[ProviderReply, list[Attempt]]:
        attempts: list[Attempt] = []
        last_kind = FailureKind.UNKNOWN
        last_message = ""

        for model in chain:
            provider = self.provider_for(model)
            if provider is None:
                # A chain entry naming a model nobody serves is a mistake in the
                # GATEWAY's configuration, not in the caller's request, so the
                # chain carries on to the next entry. That is the opposite of
                # what happens to a bad request below, and the distinction is
                # worth being deliberate about: a typo in a route should not
                # take the route down, while a malformed prompt should not be
                # sent to three providers in turn to be rejected three times.
                #
                # An explicit model the gateway does not serve becomes a chain
                # of one, so the caller still gets a clear failure.
                attempts.append(Attempt(
                    provider="none", model=model, ok=False,
                    failure_kind=FailureKind.BAD_REQUEST.value,
                    error="no provider serves a model called %r" % model))
                last_kind = FailureKind.BAD_REQUEST
                last_message = "no provider serves %r" % model
                continue

            for attempt_number in range(1, self.max_attempts_per_provider + 1):
                started = time.time()
                try:
                    reply = provider.complete(system, prompt, model,
                                              max_tokens, temperature)
                except ProviderError as error:
                    elapsed = round(time.time() - started, 4)
                    attempts.append(Attempt(
                        provider=provider.name, model=model, ok=False,
                        failure_kind=error.kind.value, error=str(error)[:300],
                        seconds=elapsed))
                    last_kind = error.kind
                    last_message = str(error)

                    if error.kind.worth_retrying() and \
                            attempt_number < self.max_attempts_per_provider:
                        time.sleep(self.retry_base_seconds * attempt_number)
                        continue
                    break

                elapsed = round(time.time() - started, 4)
                price = price_for(model)
                attempts.append(Attempt(
                    provider=provider.name, model=model, ok=True,
                    seconds=elapsed, input_tokens=reply.input_tokens,
                    output_tokens=reply.output_tokens,
                    cost_usd=price.cost(reply.input_tokens, reply.output_tokens)))
                return (reply, attempts)

            if not last_kind.worth_falling_back():
                # A bad request will be just as bad at the next provider.
                break

        raise NoProviderAnswered(
            "every model in the chain failed; the last said: %s" % last_message[:200],
            attempts, last_kind)
