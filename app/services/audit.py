import json
import sqlite3
from datetime import datetime, timezone
from app.core.config import get_settings

settings = get_settings()


def init_db() -> None:
    con = sqlite3.connect(settings.audit_db_path)
    con.execute(
        """CREATE TABLE IF NOT EXISTS query_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            question TEXT NOT NULL,
            source_used TEXT NOT NULL,
            trace_json TEXT NOT NULL
        )"""
    )
    con.commit()
    con.close()


def write_audit(question: str, source_used: str, trace: list[str]) -> None:
    con = sqlite3.connect(settings.audit_db_path)
    con.execute(
        "INSERT INTO query_audit(created_at, question, source_used, trace_json) VALUES (?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), question, source_used, json.dumps(trace)),
    )
    con.commit()
    con.close()


def write_ticket_event(event_type: str, question: str, trace: list[str] | None = None, details: dict | None = None) -> None:
    """Keep ticket lifecycle actions in the same audit store so approvals and verifications are observable."""
    payload = {"event_type": event_type}
    if details:
        payload.update(details)
    write_audit(question, event_type, trace or [json.dumps(payload, default=str)])
