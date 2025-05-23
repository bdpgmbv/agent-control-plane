"""Routes, fallback chains, and what happens after each kind of failure."""

import pytest

from llm_gateway.layer2_models.schemas import FailureKind
from llm_gateway.layer4_providers.step3_offline import OfflineProvider
from llm_gateway.layer4_providers.step4_openai import classify
from llm_gateway.layer5_routing.step1_routes import RouteTable
from llm_gateway.layer5_routing.step2_router import NoProviderAnswered, Router


@pytest.fixture
def offline():
    return OfflineProvider()


@pytest.fixture
def router(offline):
    return Router({"offline-fast": offline, "offline-strong": offline},
                  max_attempts_per_provider=2, retry_base_seconds=0.0)


def test_the_cheap_model_comes_first():
    # The expensive model is there to catch an outage, not to be the default.
    chain, name = RouteTable().chain_for("general")
    assert chain[0] == "offline-fast"
    assert name == "cheap-first"


def test_an_explicit_model_gets_no_fallback():
    # A caller who names a model has a reason, and quietly answering from a
    # different one would make their measurement meaningless.
    chain, name = RouteTable().chain_for("general", "offline-strong")
    assert chain == ["offline-strong"]
    assert name == "explicit"


def test_an_unknown_task_still_routes_somewhere():
    chain, _ = RouteTable().chain_for("nothing-like-this")
    assert len(chain) > 0


def test_a_working_provider_is_used_once(router):
    reply, attempts = router.call(["offline-fast"], "", "2 and 3", 100, 0.0)
    assert reply.model == "offline-fast"
    assert len(attempts) == 1
    assert attempts[0].ok


def test_a_transient_failure_is_retried(router, offline):
    offline.break_model("offline-fast", 1, FailureKind.UNREACHABLE)
    reply, attempts = router.call(["offline-fast"], "", "2 and 3", 100, 0.0)
    assert reply.model == "offline-fast"
    assert len(attempts) == 2
    assert not attempts[0].ok and attempts[1].ok


def test_it_falls_back_when_a_model_stays_down(router, offline):
    offline.break_model("offline-fast", 99, FailureKind.UNREACHABLE)
    reply, attempts = router.call(["offline-fast", "offline-strong"], "", "x", 100, 0.0)
    assert reply.model == "offline-strong"
    assert len(attempts) == 3          # two tries at the first, one at the second


def test_no_credit_is_not_retried_but_is_fallen_back_from(router, offline):
    # Retrying "no credit" costs two attempts to learn the same thing.
    offline.break_model("offline-fast", 99, FailureKind.NO_CREDIT)
    reply, attempts = router.call(["offline-fast", "offline-strong"], "", "x", 100, 0.0)
    assert reply.model == "offline-strong"
    assert len(attempts) == 2          # ONE try at the first, one at the second


def test_a_bad_request_from_a_provider_is_neither_retried_nor_passed_along(router, offline):
    # It will be just as malformed at the next provider, so trying anyway turns
    # one clear error into three slow ones.
    offline.break_model("offline-fast", 99, FailureKind.BAD_REQUEST)
    with pytest.raises(NoProviderAnswered) as caught:
        router.call(["offline-fast", "offline-strong"], "", "x", 100, 0.0)
    assert len(caught.value.attempts) == 1


def test_a_misconfigured_chain_entry_is_skipped_not_fatal(router):
    # A typo in a route is the gateway's mistake, not the caller's, and it
    # should not take the route down.
    reply, attempts = router.call(["no-such-model", "offline-fast"], "", "x", 100, 0.0)
    assert reply.model == "offline-fast"
    assert len(attempts) == 2
    assert not attempts[0].ok


def test_an_explicit_unknown_model_does_fail(router):
    # A chain of one, so the caller gets told rather than quietly rerouted.
    with pytest.raises(NoProviderAnswered) as caught:
        router.call(["no-such-model"], "", "x", 100, 0.0)
    assert len(caught.value.attempts) == 1


def test_every_attempt_is_recorded_including_the_failures(router, offline):
    offline.break_model("offline-fast", 99)
    _, attempts = router.call(["offline-fast", "offline-strong"], "", "x", 100, 0.0)
    failed = 0
    for attempt in attempts:
        if not attempt.ok:
            failed = failed + 1
    assert failed == 2


def test_when_everything_is_down_it_says_so(router, offline):
    offline.break_model("offline-fast", 99)
    offline.break_model("offline-strong", 99)
    with pytest.raises(NoProviderAnswered) as caught:
        router.call(["offline-fast", "offline-strong"], "", "x", 100, 0.0)
    assert len(caught.value.attempts) == 4


def test_sdk_errors_are_classified_by_what_to_do_about_them():
    cases = [
        ("Error code: 429 - insufficient_quota", FailureKind.NO_CREDIT),
        ("Error code: 429 - rate_limit_exceeded", FailureKind.RATE_LIMITED),
        ("Error code: 401 - invalid_api_key", FailureKind.BAD_KEY),
        ("Connection error.", FailureKind.UNREACHABLE),
        ("Request timed out.", FailureKind.TIMEOUT),
        ("Error code: 400 - invalid_request_error", FailureKind.BAD_REQUEST),
    ]
    for text, expected in cases:
        assert classify(Exception(text)) == expected, text


def test_insufficient_quota_is_not_mistaken_for_a_rate_limit():
    # Both arrive as a 429. One clears in a second; the other does not clear.
    kind = classify(Exception("Error code: 429 - insufficient_quota"))
    assert not kind.worth_retrying()
    assert kind.worth_falling_back()
