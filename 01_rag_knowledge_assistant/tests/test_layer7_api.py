"""TESTS FOR LAYER 7 - the HTTP API, authentication and caching behaviour."""

from tests.conftest import ADMIN_HEADERS, USER_HEADERS

# ---------------- authentication ----------------

def test_a_request_with_no_key_is_rejected(api_client):
    response = api_client.post("/api/ask", json={"question": "refund window"})
    assert response.status_code == 401


def test_an_unknown_key_is_rejected(api_client):
    response = api_client.post(
        "/api/ask", json={"question": "refund window"}, headers={"X-API-Key": "not-a-real-key"}
    )
    assert response.status_code == 401


def test_a_user_key_cannot_add_documents(api_client):
    response = api_client.post(
        "/api/documents/text",
        json={"title": "Sneaky", "text": "Some text about refunds and shipping."},
        headers=USER_HEADERS,
    )
    assert response.status_code == 403


def test_an_admin_key_can_add_documents(api_client):
    response = api_client.post(
        "/api/documents/text",
        json={
            "title": "Warranty Policy",
            "text": "# Warranty\nAll hardware carries a 24 month warranty from the purchase date.",
            "access_tag": "public",
        },
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["chunks_created"] > 0


def test_a_caller_cannot_publish_into_a_tag_they_cannot_read(api_client):
    """
    The user key may only read "public". Writing a "secret" document would let it
    smuggle content into an admin's answers.
    """
    response = api_client.post(
        "/api/documents/text",
        json={"title": "X", "text": "Some secret text here.", "access_tag": "secret"},
        headers=USER_HEADERS,
    )
    assert response.status_code == 403


# ---------------- access control on answers ----------------

def test_the_user_key_is_refused_the_secret_answer(api_client):
    response = api_client.post(
        "/api/ask",
        json={"question": "What does a level three engineer earn?", "use_cache": False},
        headers=USER_HEADERS,
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["answered"] is False
    assert "120000" not in payload["answer"]


def test_the_admin_key_gets_the_secret_answer(api_client):
    response = api_client.post(
        "/api/ask",
        json={"question": "What does a level three engineer earn?", "use_cache": False},
        headers=ADMIN_HEADERS,
    )
    payload = response.json()

    assert payload["answered"] is True
    cited_sources = []
    for citation in payload["citations"]:
        cited_sources.append(citation["source"])
    assert "salary_bands.md" in cited_sources


def test_the_document_list_differs_by_key(api_client):
    as_admin = api_client.get("/api/documents", headers=ADMIN_HEADERS).json()
    as_user = api_client.get("/api/documents", headers=USER_HEADERS).json()

    assert len(as_admin) == 4
    assert len(as_user) == 2

    for document in as_user:
        assert document["access_tag"] == "public"


# ---------------- answering ----------------

def test_asking_returns_citations_and_a_trace(api_client):
    response = api_client.post(
        "/api/ask",
        json={"question": "What does express delivery cost?", "use_cache": False},
        headers=ADMIN_HEADERS,
    )
    payload = response.json()

    assert payload["answered"] is True
    assert "12.99" in payload["answer"]
    assert len(payload["citations"]) > 0
    assert payload["trace"]["vector_hits"] > 0
    assert payload["request_id"] != ""


def test_an_empty_question_is_rejected(api_client):
    response = api_client.post("/api/ask", json={"question": "   "}, headers=ADMIN_HEADERS)
    assert response.status_code == 400


def test_the_debug_endpoint_returns_scored_passages(api_client):
    response = api_client.post(
        "/api/retrieve",
        json={"question": "What encryption is used for stored customer data?"},
        headers=ADMIN_HEADERS,
    )
    payload = response.json()

    assert len(payload["passages"]) > 0
    first = payload["passages"][0]
    assert first["source"] == "security_faq.md"
    assert first["rerank_score"] is not None


# ---------------- caching ----------------

def test_the_same_question_twice_is_a_cache_hit(api_client):
    question = {"question": "How long do I have to ask for a refund?", "use_cache": True}

    first = api_client.post("/api/ask", json=question, headers=ADMIN_HEADERS).json()
    second = api_client.post("/api/ask", json=question, headers=ADMIN_HEADERS).json()

    assert first["usage"]["cache_hit"] is False
    assert second["usage"]["cache_hit"] is True
    assert second["answer"] == first["answer"]


def test_a_refusal_is_never_cached(api_client):
    """
    Caching "I don't know" would keep serving it after the missing document is
    finally added, which looks exactly like a broken system.
    """
    question = {"question": "What is the parental leave policy?", "use_cache": True}

    api_client.post("/api/ask", json=question, headers=ADMIN_HEADERS)
    second = api_client.post("/api/ask", json=question, headers=ADMIN_HEADERS).json()

    assert second["answered"] is False
    assert second["usage"]["cache_hit"] is False


def test_the_cache_is_not_shared_between_different_permissions(api_client):
    """
    An admin answer built from secret documents must never be served to a user
    who may only read public ones.
    """
    question = {"question": "What does a level three engineer earn?", "use_cache": True}

    as_admin = api_client.post("/api/ask", json=question, headers=ADMIN_HEADERS).json()
    as_user = api_client.post("/api/ask", json=question, headers=USER_HEADERS).json()

    assert as_admin["answered"] is True
    assert as_user["answered"] is False
    assert "120000" not in as_user["answer"]


# ---------------- streaming ----------------

def test_the_streaming_endpoint_sends_tokens_then_a_verdict(api_client):
    with api_client.stream(
        "POST",
        "/api/ask/stream",
        json={"question": "What does express delivery cost?"},
        headers=ADMIN_HEADERS,
    ) as response:
        assert response.status_code == 200
        body = ""
        for chunk in response.iter_text():
            body = body + chunk

    assert '"type": "token"' in body
    assert '"type": "done"' in body
    assert "citations" in body


# ---------------- housekeeping ----------------

def test_health_reports_the_indexed_chunk_count(api_client):
    payload = api_client.get("/api/health").json()
    assert payload["status"] == "ok"
    assert payload["chunks_indexed"] > 0


def test_config_never_returns_the_api_key(api_client):
    body = api_client.get("/api/config").text
    assert "sk-" not in body
    assert "openai_api_key" not in body


def test_metrics_are_recorded_after_a_question(api_client):
    api_client.post(
        "/api/ask",
        json={"question": "What does express delivery cost?", "use_cache": False},
        headers=ADMIN_HEADERS,
    )
    snapshot = api_client.get("/api/metrics").json()

    assert snapshot["counters"]["requests_total"] >= 1
    assert "request_latency_ms" in snapshot["distributions"]


def test_deleting_an_invisible_document_returns_not_found(api_client):
    """The user key must not learn that the secret document exists."""
    as_admin = api_client.get("/api/documents", headers=ADMIN_HEADERS).json()

    secret_id = ""
    for document in as_admin:
        if document["access_tag"] == "secret":
            secret_id = document["document_id"]

    assert secret_id != ""
    response = api_client.delete("/api/documents/" + secret_id, headers=USER_HEADERS)
    # A user key cannot delete at all, and also must not be told it exists.
    assert response.status_code in (403, 404)


def test_the_ui_is_served_at_the_root(api_client):
    response = api_client.get("/")
    assert response.status_code == 200
    assert "RAG Knowledge Assistant" in response.text


def test_the_stream_reports_its_token_usage(api_client):
    """
    Streaming used to report zero tokens and zero cost. If the UI path is the one
    real users take, an unmeasured streaming path means an unmeasured bill.
    """
    import json

    with api_client.stream(
        "POST",
        "/api/ask/stream",
        json={"question": "What does express delivery cost?"},
        headers=ADMIN_HEADERS,
    ) as response:
        body = ""
        for chunk in response.iter_text():
            body = body + chunk

    done_message = None
    for line in body.split("\n\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        message = json.loads(line[5:].strip())
        if message.get("type") == "done":
            done_message = message

    assert done_message is not None
    assert done_message["usage"]["prompt_tokens"] > 0
    assert done_message["usage"]["total_tokens"] > 0


def test_every_model_call_is_counted_not_just_the_answer(seeded_store, embedder):
    """
    A REAL BUG THIS TEST EXISTS TO PREVENT.

    In live mode a single question makes up to four calls: embedding, query
    rewriting, reranking, and writing the answer. Only the last one was being
    counted, so a refused question reported "0 tokens, $0.000000" after making
    two real API calls and taking 3.6 seconds. The bill disagreed.

    A cost number you trust and that is wrong is worse than no cost number.
    """
    from rag_assistant.layer0_shared.cache import build_cache
    from rag_assistant.layer0_shared.llm_client import LlmResult
    from rag_assistant.layer2_models.schemas import AskRequest
    from rag_assistant.layer7_api.assistant_service import AssistantService

    class CountingLiveClient:
        """Pretends to be live, so the rewrite and rerank paths actually run."""

        model = "test-live-model"
        is_live = True

        def __init__(self):
            self.calls = 0

        def complete(self, system_prompt, user_prompt, json_mode=False):
            self.calls = self.calls + 1
            if json_mode and "rewrite" in system_prompt.lower():
                text = '{"queries": ["refund window", "refund policy"]}'
            elif json_mode:
                text = '{"scores": [{"id": 1, "score": 9}, {"id": 2, "score": 2}]}'
            else:
                text = "Refunds may be requested within 30 days [1]."
            return LlmResult(
                text=text,
                model=self.model,
                prompt_tokens=100,
                completion_tokens=20,
                cost_usd=0.000042,
            )

        def stream(self, system_prompt, user_prompt, usage_sink=None):
            yield self.complete(system_prompt, user_prompt).text

    client = CountingLiveClient()
    service = AssistantService(
        store=seeded_store, embedder=embedder, chat_client=client, cache=build_cache()
    )

    response = service.ask(
        AskRequest(question="How long do I have to ask for a refund?", use_cache=False),
        allowed_tags=["public", "internal"],
    )

    # Rewrite, rerank and answer all ran.
    assert client.calls >= 3

    stages = response.usage.by_stage
    assert "query_rewrite" in stages
    assert "rerank" in stages
    assert "answer" in stages

    # The reported total is the sum of every call, not just the answer.
    assert response.usage.prompt_tokens >= 300
    assert response.usage.estimated_cost_usd >= 0.000126
