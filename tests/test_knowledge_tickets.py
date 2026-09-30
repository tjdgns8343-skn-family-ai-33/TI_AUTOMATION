from pathlib import Path

from fae_agent.ingest import build_chunks, parse_filename
from fae_agent.tools import knowledge, tickets
from fae_agent.tools.gateway import _to_dict


def test_parse_filename():
    assert parse_filename(Path("TPS54331_datasheet.pdf")) == ("TPS54331", "datasheet")
    assert parse_filename(Path("GENERAL_notes_i2c.md")) == ("", "notes")


def test_build_chunks_and_search():
    stats = build_chunks()
    assert stats["files"] >= 3 and stats["chunks"] > 0 and not stats["errors"]
    hits = knowledge.search_docs("출력 발진 phase margin", part_number="EXAMPLE-BUCK1")["results"]
    assert hits and hits[0]["source"].startswith("EXAMPLE-BUCK1_notes.md")


def test_part_filter_excludes_other_parts():
    build_chunks()
    hits = knowledge.search_docs("output ripple phase margin", part_number="EXAMPLE-LDO1")["results"]
    assert all(not h["source"].startswith("EXAMPLE-BUCK1") for h in hits)


def test_part_card_prefix_match():
    result = knowledge.get_part_card("example-buck1dr")
    assert result["found"] and result["card"]["part_number"] == "EXAMPLE-BUCK1"
    assert knowledge.get_part_card("UNKNOWN123")["found"] is False


def test_search_cases():
    hits = knowledge.search_cases("I2C NACK 풀업")["results"]
    assert hits[0]["source"] == "case:C-0002"


def test_ticket_lifecycle():
    ticket_id = tickets.create_ticket("가상전자", "제목", "본문")["ticket_id"]
    tickets.update_ticket(ticket_id, category="technical", status="in_progress")
    tickets.add_event(ticket_id, "intake", {"ok": True})
    data = tickets.get_ticket(ticket_id)
    assert data["ticket"]["category"] == "technical"
    assert data["events"][0]["payload"] == {"ok": True}
    assert tickets.list_tickets()["tickets"][0]["id"] == ticket_id


def test_mcp_result_parsing():
    assert _to_dict('{"a": 1}') == {"a": 1}
    assert _to_dict([{"type": "text", "text": '{"b": 2}'}]) == {"b": 2}
    assert _to_dict(({"c": 3}, None)) == {"c": 3}
    assert _to_dict("plain") == {"text": "plain"}
