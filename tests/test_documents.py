"""승인 뒤 문서화: 승인 전에는 만들지 않고, 승인된 내용만 담고, 승인된 내용에 없는 숫자·약속이 끼면 만들지 않는다."""

import email
import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from fae_agent.agents import documents
from fae_agent.config import get_settings
from fae_agent.docs_check import check_document
from fae_agent.graph import build_graph, new_thread_id, pending_review, resume_run, start_run
from fae_agent.schemas import Complaint

SOURCE = "TPS54331 입력 28 V, 출력 1.8 V에서 소프트 스타트 시간은 40 ms입니다. 데이터시트 p.11 참고."


def load(name: str) -> list[dict]:
    path = get_settings().data_dir / "eval" / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def complaint_of(case: dict) -> Complaint:
    return Complaint(customer=case.get("customer", "평가"), subject=case.get("subject", ""), body=case["body"])


def first_case(category: str) -> dict:
    return next(c for c in load("eval_cases.jsonl") if c["expected_category"] == category)


def outbox() -> Path:
    return get_settings().outbox_dir


# ---------------------------------------------------------------- 문서 검사


def test_a_clean_document_passes_and_phrases_in_the_approved_text_are_allowed():
    assert check_document("소프트 스타트 시간은 40 ms이고 입력 28 V입니다.", SOURCE) == []
    assert check_document("소프트 스타트 40 ms입니다. 5년 보증은 하지 않습니다.", SOURCE + " 5년 보증은 하지 않습니다.") == []  # 사람이 본 표현
    assert check_document("티켓 TCK-20261012-8C7E0A, 2026-10-12", SOURCE, allowed="TCK-20261012-8C7E0A 2026-10-12T14:44:15") == []


@pytest.mark.parametrize(
    ("doctored", "expected"),
    [
        ("소프트 스타트 시간은 400 ms입니다.", "숫자"),  # 숫자를 바꿈
        ("소프트 스타트 40 ms, 효율은 93 %입니다.", "숫자"),  # 새 숫자
        ("TPS54331과 TPS62130A 중에서 고르세요. 40 ms", "부품 번호"),
        ("소프트 스타트 40 ms. 5년간 품질을 보증합니다.", "약속"),
        ("소프트 스타트 40 ms. 불량이면 무상 교환해 드립니다.", "약속"),
        ("소프트 스타트 40 ms. 납기는 리드 타임 4주입니다.", "약속"),
        ("소프트 스타트 40 ms. 단가는 $12 입니다.", "약속"),
        ("소프트 스타트 40 ms. 가격은 3,000원입니다.", "약속"),
        ("소프트 스타트 40 ms [tps54331.pdf p.11]", "내부 파일"),
        ("소프트 스타트 40 ms, 담당자 {name}님께 전달", "자리표시"),
    ],
)
def test_added_numbers_parts_commitments_and_internal_terms_are_caught(doctored, expected):
    issues = check_document(doctored, SOURCE)
    assert issues and any(expected in issue for issue in issues), issues


# ---------------------------------------------------------------- 승인 게이트


def test_nothing_is_written_before_approval_and_after_rejection():
    graph, thread = build_graph(), new_thread_id()
    waiting = start_run(graph, complaint_of(first_case("technical")), thread)
    assert pending_review(waiting) is not None and not outbox().exists()  # 승인 대기 중에는 문서가 없다
    rejected = resume_run(graph, thread, "reject", "근거가 약함")
    assert rejected["status"] == "rejected" and "documents" not in rejected and not outbox().exists()


def test_revise_then_approve_writes_the_documents_once_from_the_latest_draft():
    graph, thread = build_graph(), new_thread_id()
    start_run(graph, complaint_of(first_case("technical")), thread)
    revised = resume_run(graph, thread, "revise", "표현을 줄여 주세요")
    assert pending_review(revised) is not None and not outbox().exists()  # 수정 요청은 문서를 만들지 않는다
    final = resume_run(graph, thread, "approve", "확인")
    folder = outbox() / final["ticket_id"]
    assert final["documents"]["status"] == "generated" and sorted(p.name for p in folder.iterdir()) == sorted(final["documents"]["files"])
    assert final["response"]["customer_reply"] in (folder / "customer_email.md").read_text(encoding="utf-8")


def test_approved_documents_contain_the_approved_reply_and_are_drafts_that_cannot_be_delivered():
    graph, thread = build_graph(), new_thread_id()
    start_run(graph, complaint_of(first_case("technical")), thread)
    final = resume_run(graph, thread, "approve")
    folder = outbox() / final["ticket_id"]
    reply = final["response"]["customer_reply"]
    parsed = email.message_from_bytes((folder / "customer_email.eml").read_bytes())
    assert parsed["X-FAE-Status"] == "DRAFT-NOT-SENT" and parsed["To"].endswith("@example.invalid")
    assert parsed.get_payload(decode=True).decode("utf-8").strip() == reply.strip()
    assert "발송되지 않음" in (folder / "customer_email.md").read_text(encoding="utf-8")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "DRAFT-NOT-SENT" and manifest["approval"]["action"] == "approve"
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest
    summary = json.loads((folder / "ticket_summary.json").read_text(encoding="utf-8"))
    assert summary["ticket_id"] == final["ticket_id"] and summary["decision"] == "approve" and summary["email_status"] == "DRAFT-NOT-SENT"


def test_the_internal_report_has_the_analysis_and_the_quality_claim_8d():
    graph, thread = build_graph(), new_thread_id()
    start_run(graph, complaint_of(first_case("technical")), thread)
    tech = resume_run(graph, thread, "approve")
    report = (outbox() / tech["ticket_id"] / "internal_report.md").read_text(encoding="utf-8")
    for heading in ("## 기본 정보", "## 문의 원문", "## 원인 가설", "## 권장 조치", "## 검수", "## 내부 메모"):
        assert heading in report
    assert "**첫째**" in report and "1." not in report.split("## 원인 가설")[1].split("##")[0]  # 번호 대신 낱말(새 숫자를 만들지 않음)
    graph, thread = build_graph(), new_thread_id()
    start_run(graph, complaint_of(first_case("quality_claim")), thread)
    claim = resume_run(graph, thread, "approve")
    claim_report = (outbox() / claim["ticket_id"] / "internal_report.md").read_text(encoding="utf-8")
    assert "## 반품(CPR) 점검과 8D 초안" in claim_report and "D4" in claim_report


# ---------------------------------------------------------------- 검사에 걸리면 만들지 않는다


def test_a_document_with_an_unapproved_promise_is_blocked_and_nothing_is_written(monkeypatch):
    real = documents.build_documents

    def with_promise(state, generated_at):
        files = real(state, generated_at)
        files["customer_email.md"] += "\n불량이면 무상 교환을 보증합니다. 납기는 3주입니다.\n".encode()
        return files

    monkeypatch.setattr(documents, "build_documents", with_promise)
    graph, thread = build_graph(), new_thread_id()
    start_run(graph, complaint_of(first_case("technical")), thread)
    final = resume_run(graph, thread, "approve")
    assert final["documents"]["status"] == "blocked" and final["status"] == "answered"  # 처리는 끝나고 문서만 막힌다
    assert any("customer_email.md" in issue and "약속" in issue for issue in final["documents"]["issues"])
    assert not outbox().exists() and any("[문서화] blocked" in line for line in final["log"])


def test_a_failure_to_save_does_not_undo_the_approval(monkeypatch):
    def boom(state, generated_at):
        raise OSError("디스크가 가득 참")

    monkeypatch.setattr(documents, "build_documents", boom)
    graph, thread = build_graph(), new_thread_id()
    start_run(graph, complaint_of(first_case("technical")), thread)
    final = resume_run(graph, thread, "approve")
    assert final["status"] == "answered" and final["documents"]["status"] == "error" and "디스크" in final["documents"]["issues"][0]


def test_documents_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("GENERATE_DOCUMENTS", "false")
    get_settings.cache_clear()
    graph, thread = build_graph(), new_thread_id()
    start_run(graph, complaint_of(first_case("technical")), thread)
    final = resume_run(graph, thread, "approve")
    assert final["documents"]["status"] == "skipped" and not outbox().exists()


# ---------------------------------------------------------------- 템플릿이 새 숫자를 만들지 않는지 (여러 문의로)


def test_templates_add_no_unapproved_numbers_across_many_inquiries(monkeypatch):
    """분류가 다른 문의 12건(기술, 품질 클레임, 배송·기타)을 자동 승인으로 돌려 문서가 검사를 통과하는지 본다."""
    monkeypatch.setenv("AUTO_APPROVE", "true")
    get_settings.cache_clear()
    cases = load("eval_cases.jsonl") + load("eval_real_parts.jsonl")[:5]
    assert len(cases) == 12
    graph = build_graph()
    statuses = {}
    for case in cases:
        result = start_run(graph, complaint_of(case), new_thread_id())
        statuses[case["id"]] = (result["documents"]["status"], result["documents"]["issues"])
    assert all(status == "generated" for status, _ in statuses.values()), statuses
