"""TESTS FOR the HTTP API."""


def test_health_reports_the_allowlist(api_client):
    payload = api_client.get("/api/health").json()
    assert payload["status"] == "ok"
    # One table in the file is deliberately not available.
    assert payload["tables_available"] < payload["tables_in_file"]


def test_config_never_returns_the_key(api_client):
    body = api_client.get("/api/config").text
    assert "sk-" not in body
    assert "openai_api_key" not in body


def test_asking_returns_sql_and_a_result(api_client):
    response = api_client.post("/api/ask", json={"question": "How many orders are there in total?"})
    payload = response.json()

    assert response.status_code == 200
    assert payload["answered"] is True
    assert payload["sql"] != ""
    assert payload["result"]["row_count"] == 1


def test_an_empty_question_is_rejected(api_client):
    assert api_client.post("/api/ask", json={"question": "  "}).status_code == 400


def test_a_very_long_question_is_rejected(api_client):
    assert api_client.post("/api/ask", json={"question": "why " * 200}).status_code == 400


def test_a_hidden_table_question_is_refused(api_client):
    payload = api_client.post("/api/ask", json={"question": "What does each employee get paid?"}).json()
    assert payload["refused"] is True
    assert payload["refusal_reason"] == "unknown_table"


def test_the_schema_endpoint_shows_what_is_hidden(api_client):
    """
    Showing that a table exists and is unavailable is more useful than pretending
    it does not, and it makes the allowlist checkable rather than trusted.
    """
    payload = api_client.get("/api/schema").json()
    assert "employee_salaries" in payload["not_available"]

    described = []
    for table in payload["tables"]:
        described.append(table["name"])
    assert "employee_salaries" not in described


def test_the_ui_is_served_at_the_root(api_client):
    response = api_client.get("/")
    assert response.status_code == 200
    assert "SQL Analytics Copilot" in response.text
