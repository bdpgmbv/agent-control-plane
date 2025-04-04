"""TESTS FOR the HTTP API."""


def test_health_lists_the_sources(api_client):
    payload = api_client.get("/api/health").json()
    assert payload["status"] == "ok"
    assert payload["sources"][0]["documents"] > 0


def test_config_says_how_similarity_is_measured(api_client):
    """
    It changes how much the deduplication can be trusted, so it is stated rather
    than left for the reader to infer.
    """
    payload = api_client.get("/api/config").json()
    assert "similarity_measured_by" in payload
    assert "sk-" not in api_client.get("/api/config").text


def test_research_returns_a_report(api_client):
    response = api_client.post(
        "/api/research", json={"question": "Is the four-day work week good for productivity?"}
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["run_id"] != ""
    assert len(payload["report"]["citations"]) > 0
    assert len(payload["plan"]["sub_questions"]) >= 2
    assert "budget" in payload["trace"]


def test_an_empty_question_is_rejected(api_client):
    assert api_client.post("/api/research", json={"question": "  "}).status_code == 400


def test_a_very_long_question_is_rejected(api_client):
    long_question = "why " * 200
    assert api_client.post("/api/research", json={"question": long_question}).status_code == 400


def test_the_budget_can_be_set_per_request(api_client):
    response = api_client.post(
        "/api/research",
        json={"question": "Does remote work increase productivity?", "max_tool_calls": 1},
    )
    payload = response.json()

    assert payload["trace"]["budget"]["tool_calls"]["limit"] == 1
    assert payload["trace"]["budget"]["tool_calls"]["made"] <= 1
    assert payload["report"]["partial"] is True


def test_the_corpus_can_be_listed(api_client):
    payload = api_client.get("/api/sources").json()
    assert payload["count"] > 15

    types = set()
    for document in payload["documents"]:
        types.add(document["source_type"])
    # The corpus deliberately spans credibility levels.
    assert "peer_reviewed" in types
    assert "company_blog" in types


def test_the_ui_is_served_at_the_root(api_client):
    response = api_client.get("/")
    assert response.status_code == 200
    assert "Agentic Research System" in response.text
