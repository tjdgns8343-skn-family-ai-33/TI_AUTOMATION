"""영업·문서·워크플로 도구(MCP 서버 sales, docs, fae_workflow)를 함수로 직접 부른다. 승인 게이트와 안전장치를 확인한다."""

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from fae_agent.config import get_settings
from fae_agent.tools import workflow
from fae_agent.tools.docs import check_document_text, list_documents, read_document
from fae_agent.tools.sales import check_request, classify_inquiry, recommend_parts

ROOT = Path(__file__).resolve().parent.parent
CASES = {c["id"]: c for c in map(json.loads, (ROOT / "data" / "eval" / "eval_sales.jsonl").read_text(encoding="utf-8").splitlines())}


def submit(case_id: str) -> dict:
    case = CASES[case_id]
    return workflow.submit_inquiry(case["customer"], case["subject"], case["body"])


@pytest.fixture(autouse=True)
def fresh_server_state():
    workflow._STATE.update(graph=None, runs={})
    yield
    workflow._STATE.update(graph=None, runs={})


# ---------------------------------------------------------------- 영업 도구


def test_sales_tools_use_the_same_rules_as_the_graph():
    s1 = CASES["S-1"]
    assert classify_inquiry(s1["subject"], s1["body"])["category"] == "quote_sample"
    checked = check_request(s1["subject"], s1["body"])
    assert checked["applicable"] and checked["collected"] == s1["expected_collected"] and checked["missing"] == s1["expected_missing"]
    s4 = CASES["S-4"]
    recommended = recommend_parts(s4["subject"], s4["body"])
    assert recommended["applicable"] and recommended["top"] == s4["expected_top"]
    assert {c["part"]: c["verdict"] for c in recommended["candidates"]} == s4["expected_verdicts"]


def test_sales_tools_say_when_they_do_not_apply():
    assert check_request(CASES["S-4"]["subject"], CASES["S-4"]["body"])["applicable"] is False
    assert recommend_parts(CASES["S-1"]["subject"], CASES["S-1"]["body"])["applicable"] is False


# ---------------------------------------------------------------- 문서 도구


def test_document_tools_reject_path_tricks_and_unknown_files():
    assert list_documents("../..") == {"found": False, "ticket_id": "../.."}
    assert read_document("../tickets", "manifest.json")["found"] is False
    assert list_documents() == {"tickets": []}


def test_check_document_text_catches_a_promise():
    source = "샘플 요청을 받았습니다."
    assert check_document_text(source, source)["ok"] is True
    result = check_document_text(source + " 납기는 3주, 무상 교환해 드립니다.", source)
    assert result["ok"] is False and result["issues"]


# ---------------------------------------------------------------- 워크플로: 승인 게이트


def test_submit_stops_at_review_and_creates_no_documents():
    snap = submit("S-1")
    assert snap["status"] == "awaiting_review" and snap["options"] == ["approve", "revise", "reject"]
    assert snap["category"] == "quote_sample" and snap["draft"]["customer_reply"] and "documents" not in snap
    assert not list(get_settings().outbox_dir.glob("*"))  # 승인 전에는 문서 폴더도 없다
    assert [p["thread_id"] for p in workflow.list_pending()["pending"]] == [snap["thread_id"]]
    assert workflow.get_draft(snap["thread_id"])["draft"] == snap["draft"]


def test_approve_generates_documents_that_the_docs_tools_can_read_and_verify():
    snap = submit("S-4")
    done = workflow.review(snap["thread_id"], "approve", "kim", "숫자 확인함")
    assert done["status"] == "answered" and done["documents"]["status"] == "generated"
    assert done["review"]["action"] == "approve" and "[kim]" in done["review"]["comment"]
    ticket = done["ticket_id"]
    listing = list_documents(ticket)
    assert listing["found"] and listing["verified"] and "internal_report.md" in listing["files"] and listing["approval"]["action"] == "approve"
    assert "[kim]" in listing["approval"]["comment"]
    assert read_document(ticket, "internal_report.md")["content"].startswith("#")
    assert [t["ticket_id"] for t in list_documents()["tickets"]] == [ticket]
    assert workflow.list_pending() == {"pending": []}


def test_changed_documents_fail_verification():
    done = workflow.review(submit("S-1")["thread_id"], "approve", "kim")
    report = get_settings().outbox_dir / done["ticket_id"] / "internal_report.md"
    report.write_text(report.read_text(encoding="utf-8") + "\n납기 2주 보장", encoding="utf-8")
    listing = list_documents(done["ticket_id"])
    assert listing["verified"] is False and listing["changed"] == ["internal_report.md"]


def test_reject_makes_no_documents_and_a_decided_run_cannot_be_decided_again():
    snap = submit("S-1")
    rejected = workflow.review(snap["thread_id"], "reject", "kim", "범위 밖")
    assert rejected["status"] == "rejected" and "documents" not in rejected
    assert not list(get_settings().outbox_dir.glob("*"))
    again = workflow.review(snap["thread_id"], "approve", "kim")
    assert "error" in again and not list(get_settings().outbox_dir.glob("*"))


def test_revise_returns_to_review_and_documents_wait_for_the_final_approval():
    snap = submit("S-1")
    revised = workflow.review(snap["thread_id"], "revise", "kim", "수량 확인 문구를 더 분명하게")
    assert revised["status"] == "awaiting_review" and "documents" not in revised
    assert not list(get_settings().outbox_dir.glob("*"))
    done = workflow.review(snap["thread_id"], "approve", "kim")
    assert done["documents"]["status"] == "generated"


def test_review_input_is_checked():
    snap = submit("S-1")
    assert "decision" in workflow.review(snap["thread_id"], "maybe", "kim")["error"]  # 모르는 값이 승인으로 처리되면 안 된다
    assert "reviewer" in workflow.review(snap["thread_id"], "approve", "  ")["error"]
    assert "알 수 없는" in workflow.review("nope", "approve", "kim")["error"]
    assert workflow.get_draft(snap["thread_id"])["status"] == "awaiting_review"  # 잘못된 호출은 상태를 바꾸지 않는다
    assert "본문" in workflow.submit_inquiry("c", "s", "  ")["error"]


def test_server_refuses_to_skip_human_review_or_spend_money(monkeypatch):
    monkeypatch.setenv("AUTO_APPROVE", "true")
    get_settings.cache_clear()
    assert "AUTO_APPROVE" in submit("S-1")["error"]
    monkeypatch.setenv("AUTO_APPROVE", "false")
    monkeypatch.setenv("FAE_MODE", "llm")
    monkeypatch.delenv("FAE_ALLOW_LLM", raising=False)
    get_settings.cache_clear()
    assert "FAE_ALLOW_LLM" in submit("S-1")["error"]
    assert workflow._STATE["graph"] is None  # 거부되면 그래프도 만들지 않는다
