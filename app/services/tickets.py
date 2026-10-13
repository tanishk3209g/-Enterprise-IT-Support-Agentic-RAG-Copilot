import json
import os
import random
import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "tickets.db"


def init_db() -> None:
    """Create the SQLite schema used for ticket persistence."""
    ensure_db()


def ensure_db() -> None:
    """Create the ticket database and schema once so the app can safely persist ticket records."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            priority TEXT NOT NULL,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()


def _delete_all_tickets_for_tests() -> None:
    """Test-only cleanup hook for deterministic ticket checks in isolated runs."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM tickets")
    conn.commit()
    conn.close()


def create_ticket(title: str, category: str, priority: str, description: str) -> int:
    """Create a sandboxed ticket record, optionally failing on a configured simulation rate to exercise retry logic."""
    ensure_db()
    sim_rate = float(os.getenv("SIMULATE_TICKET_FAILURE_RATE", "0") or "0")
    if random.random() < sim_rate:
        raise RuntimeError("Simulated ticket creation failure")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        "INSERT INTO tickets(title, category, priority, description) VALUES (?, ?, ?, ?)",
        (title.strip(), category.strip(), priority.strip(), description.strip()),
    )
    conn.commit()
    ticket_id = int(cursor.lastrowid)
    conn.close()
    return ticket_id


def get_ticket(ticket_id: int) -> dict | None:
    """Read a single ticket record back from SQLite to support independent verification."""
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, title, category, priority, description, created_at FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def list_tickets() -> list[dict]:
    """Return the current ticket list for demo and operational visibility."""
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, title, category, priority, description, created_at FROM tickets ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [{key: row[key] for key in row.keys()} for row in rows]
