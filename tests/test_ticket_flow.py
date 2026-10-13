import sqlite3

import pytest

from app.rag import workflow
from app.services.tickets import DB_PATH, create_ticket, get_ticket, list_tickets


@pytest.fixture
def clean_ticket_db(monkeypatch):
    monkeypatch.setenv("SIMULATE_TICKET_FAILURE_RATE", "0")
    from app.services import tickets

    tickets.init_db()
    tickets._delete_all_tickets_for_tests()
    yield
    tickets._delete_all_tickets_for_tests()


def test_ticket_service_round_trip(clean_ticket_db):
    ticket_id = create_ticket(
        title="VPN login fails",
        category="network",
        priority="high",
        description="User cannot connect to VPN from home.",
    )

    stored = get_ticket(ticket_id)
    assert stored is not None
    assert stored["title"] == "VPN login fails"
    assert stored["category"] == "network"
    assert stored["priority"] == "high"
    assert any(item["id"] == ticket_id for item in list_tickets())


def test_verify_ticket_record_passes_and_fails_on_tamper(clean_ticket_db):
    draft = {
        "title": "Laptop battery drain",
        "category": "hardware",
        "priority": "medium",
        "description": "User reports battery drains quickly after patch rollout.",
    }

    ticket_id = create_ticket(**draft)
    result = workflow.verify_ticket_record(draft, ticket_id)
    assert result["passed"] is True
    assert all(check["passed"] for check in result["checks"])

    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE tickets SET title = ? WHERE id = ?", ("tampered title", ticket_id))
    conn.commit()
    conn.close()
    assert workflow.verify_ticket_record(draft, ticket_id)["passed"] is False


def test_retry_path_retries_after_initial_create_failure(clean_ticket_db, monkeypatch):
    calls = {"count": 0}

    def flaky_create(title, category, priority, description):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("simulated outage")
        return create_ticket(title, category, priority, description)

    monkeypatch.setattr(workflow, "create_ticket", flaky_create)
    state = {
        "question": "Please open a ticket for VPN issue",
        "current_query": "VPN issue",
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
        "intent": "create_ticket",
        "ticket_draft": {
            "title": "VPN issue",
            "category": "network",
            "priority": "high",
            "description": "VPN issue from home office.",
        },
        "approval_status": "approved",
        "ticket_id": "",
        "verification": {},
        "action_retry_count": 0,
    }

    result = workflow.execute_ticket(state)
    assert result["ticket_id"]
    assert calls["count"] == 2
    assert any("retrying once" in item.lower() for item in result["trace"])


def test_rejection_path_creates_no_ticket(clean_ticket_db):
    state = {
        "question": "Open an IT ticket",
        "current_query": "Open an IT ticket",
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
        "intent": "create_ticket",
        "ticket_draft": {
            "title": "MFA not working",
            "category": "identity",
            "priority": "high",
            "description": "MFA prompt loops on login.",
        },
        "approval_status": "pending",
        "ticket_id": "",
        "verification": {},
        "action_retry_count": 0,
    }

    resumed = workflow.await_approval(state, {"approved": False})
    assert resumed["approval_status"] == "rejected"
    assert resumed["ticket_id"] == ""
    assert len(list_tickets()) == 0
