"""TESTS FOR LAYER 7 - the HTTP API, roles, and the approval flow."""

from tests.conftest import ALICE_HEADERS, BOB_HEADERS, STAFF_HEADERS

# ---------------- authentication ----------------

def test_a_request_with_no_key_is_rejected(api_client):
    assert api_client.post("/api/chat", json={"message": "hello"}).status_code == 401


def test_an_unknown_key_is_rejected(api_client):
    response = api_client.post(
        "/api/chat", json={"message": "hello"}, headers={"X-API-Key": "made-up"}
    )
    assert response.status_code == 401


def test_the_customer_id_comes_from_the_key_not_the_request(api_client):
    """
    A customer key always acts for itself. If the request could choose, every
    customer could read every other customer's data by editing one field.
    """
    response = api_client.post(
        "/api/chat?acting_for=CUST-1002",
        json={"message": "Where is my order ORD-20001?"},
        headers=ALICE_HEADERS,
    )
    payload = response.json()
    assert "standing desk" not in payload["reply"].lower()


def test_support_staff_may_act_for_a_named_customer(api_client):
    response = api_client.post(
        "/api/chat?acting_for=CUST-1002",
        json={"message": "Where is my order ORD-20001?"},
        headers=STAFF_HEADERS,
    )
    payload = response.json()
    assert payload["tool_calls"][0]["outcome"] == "ok"


# ---------------- chat ----------------

def test_a_normal_question_is_answered(api_client):
    response = api_client.post(
        "/api/chat", json={"message": "Where is my order ORD-10023?"}, headers=ALICE_HEADERS
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["intent"] == "order_status"
    assert payload["conversation_id"] != ""
    assert payload["request_id"] != ""
    assert payload["usage"]["total_tokens"] > 0


def test_an_empty_message_is_rejected(api_client):
    response = api_client.post("/api/chat", json={"message": "   "}, headers=ALICE_HEADERS)
    assert response.status_code == 400


# ---------------- conversations ----------------

def test_a_customer_sees_only_their_own_conversations(api_client):
    api_client.post("/api/chat", json={"message": "Where is my order ORD-10023?"}, headers=ALICE_HEADERS)
    api_client.post("/api/chat", json={"message": "Where is my order ORD-20001?"}, headers=BOB_HEADERS)

    as_alice = api_client.get("/api/conversations", headers=ALICE_HEADERS).json()
    as_staff = api_client.get("/api/conversations", headers=STAFF_HEADERS).json()

    assert len(as_alice) == 1
    assert len(as_staff) == 2


def test_reading_someone_elses_conversation_is_a_404_not_a_403(api_client):
    """403 would confirm the conversation exists. 404 tells them nothing."""
    started = api_client.post(
        "/api/chat", json={"message": "Where is my order ORD-20001?"}, headers=BOB_HEADERS
    ).json()

    response = api_client.get(
        "/api/conversations/" + started["conversation_id"], headers=ALICE_HEADERS
    )
    assert response.status_code == 404


# ---------------- the approval flow ----------------

def create_pending_approval(api_client):
    api_client.post(
        "/api/chat",
        json={"message": "I want a refund for ORD-10025, the espresso machine is broken"},
        headers=ALICE_HEADERS,
    )
    return api_client.get("/api/approvals?status=pending", headers=STAFF_HEADERS).json()


def test_a_customer_cannot_see_the_approval_queue(api_client):
    assert api_client.get("/api/approvals", headers=ALICE_HEADERS).status_code == 403


def test_a_customer_cannot_approve_their_own_refund(api_client):
    """The obvious attack, and the reason approval is a separate role."""
    pending = create_pending_approval(api_client)
    assert len(pending) == 1

    response = api_client.post(
        "/api/approvals/decide",
        json={"approval_id": pending[0]["approval_id"], "approve": True},
        headers=ALICE_HEADERS,
    )
    assert response.status_code == 403


def test_support_staff_can_approve_and_the_refund_is_actually_issued(api_client, database):
    pending = create_pending_approval(api_client)

    response = api_client.post(
        "/api/approvals/decide",
        json={"approval_id": pending[0]["approval_id"], "approve": True, "note": "checked"},
        headers=STAFF_HEADERS,
    )
    payload = response.json()

    assert payload["status"] == "approved"
    assert payload["tool_outcome"] == "ok"

    refund = database.get_refund_for_order("ORD-10025")
    assert refund is not None
    assert refund["amount"] == 899.00


def test_approving_twice_is_refused(api_client):
    """Guards against two people clicking approve at the same moment."""
    pending = create_pending_approval(api_client)
    body = {"approval_id": pending[0]["approval_id"], "approve": True}

    first = api_client.post("/api/approvals/decide", json=body, headers=STAFF_HEADERS)
    second = api_client.post("/api/approvals/decide", json=body, headers=STAFF_HEADERS)

    assert first.status_code == 200
    assert second.status_code == 409


def test_rejecting_pays_nothing_and_tells_the_customer(api_client, database):
    pending = create_pending_approval(api_client)

    response = api_client.post(
        "/api/approvals/decide",
        json={"approval_id": pending[0]["approval_id"], "approve": False,
              "note": "The item was used."},
        headers=STAFF_HEADERS,
    )
    payload = response.json()

    assert payload["status"] == "rejected"
    assert database.get_refund_for_order("ORD-10025") is None

    transcript = api_client.get(
        "/api/conversations/" + pending[0]["conversation_id"], headers=STAFF_HEADERS
    ).json()
    last_message = transcript["messages"][-1]
    assert "not able to approve" in last_message["content"]


def test_an_approval_still_runs_every_other_check(api_client, database):
    """
    An approval says "that amount is acceptable". It does not say "skip the rest".

    Here the order is refunded by another route between the hold and the
    approval. The approved action must still refuse.
    """
    pending = create_pending_approval(api_client)

    database.insert_refund(
        {
            "refund_id": "REF-SNEAK",
            "order_id": "ORD-10025",
            "customer_id": "CUST-1001",
            "amount": 899.00,
            "status": "processing",
            "reason": "refunded another way",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )

    response = api_client.post(
        "/api/approvals/decide",
        json={"approval_id": pending[0]["approval_id"], "approve": True},
        headers=STAFF_HEADERS,
    )
    payload = response.json()

    assert payload["tool_outcome"] == "failed"
    assert "already has refund" in payload["result"]["error_message"]
    # Still exactly one refund on that order.
    assert database.count_refunds() == 2      # the seeded one plus REF-SNEAK


# ---------------- the audit log ----------------

def test_a_customer_cannot_read_the_audit_log(api_client):
    assert api_client.get("/api/audit", headers=ALICE_HEADERS).status_code == 403


def test_the_audit_log_records_a_refused_cross_customer_read(api_client):
    api_client.post(
        "/api/chat", json={"message": "Where is my order ORD-20001?"}, headers=ALICE_HEADERS
    )
    records = api_client.get("/api/audit", headers=STAFF_HEADERS).json()

    flagged = False
    for record in records:
        if "cross_customer_access_attempt" in record["detail"]:
            flagged = True
    assert flagged is True


def test_the_audit_log_holds_no_personal_data(api_client):
    api_client.post(
        "/api/chat",
        json={"message": "my card 4532015112830366 and email alice@example.com were used"},
        headers=ALICE_HEADERS,
    )
    body = api_client.get("/api/audit", headers=STAFF_HEADERS).text

    assert "4532015112830366" not in body
    assert "alice@example.com" not in body


# ---------------- housekeeping ----------------

def test_config_never_returns_the_key(api_client):
    body = api_client.get("/api/config").text
    assert "sk-" not in body
    assert "openai_api_key" not in body


def test_health_reports_the_seeded_data(api_client):
    payload = api_client.get("/api/health").json()
    assert payload["status"] == "ok"
    assert payload["counts"]["orders"] == 7


def test_the_tool_list_shows_risk_levels(api_client):
    tools = api_client.get("/api/tools").json()

    risk_by_name = {}
    for tool in tools:
        risk_by_name[tool["name"]] = tool["risk"]

    assert risk_by_name["get_order"] == "read"
    assert risk_by_name["create_ticket"] == "write_low"
    assert risk_by_name["issue_refund"] == "write_high"


def test_the_ui_is_served_at_the_root(api_client):
    response = api_client.get("/")
    assert response.status_code == 200
    assert "AI Customer Support Agent" in response.text
