"""The HTTP surface."""

import time

import pytest
from fastapi.testclient import TestClient
from tests.conftest import EXPENSIVE_REQUEST, REQUEST, TODAY

from enterprise_workflow.layer9_api.app import SERVICE, app


@pytest.fixture
def client():
    SERVICE.stop_worker()          # tests advance runs themselves, deterministically
    SERVICE.store.clear()
    with TestClient(app) as made:
        yield made
    SERVICE.store.clear()


def start(client, text=REQUEST, **extra):
    payload = {"request_text": text, "today_override": TODAY}
    for key, value in extra.items():
        payload[key] = value
    response = client.post("/api/runs", json={"workflow_name": "employee_onboarding",
                                              "input": payload})
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


def test_health_reports_the_settings(client):
    body = client.get("/api/health").json()
    assert body["ok"]
    assert body["mode"] == "offline"
    assert body["settings"]["max_attempts"] == 3
    assert "employee_onboarding" in body["workflows"]
    assert len(body["steps"]) == 8


def test_an_empty_request_is_refused_before_a_run_is_made(client):
    response = client.post("/api/runs", json={
        "workflow_name": "employee_onboarding", "input": {"request_text": "   "}})
    assert response.status_code == 400
    assert "nothing to read" in response.json()["detail"]


def test_an_unknown_workflow_is_refused(client):
    response = client.post("/api/runs", json={"workflow_name": "nope",
                                              "input": {"request_text": "x"}})
    assert response.status_code == 400


def test_a_run_can_be_started_and_advanced(client):
    run_id = start(client)
    SERVICE.advance()

    body = client.get("/api/runs/%s" % run_id).json()
    assert body["run"]["state"] == "succeeded"
    assert len(body["steps"]) == 8
    assert len(body["side_effects"]) == 5
    assert len(body["events"]) > 8


def test_a_missing_run_is_a_clean_404(client):
    assert client.get("/api/runs/run_nope").status_code == 404


def test_the_run_list_shows_progress(client):
    start(client)
    SERVICE.advance()
    rows = client.get("/api/runs").json()["runs"]
    assert len(rows) == 1
    assert rows[0]["steps_done"] == rows[0]["steps_total"]
    assert rows[0]["person"] == "Ada Lovelace"


def test_an_approval_can_be_answered_over_http(client):
    run_id = start(client, text=EXPENSIVE_REQUEST)
    SERVICE.advance()

    approvals = client.get("/api/approvals").json()["approvals"]
    assert len(approvals) == 1

    response = client.post("/api/approvals/%s/decide" % approvals[0]["approval_id"],
                           json={"approved": True, "decided_by": "grace", "note": ""})
    assert response.status_code == 200
    assert response.json()["ok"]

    SERVICE.advance()
    assert client.get("/api/runs/%s" % run_id).json()["run"]["state"] == "succeeded"


def test_answering_twice_is_refused(client):
    start(client, text=EXPENSIVE_REQUEST)
    SERVICE.advance()
    approval_id = client.get("/api/approvals").json()["approvals"][0]["approval_id"]

    body = {"approved": True, "decided_by": "grace", "note": ""}
    assert client.post("/api/approvals/%s/decide" % approval_id, json=body).status_code == 200
    second = client.post("/api/approvals/%s/decide" % approval_id, json=body)
    assert second.status_code == 400
    assert "already" in second.json()["message"]


def test_a_run_can_be_cancelled(client):
    run_id = start(client)
    SERVICE.engine.tick()
    SERVICE.engine.tick()

    response = client.post("/api/runs/%s/cancel" % run_id)
    assert response.status_code == 200
    SERVICE.advance()
    assert client.get("/api/runs/%s" % run_id).json()["run"]["state"] == "cancelled"


def test_a_finished_run_cannot_be_cancelled(client):
    run_id = start(client)
    SERVICE.advance()
    response = client.post("/api/runs/%s/cancel" % run_id)
    assert response.status_code == 400


def test_the_crash_button_stops_the_worker(client):
    run_id = start(client)
    SERVICE.start_worker()
    time.sleep(0.2)

    response = client.post("/api/runs/%s/simulate-crash" % run_id)
    assert response.status_code == 200
    assert "lease" in response.json()["message"]
    SERVICE.stop_worker()


def test_reset_empties_everything(client):
    start(client)
    SERVICE.advance()
    assert len(client.get("/api/runs").json()["runs"]) == 1
    client.post("/api/reset")
    assert client.get("/api/runs").json()["runs"] == []


def test_the_interface_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Enterprise Workflow" in response.text
    assert client.get("/ui/app.js").status_code == 200
    assert client.get("/ui/style.css").status_code == 200
