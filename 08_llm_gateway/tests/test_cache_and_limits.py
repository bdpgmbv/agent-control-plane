"""Caching, scoping, and the two kinds of limit."""

import time

from llm_gateway.layer2_models.schemas import CacheStatus, Outcome, Trace
from llm_gateway.layer3_storage.store import new_request_id
from llm_gateway.layer4_providers.step2_base import EmbeddingUnavailable
from llm_gateway.layer4_providers.step3_offline import hashing_embedding
from llm_gateway.layer6_cache.step1_cache import (
    ResponseCache,
    build_cache_key,
    build_scope_key,
)
from llm_gateway.layer7_limits.step1_limits import Limiter, TokenBucket

QUESTION = "What is the refund window for online orders?"


def make_cache(store, **kwargs):
    options = {"enabled": True, "semantic_enabled": True,
               "threshold": 0.93, "ttl_seconds": 3600.0}
    options.update(kwargs)
    return ResponseCache(store, hashing_embedding, **options)


def test_an_exact_repeat_is_a_hit(store):
    cache = make_cache(store)
    scope = build_scope_key("alice", "")
    cache.store_answer(scope, "", QUESTION, "offline-fast", 0.0, 500,
                       "offline", "Thirty days.", 10, 5)

    found = cache.look_up(scope, "", QUESTION, "offline-fast", 0.0, 500)
    assert found.status == CacheStatus.EXACT
    assert found.text == "Thirty days."


def test_a_rewording_is_a_semantic_hit(store):
    cache = make_cache(store)
    scope = build_scope_key("alice", "")
    cache.store_answer(scope, "", QUESTION, "offline-fast", 0.0, 500,
                       "offline", "Thirty days.", 10, 5)

    found = cache.look_up(scope, "", "For online orders, what is the refund window?",
                          "offline-fast", 0.0, 500)
    assert found.status == CacheStatus.SEMANTIC
    assert found.similarity >= 0.93


def test_a_different_question_is_not_a_hit(store):
    cache = make_cache(store)
    scope = build_scope_key("alice", "")
    cache.store_answer(scope, "", QUESTION, "offline-fast", 0.0, 500,
                       "offline", "Thirty days.", 10, 5)

    found = cache.look_up(scope, "", "How do I change my delivery address?",
                          "offline-fast", 0.0, 500)
    assert found.status == CacheStatus.MISS


def test_one_scope_never_sees_another_scopes_answer(store):
    """
    The bug project 01 shipped. The similarity search found the nearest entry
    and the permission check came afterwards.
    """
    cache = make_cache(store)
    alice = build_scope_key("alice", "")
    bob = build_scope_key("bob", "")

    cache.store_answer(alice, "", QUESTION, "offline-fast", 0.0, 500,
                       "offline", "Thirty days, and alice's account is overdue.", 10, 5)

    assert cache.look_up(bob, "", QUESTION, "offline-fast", 0.0, 500).status \
        == CacheStatus.MISS
    # And not by a rewording either - there is no path that finds it at all.
    assert cache.look_up(bob, "", "the refund window for online orders is what?",
                         "offline-fast", 0.0, 500).status == CacheStatus.MISS


def test_a_shared_scope_is_shared_on_purpose(store):
    cache = make_cache(store)
    shared = build_scope_key("alice", "public-docs")
    also_shared = build_scope_key("bob", "public-docs")

    cache.store_answer(shared, "", QUESTION, "offline-fast", 0.0, 500,
                       "offline", "Thirty days.", 10, 5)
    assert cache.look_up(also_shared, "", QUESTION, "offline-fast", 0.0, 500).status \
        == CacheStatus.EXACT


def test_a_different_model_is_a_different_answer(store):
    cache = make_cache(store)
    scope = build_scope_key("alice", "")
    cache.store_answer(scope, "", QUESTION, "offline-fast", 0.0, 500,
                       "offline", "Thirty days.", 10, 5)
    assert cache.look_up(scope, "", QUESTION, "offline-strong", 0.0, 500).status \
        == CacheStatus.MISS


def test_randomness_makes_a_request_uncacheable(store):
    # The caller asked for variety; handing back a stored answer refuses to
    # give it.
    cache = make_cache(store)
    scope = build_scope_key("alice", "")
    assert cache.look_up(scope, "", QUESTION, "offline-fast", 0.7, 500).status \
        == CacheStatus.NOT_CACHEABLE


def test_max_tokens_is_part_of_the_key():
    # Otherwise a 100-token answer gets served to somebody who asked for 2000.
    first = build_cache_key("s", "", "q", "m", 0.0, 100)
    second = build_cache_key("s", "", "q", "m", 0.0, 2000)
    assert first != second


def test_an_expired_entry_is_not_served(store):
    cache = make_cache(store, ttl_seconds=0.01)
    scope = build_scope_key("alice", "")
    cache.store_answer(scope, "", QUESTION, "offline-fast", 0.0, 500,
                       "offline", "Thirty days.", 10, 5)
    time.sleep(0.05)
    assert cache.look_up(scope, "", QUESTION, "offline-fast", 0.0, 500).status \
        == CacheStatus.MISS


def test_a_disabled_cache_says_so_rather_than_missing(store):
    cache = make_cache(store, enabled=False)
    assert cache.look_up("s", "", QUESTION, "m", 0.0, 500).status == CacheStatus.DISABLED


# ---------------------------------------------------------------- limits

def test_a_bucket_allows_a_burst_then_slows():
    bucket = TokenBucket(per_minute=60, burst=3)
    assert bucket.take()[0]
    assert bucket.take()[0]
    assert bucket.take()[0]
    allowed, wait = bucket.take()
    assert not allowed
    assert wait > 0


def test_a_bucket_refills():
    bucket = TokenBucket(per_minute=6000, burst=1)   # 100 a second
    assert bucket.take()[0]
    assert not bucket.take()[0]
    time.sleep(0.05)
    assert bucket.take()[0]


def test_the_rate_limit_refuses_and_says_how_long(store):
    limiter = Limiter(store, requests_per_minute=3, burst=3, daily_budget_usd=100)
    for _ in range(3):
        assert limiter.check("demo").allowed
    decision = limiter.check("demo")
    assert not decision.allowed
    assert decision.retry_after_seconds > 0
    assert "rate limit" in decision.reason


def test_one_key_running_out_does_not_affect_another(store):
    limiter = Limiter(store, requests_per_minute=2, burst=2, daily_budget_usd=100)
    limiter.check("noisy")
    limiter.check("noisy")
    assert not limiter.check("noisy").allowed
    assert limiter.check("quiet").allowed


def test_the_budget_refuses_and_explains_differently(store):
    # Out of money and going too fast need different messages: one is fixable
    # by waiting and the other is not.
    store.record(Trace(request_id=new_request_id(), api_key_owner="spender",
                       outcome=Outcome.OK, cost_usd=5.50))
    decision = Limiter(store, 1000, 1000, 5.0).check("spender")
    assert not decision.allowed
    assert "budget" in decision.reason
    assert decision.retry_after_seconds == 0


def test_the_budget_is_checked_before_the_rate_limit(store):
    # A caller who is out of money should be told that, not told to slow down -
    # the wrong message sends them into a retry loop.
    store.record(Trace(request_id=new_request_id(), api_key_owner="spender",
                       outcome=Outcome.OK, cost_usd=99.0))
    limiter = Limiter(store, requests_per_minute=1, burst=1, daily_budget_usd=5.0)
    limiter.check("spender")
    decision = limiter.check("spender")
    assert "budget" in decision.reason


def test_spending_is_summed_from_traces_not_counted(store):
    for _ in range(4):
        store.record(Trace(request_id=new_request_id(), api_key_owner="demo",
                           outcome=Outcome.OK, cost_usd=0.25))
    assert abs(store.spend_today("demo") - 1.0) < 1e-9


# --------------------------------------------------------------------------
# When the embedder cannot answer.
#
# The dangerous version of this is not a crash. It is substituting a vector
# from a different embedder and comparing it against the stored ones anyway:
# cosine similarity between two spaces is a meaningless number that is still
# checked against the threshold, and it can clear it.
# --------------------------------------------------------------------------

def broken_embedder(text: str) -> list[float]:
    raise EmbeddingUnavailable("no credits remaining")


# The same question, reworded. Under the hashing embedder these score 1.0000,
# which is what makes the tests below able to fail.
#
# The first version of this used "How long have I got to return something?" -
# a rewording of a DIFFERENT question, scoring 0.0000 against QUESTION. Every
# assertion below passed, including with the bug deliberately put back, because
# the lookup missed for the wrong reason. A negative test needs a positive
# control or it is decoration, which is the fifth time that has come up in this
# series.
REWORDED = "For online orders, what is the refund window?"


def test_the_rewording_really_is_a_hit_when_the_embedder_works(store):
    """
    The control. If this fails, every test below it is meaningless.

    It asserts the fixture itself is capable of producing a semantic hit at
    this threshold, so that a MISS in the next tests can only be caused by the
    embedder being unavailable.
    """
    cache = make_cache(store, threshold=0.5)
    scope = build_scope_key("alice", "")
    cache.store_answer(scope, "", QUESTION, "offline-fast", 0.0, 500,
                       "offline", "Thirty days.", 10, 5)

    found = cache.look_up(scope, "", REWORDED, "offline-fast", 0.0, 500)
    assert found.status == CacheStatus.SEMANTIC
    assert found.text == "Thirty days."


def test_a_failing_embedder_gives_a_miss_not_an_error(store):
    """
    A semantic lookup with no embedder available is a miss, not an outage - and
    not a hit found by substituting a vector from somewhere else.
    """
    good = make_cache(store)
    scope = build_scope_key("alice", "")
    good.store_answer(scope, "", QUESTION, "offline-fast", 0.0, 500,
                      "offline", "Thirty days.", 10, 5)

    broken = ResponseCache(store, broken_embedder, threshold=0.5)
    found = broken.look_up(scope, "", REWORDED, "offline-fast", 0.0, 500)
    assert found.status == CacheStatus.MISS
    assert found.text == ""


def test_an_exact_hit_still_works_when_the_embedder_is_down(store):
    """
    Losing embeddings must not lose the exact-match cache too.

    The embedder is only needed for rewordings. An identical repeat is a
    dictionary lookup, and it should keep saving money while embeddings are
    unavailable.
    """
    good = make_cache(store)
    scope = build_scope_key("alice", "")
    good.store_answer(scope, "", QUESTION, "offline-fast", 0.0, 500,
                      "offline", "Thirty days.", 10, 5)

    broken = ResponseCache(store, broken_embedder)
    found = broken.look_up(scope, "", QUESTION, "offline-fast", 0.0, 500)
    assert found.status == CacheStatus.EXACT
    assert found.text == "Thirty days."


def test_an_answer_is_still_stored_when_it_cannot_be_embedded(store):
    """
    Store the entry with no embedding rather than not storing it.

    It cannot be found by rewording, which is the honest consequence, but an
    exact repeat still hits. What must never be written is a vector from
    another embedder sitting next to the real ones.
    """
    broken = ResponseCache(store, broken_embedder)
    scope = build_scope_key("alice", "")
    broken.store_answer(scope, "", QUESTION, "offline-fast", 0.0, 500,
                        "offline", "Thirty days.", 10, 5)

    assert broken.look_up(scope, "", QUESTION, "offline-fast",
                          0.0, 500).status == CacheStatus.EXACT

    # And a WORKING embedder cannot reach it by rewording either, because there
    # is no vector to compare against - not a wrong one. The control above
    # proves this same lookup would hit if the entry had been embedded.
    working = make_cache(store, threshold=0.5)
    found = working.look_up(scope, "", REWORDED, "offline-fast", 0.0, 500)
    assert found.status == CacheStatus.MISS


def test_an_unembedded_entry_never_matches_anything(store):
    """
    An entry stored with an empty embedding must score zero, not one.

    `cosine_similarity` returns 0.0 on a length mismatch. If it ever returned
    something else - or if an empty vector were treated as all-zeros of the
    right length - every unembedded entry would become a candidate for every
    question, and the first one stored would answer all of them.
    """
    from llm_gateway.layer4_providers.step3_offline import cosine_similarity

    real = hashing_embedding(QUESTION)
    assert cosine_similarity(real, []) == 0.0
    assert cosine_similarity([], real) == 0.0
