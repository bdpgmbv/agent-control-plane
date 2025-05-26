"""The gateway end to end, and the HTTP surface."""

import pytest
from fastapi.testclient import TestClient

from llm_gateway.layer2_models.schemas import CacheStatus, ChatRequest, Outcome
from llm_gateway.layer9_api.app import GATEWAY, app

# ---------------------------------------------------------------- the gateway

def test_a_request_is_answered_and_traced(gateway):
    response = gateway.handle(ChatRequest(prompt="2 and 3 altogether?"), "demo")
    assert response.outcome == Outcome.OK
    assert response.text != ""

    trace = gateway.store.get_trace(response.request_id)
    assert trace is not None
    assert trace.api_key_owner == "demo"
    assert trace.attempts_made() == 1


def test_the_second_identical_request_costs_nothing(gateway):
    first = gateway.handle(ChatRequest(prompt="2 and 3 altogether?"), "demo")
    second = gateway.handle(ChatRequest(prompt="2 and 3 altogether?"), "demo")

    assert first.cache == CacheStatus.MISS
    assert second.cache == CacheStatus.EXACT
    assert second.cost_usd == 0.0
    assert second.attempts == 0
    assert second.text == first.text


def test_skipping_the_cache_really_skips_it(gateway):
    gateway.handle(ChatRequest(prompt="2 and 3 altogether?"), "demo")
    again = gateway.handle(ChatRequest(prompt="2 and 3 altogether?", no_cache=True), "demo")
    assert again.cache == CacheStatus.MISS
    assert again.attempts == 1


def test_one_key_cannot_read_anothers_cache(gateway):
    gateway.handle(ChatRequest(prompt="something private"), "alice")
    theirs = gateway.handle(ChatRequest(prompt="something private"), "bob")
    assert theirs.cache == CacheStatus.MISS


def test_it_falls_back_and_the_trace_shows_it(gateway):
    gateway.offline_provider.break_model("offline-fast", 99)
    response = gateway.handle(ChatRequest(prompt="2 and 3", no_cache=True), "demo")

    assert response.outcome == Outcome.OK
    assert response.model == "offline-strong"

    trace = gateway.store.get_trace(response.request_id)
    assert trace.fell_back()
    failed = 0
    for attempt in trace.attempt_list:
        if not attempt.ok:
            failed = failed + 1
    assert failed >= 1


def test_a_total_outage_is_traced_too(gateway):
    gateway.offline_provider.break_model("offline-fast", 99)
    gateway.offline_provider.break_model("offline-strong", 99)
    response = gateway.handle(ChatRequest(prompt="anything", no_cache=True), "demo")

    assert response.outcome == Outcome.FAILED
    trace = gateway.store.get_trace(response.request_id)
    assert trace is not None
    assert trace.outcome == Outcome.FAILED
    assert trace.attempts_made() == 4


def test_a_refused_request_is_traced_and_costs_nothing(gateway):
    gateway.limiter.requests_per_minute = 1
    gateway.limiter.burst = 1
    gateway.limiter.buckets = {}

    gateway.handle(ChatRequest(prompt="one", no_cache=True), "demo")
    refused = gateway.handle(ChatRequest(prompt="two", no_cache=True), "demo")

    assert refused.outcome == Outcome.REFUSED
    trace = gateway.store.get_trace(refused.request_id)
    assert trace.outcome == Outcome.REFUSED
    assert trace.cost_usd == 0.0
    assert trace.attempts_made() == 0


def test_an_explicit_model_is_honoured(gateway):
    response = gateway.handle(
        ChatRequest(prompt="2 and 3", model="offline-strong", no_cache=True), "demo")
    assert response.model == "offline-strong"


def test_the_reasoning_route_starts_with_the_strong_model(gateway):
    response = gateway.handle(
        ChatRequest(prompt="2 and 3", task="reasoning", no_cache=True), "demo")
    assert response.model == "offline-strong"


# ---------------------------------------------------------------- the API

@pytest.fixture
def client():
    GATEWAY.store.clear()
    GATEWAY.limiter.buckets = {}
    GATEWAY.limiter.requests_per_minute = 1e9
    GATEWAY.limiter.burst = 1e9
    GATEWAY.limiter.daily_budget_usd = 1e9
    with TestClient(app) as made:
        yield made
    GATEWAY.store.clear()


def send(client, prompt="2 and 3 altogether?", key="demo-key", **extra):
    body = {"prompt": prompt}
    for name, value in extra.items():
        body[name] = value
    return client.post("/v1/chat", json=body, headers={"X-API-Key": key})


def test_health_lists_what_is_available(client):
    body = client.get("/api/health").json()
    assert body["ok"]
    assert body["mode"] == "offline"
    assert "offline-fast" in body["models"]


def test_an_unknown_key_is_refused(client):
    response = send(client, key="not-a-key")
    assert response.status_code == 401


def test_an_empty_prompt_is_refused(client):
    assert send(client, prompt="   ").status_code == 400


def test_chat_works_over_http(client):
    body = send(client).json()
    assert body["outcome"] == "ok"
    assert body["text"] != ""
    assert body["request_id"].startswith("req_")


def test_a_rate_limited_caller_gets_429(client):
    GATEWAY.limiter.requests_per_minute = 1
    GATEWAY.limiter.burst = 1
    GATEWAY.limiter.buckets = {}

    send(client, no_cache=True)
    second = send(client, prompt="different", no_cache=True)
    assert second.status_code == 429
    assert "rate limit" in second.json()["message"]


def test_a_failed_request_gets_502(client):
    GATEWAY.offline_provider.break_model("offline-fast", 99)
    GATEWAY.offline_provider.break_model("offline-strong", 99)
    response = send(client, no_cache=True)
    assert response.status_code == 502
    GATEWAY.offline_provider.mend()


def test_traces_can_be_listed_and_fetched(client):
    request_id = send(client).json()["request_id"]

    rows = client.get("/v1/traces").json()["traces"]
    assert len(rows) == 1

    one = client.get("/v1/traces/%s" % request_id).json()
    assert one["request_id"] == request_id
    assert client.get("/v1/traces/req_nope").status_code == 404


def test_a_grade_can_arrive_afterwards(client):
    # Serving and grading are separate because the useful metrics usually are.
    request_id = send(client).json()["request_id"]
    response = client.patch("/v1/traces/%s/score" % request_id, json={"score": 1.0})
    assert response.status_code == 200
    assert client.get("/v1/traces/%s" % request_id).json()["score"] == 1.0


def test_the_dashboard_adds_up(client):
    send(client, prompt="one", no_cache=True)
    send(client, prompt="two", no_cache=True)
    body = client.get("/api/dashboard").json()
    assert body["totals"]["requests"] == 2
    assert body["latency"]["count"] == 2
    assert len(body["by_model"]) >= 1


def test_an_experiment_needs_exactly_two_variants(client):
    response = client.post("/api/experiments", json={
        "name": "three", "variants": [{"name": "a"}, {"name": "b"}, {"name": "c"}]})
    assert response.status_code == 400
    assert "exactly two" in response.json()["detail"]


def test_an_experiment_can_be_created_and_run(client):
    client.post("/api/experiments", json={
        "name": "prompt-style", "question": "does it help?",
        "variants": [
            {"name": "direct", "system": "Answer with just the number.", "weight": 0.5},
            {"name": "stepwise", "system": "Work through it step by step.", "weight": 0.5},
        ]})

    body = client.post("/api/experiments/prompt-style/simulate",
                       json={"requests": 80},
                       headers={"X-API-Key": "demo-key"}).json()
    assert body["graded"] == 80
    assert body["refused"] == 0

    results = body["results"]
    assert len(results["arms"]) == 2
    total = 0
    for arm in results["arms"]:
        total = total + arm["scored"]
    assert total == 80


def test_the_verdict_is_honest_about_small_samples(client):
    client.post("/api/experiments", json={
        "name": "prompt-style",
        "variants": [
            {"name": "direct", "system": "Answer with just the number.", "weight": 0.5},
            {"name": "stepwise", "system": "Work through it step by step.", "weight": 0.5},
        ]})
    client.post("/api/experiments/prompt-style/simulate", json={"requests": 20},
                headers={"X-API-Key": "demo-key"})

    results = client.get("/api/experiments/prompt-style/results").json()
    assert results["winner"] == ""
    assert "Not enough" in results["verdict"]


def test_reset_empties_everything(client):
    send(client)
    assert len(client.get("/v1/traces").json()["traces"]) == 1
    client.post("/api/reset")
    assert client.get("/v1/traces").json()["traces"] == []


def test_the_interface_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "LLM Gateway" in response.text
    assert client.get("/ui/app.js").status_code == 200
    assert client.get("/ui/style.css").status_code == 200
