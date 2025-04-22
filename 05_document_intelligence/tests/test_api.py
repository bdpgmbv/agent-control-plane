"""The HTTP surface, driven the way the browser drives it."""

import pytest
from fastapi.testclient import TestClient

from doc_intelligence.layer9_api.app import app, store


@pytest.fixture
def client():
    store.clear()
    with TestClient(app) as made:
        yield made
    store.clear()


def test_health_reports_offline_and_its_gates(client):
    body = client.get("/api/health").json()
    assert body["ok"]
    assert body["mode"] == "offline"
    assert body["gates"]["auto_approve_confidence"] == 0.90
    assert body["gates"]["always_review_above_amount"] == 10000.0


def test_the_sample_list_is_served(client):
    body = client.get("/api/samples").json()
    assert "01_invoice_clean.txt" in body["samples"]
    assert len(body["samples"]) == 13


def test_a_sample_can_be_read(client):
    body = client.get("/api/samples/01_invoice_clean.txt").json()
    assert "INV-2025-0412" in body["text"]


def test_a_sample_name_cannot_escape_the_samples_folder(client):
    # Without the resolve-and-compare check, this reads the API key out of .env.
    for attempt in ("../.env", "..%2F.env", "../../etc/passwd"):
        response = client.get("/api/samples/" + attempt)
        assert response.status_code in (400, 404), attempt
        assert "OPENAI_API_KEY" not in response.text


def test_processing_text_returns_the_whole_result(client):
    text = open("samples/01_invoice_clean.txt").read()
    body = client.post("/api/process/text",
                       json={"text": text, "filename": "01_invoice_clean.txt"}).json()
    result = body["result"]
    assert result["document_type"] == "invoice"
    assert result["decision"] == "auto_approve"
    assert result["structured"]["fields"]["total_amount"]["value"] == "2787.60"


def test_empty_text_is_refused_with_a_reason(client):
    response = client.post("/api/process/text", json={"text": "   "})
    assert response.status_code == 400
    assert "no text" in response.json()["detail"]


def test_an_unsupported_upload_is_refused_clearly(client):
    response = client.post("/api/process/upload",
                           files={"file": ("accounts.xlsx", b"data",
                                           "application/vnd.ms-excel")})
    assert response.status_code == 400
    assert "cannot read" in response.json()["detail"]


def test_an_upload_is_processed(client):
    payload = open("samples/10_receipt_cafe.txt", "rb").read()
    body = client.post("/api/process/upload",
                       files={"file": ("10_receipt_cafe.txt", payload, "text/plain")}).json()
    assert body["result"]["document_type"] == "receipt"


def test_the_batch_endpoint_returns_the_numbers_a_finance_team_asks_for(client):
    body = client.post("/api/process/samples").json()
    summary = body["summary"]
    assert summary["processed"] == 13
    assert summary["auto_approved"] == 3
    assert summary["straight_through_rate"] == round(3 / 13, 3)
    assert summary["total_errors"] == 7
    assert len(body["documents"]) == 13
    for row in body["documents"]:
        assert row["reason"] != ""


def test_the_review_queue_fills_and_explains_itself(client):
    client.post("/api/process/samples")
    reviews = client.get("/api/reviews").json()["reviews"]
    assert len(reviews) == 10
    for review in reviews:
        assert len(review["reasons"]) > 0


def test_a_correction_through_the_api_re_runs_the_checks(client):
    client.post("/api/process/samples")
    reviews = client.get("/api/reviews").json()["reviews"]

    target = None
    for review in reviews:
        if review["filename"] == "02_invoice_arithmetic_error.txt":
            target = review
    assert target is not None

    body = client.post("/api/reviews/%s/decide" % target["review_id"],
                       json={"review_id": target["review_id"], "action": "correct",
                             "corrections": {"subtotal": "4730.00"}}).json()
    assert body["ok"]
    assert "line_items_do_not_sum_to_subtotal (error)" in body["fixed_issues"]
    assert len(body["remaining_issues"]) > 0


def test_an_invalid_review_id_is_a_clean_error(client):
    response = client.post("/api/reviews/rev_nope/decide",
                           json={"review_id": "rev_nope", "action": "approve"})
    assert response.status_code == 400
    assert "no review" in response.json()["message"]


def test_a_document_can_be_fetched_with_its_audit_trail(client):
    text = open("samples/01_invoice_clean.txt").read()
    created = client.post("/api/process/text",
                          json={"text": text, "filename": "a.txt"}).json()
    document_id = created["result"]["document_id"]

    body = client.get("/api/documents/%s" % document_id).json()
    assert body["result"]["document_id"] == document_id
    assert len(body["audit"]) >= 1


def test_a_missing_document_is_a_clean_404(client):
    assert client.get("/api/documents/doc_nope").status_code == 404


def test_reset_empties_everything(client):
    client.post("/api/process/samples")
    assert len(client.get("/api/reviews").json()["reviews"]) > 0
    client.post("/api/reset")
    assert len(client.get("/api/reviews").json()["reviews"]) == 0
    assert len(client.get("/api/documents").json()["documents"]) == 0


def test_the_interface_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Document Intelligence Pipeline" in response.text
    assert client.get("/ui/app.js").status_code == 200
    assert client.get("/ui/style.css").status_code == 200
