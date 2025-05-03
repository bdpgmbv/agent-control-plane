"""The HTTP surface."""

import time

import pytest
from fastapi.testclient import TestClient

from coding_agent.layer9_api.app import app


@pytest.fixture
def client():
    with TestClient(app) as made:
        yield made


def test_health_reports_offline_and_the_budget(client):
    body = client.get("/api/health").json()
    assert body["ok"]
    assert body["mode"] == "offline"
    assert body["budget"]["max_iterations"] > 0
    assert body["tasks"] == 8


def test_health_lists_the_protected_files(client):
    body = client.get("/api/health").json()
    assert "conftest.py" in body["protected_globs"]
    assert "pytest.ini" in body["protected_globs"]


def test_the_tasks_are_listed_with_their_tickets(client):
    body = client.get("/api/tasks").json()
    assert len(body["tasks"]) == 8
    for task in body["tasks"]:
        assert len(task["issue"]) > 40
        assert len(task["target_tests"]) > 0
        assert len(task["files_broken"]) > 0


def test_one_task_can_be_fetched(client):
    body = client.get("/api/tasks/tax_sign").json()
    assert body["title"] != ""
    assert "total_with_tax" in body["issue"]


def test_a_missing_task_is_a_clean_404(client):
    assert client.get("/api/tasks/nope").status_code == 404


def test_a_run_can_be_started_and_polled(client):
    started = client.post("/api/runs", json={"task_ids": ["tax_sign"], "live": False})
    assert started.status_code == 200
    run_id = started.json()["run_id"]

    for _ in range(60):
        body = client.get("/api/runs/%s" % run_id).json()
        if body["status"] != "running":
            break
        time.sleep(0.5)

    assert body["status"] == "finished"
    assert body["done"] == 1
    assert body["results"][0]["outcome"] == "solved"
    assert body["summary"]["accepted_with_tampered_tests"] == 0
    assert "CODING AGENT BENCHMARK" in body["report"]


def test_an_unknown_task_is_refused_before_anything_starts(client):
    response = client.post("/api/runs", json={"task_ids": ["nope"], "live": False})
    assert response.status_code == 400
    assert "no task called" in response.json()["detail"]


def test_a_live_run_without_a_key_is_refused_clearly(client):
    response = client.post("/api/runs", json={"task_ids": ["tax_sign"], "live": True})
    assert response.status_code == 400
    assert "OPENAI_API_KEY" in response.json()["detail"]


def test_an_unknown_run_is_a_clean_404(client):
    assert client.get("/api/runs/run_nope").status_code == 404


def test_the_interface_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "AI Coding Agent" in response.text
    assert client.get("/ui/app.js").status_code == 200
    assert client.get("/ui/style.css").status_code == 200
