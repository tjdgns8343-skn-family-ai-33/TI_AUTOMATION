"""tickets.* 도구의 Django 구현. fae_agent/tools/tickets.py(SQLite)와 함수 이름·인자·반환 형식이 같다."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from .models import Ticket, TicketEvent


def create_ticket(customer: str, subject: str, body: str) -> dict[str, Any]:
    ticket_id = f"TCK-{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
    Ticket.objects.create(id=ticket_id, customer=customer, subject=subject, body=body, status="received")
    return {"ticket_id": ticket_id}


def update_ticket(ticket_id: str, category: str = "", route: str = "", status: str = "") -> dict[str, Any]:
    fields = {k: v for k, v in {"category": category, "route": route, "status": status}.items() if v}
    if fields:
        Ticket.objects.filter(pk=ticket_id).update(**fields)
    return {"ticket_id": ticket_id, "updated": list(fields)}


def add_event(ticket_id: str, stage: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    TicketEvent.objects.create(ticket_id=ticket_id, stage=stage, payload=payload or {})
    return {"ticket_id": ticket_id, "stage": stage}


def get_ticket(ticket_id: str) -> dict[str, Any]:
    ticket = Ticket.objects.filter(pk=ticket_id).first()
    if ticket is None:
        return {"found": False}
    events = [
        {"ts": e.ts.isoformat(timespec="seconds"), "stage": e.stage, "payload": e.payload}
        for e in ticket.events.all()
    ]
    row = {
        "id": ticket.id,
        "created_at": ticket.created_at.isoformat(timespec="seconds"),
        "customer": ticket.customer,
        "subject": ticket.subject,
        "body": ticket.body,
        "category": ticket.category,
        "route": ticket.route,
        "status": ticket.status,
    }
    return {"found": True, "ticket": row, "events": events}


def list_tickets(limit: int = 20) -> dict[str, Any]:
    rows = Ticket.objects.all()[:limit]
    return {
        "tickets": [
            {
                "id": t.id,
                "created_at": t.created_at.isoformat(timespec="seconds"),
                "customer": t.customer,
                "subject": t.subject,
                "category": t.category,
                "route": t.route,
                "status": t.status,
            }
            for t in rows
        ]
    }


DJANGO_TICKET_TOOLS = {fn.__name__: fn for fn in (create_ticket, update_ticket, add_event, get_ticket, list_tickets)}


def install() -> None:
    """게이트웨이의 tickets 서버 함수를 Django 구현으로 교체한다 (MCP 서버 모듈 이름은 그대로)."""
    from fae_agent.tools import gateway

    module = gateway.SERVERS["tickets"][1]
    gateway.SERVERS["tickets"] = (DJANGO_TICKET_TOOLS, module)
