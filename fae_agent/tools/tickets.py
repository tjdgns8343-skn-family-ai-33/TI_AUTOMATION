"""티켓(문의 건) 저장소. SQLite 파일 하나(data/tickets.db)를 씁니다."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from ..config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    customer TEXT,
    subject TEXT,
    body TEXT,
    category TEXT,
    route TEXT,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    stage TEXT NOT NULL,
    payload TEXT
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """연결을 열고, 끝나면 커밋 후 반드시 닫습니다 (Windows 파일 잠금 방지)."""
    path = get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        with conn:
            yield conn
    finally:
        conn.close()


def create_ticket(customer: str, subject: str, body: str) -> dict[str, Any]:
    """새 문의 건을 만들고 ticket_id를 돌려줍니다."""
    ticket_id = f"TCK-{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
    with _db() as conn:
        conn.execute(
            "INSERT INTO tickets (id, created_at, customer, subject, body, status) VALUES (?, ?, ?, ?, ?, ?)",
            (ticket_id, _now(), customer, subject, body, "received"),
        )
    return {"ticket_id": ticket_id}


def update_ticket(ticket_id: str, category: str = "", route: str = "", status: str = "") -> dict[str, Any]:
    """분류·경로·상태를 바꿉니다. 빈 값은 그대로 둡니다."""
    fields = {k: v for k, v in {"category": category, "route": route, "status": status}.items() if v}
    if fields:
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with _db() as conn:
            conn.execute(f"UPDATE tickets SET {assignments} WHERE id = ?", (*fields.values(), ticket_id))
    return {"ticket_id": ticket_id, "updated": list(fields)}


def add_event(ticket_id: str, stage: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """처리 단계 기록을 남깁니다."""
    with _db() as conn:
        conn.execute(
            "INSERT INTO events (ticket_id, ts, stage, payload) VALUES (?, ?, ?, ?)",
            (ticket_id, _now(), stage, json.dumps(payload or {}, ensure_ascii=False)),
        )
    return {"ticket_id": ticket_id, "stage": stage}


def get_ticket(ticket_id: str) -> dict[str, Any]:
    """티켓과 처리 기록을 돌려줍니다."""
    with _db() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        events = conn.execute(
            "SELECT ts, stage, payload FROM events WHERE ticket_id = ? ORDER BY id", (ticket_id,)
        ).fetchall()
    if row is None:
        return {"found": False}
    return {
        "found": True,
        "ticket": dict(row),
        "events": [{"ts": e["ts"], "stage": e["stage"], "payload": json.loads(e["payload"] or "{}")} for e in events],
    }


def list_tickets(limit: int = 20) -> dict[str, Any]:
    """최근 티켓 목록."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, created_at, customer, subject, category, route, status FROM tickets "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {"tickets": [dict(r) for r in rows]}


TICKET_TOOLS = {fn.__name__: fn for fn in (create_ticket, update_ticket, add_event, get_ticket, list_tickets)}
