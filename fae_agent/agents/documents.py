"""문서화 에이전트: 사람이 승인한 뒤에만 고객 회신 메일 초안, 내부 보고서, 티켓 요약을 만든다.

- 승인된 답변과 분석 결과만 쓴다. 템플릿은 새 숫자를 만들지 않고(번호 목록 대신 낱말을 쓴다), 문장을 다시 쓰지 않는다.
- 만든 문서는 `docs_check.check_document`로 검사한다. 승인된 내용에 없는 숫자·부품 번호·약속·내부 표현이 있으면 문서를 만들지 않는다.
- 메일은 **초안 저장까지만** 한다. 받는 사람은 자리표시 주소이고 발송은 사람이 한다.
- 파일은 `data/outbox/<티켓>/` (깃 제외)에 저장하고, manifest.json에 파일별 해시와 승인 기록을 남긴다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from email.message import EmailMessage
from typing import Any

from ..config import get_settings
from ..docs_check import check_document

PLACEHOLDER_ADDRESS = "recipient@example.invalid"  # 예약된 .invalid 도메인: 실수로 발송돼도 도착하지 않는다
DRAFT_HEADER = "DRAFT-NOT-SENT"
ORDINALS = ("첫째", "둘째", "셋째")


def _line(value: Any) -> str:
    return " ".join(str(value).split())


def _subject(complaint: dict[str, Any]) -> str:
    subject = _line(complaint.get("subject", ""))
    return f"Re: {subject}" if subject else "문의 회신"


def customer_email_md(state: dict[str, Any]) -> str:
    complaint, response = state.get("complaint") or {}, state.get("response") or {}
    return (
        f"제목: {_subject(complaint)}\n"
        "받는 사람: <고객 이메일 주소를 입력하세요>\n"
        "상태: 초안 (발송되지 않음, 사람이 확인 후 발송)\n"
        f"티켓: {state.get('ticket_id', '')}\n\n"
        f"{response.get('customer_reply', '')}\n"
    )


def customer_email_eml(state: dict[str, Any]) -> bytes:
    complaint, response = state.get("complaint") or {}, state.get("response") or {}
    message = EmailMessage()
    message["Subject"] = _subject(complaint)
    message["To"] = PLACEHOLDER_ADDRESS
    message["From"] = "sales-draft@example.invalid"
    message["X-FAE-Ticket"] = str(state.get("ticket_id", ""))
    message["X-FAE-Status"] = DRAFT_HEADER
    message.set_content(response.get("customer_reply", ""))
    return message.as_bytes()


def _bullets(items: list[Any]) -> list[str]:
    return [f"- {_line(item)}" for item in items] or ["- 없음"]


def internal_report_md(state: dict[str, Any], generated_at: str) -> str:
    complaint, intake = state.get("complaint") or {}, state.get("intake") or {}
    diagnosis, qa = state.get("diagnosis") or {}, state.get("qa") or {}
    review, response = state.get("review") or {}, state.get("response") or {}
    lines = [
        f"# 문의 처리 보고서 ({state.get('ticket_id', '')})",
        "",
        f"작성: {generated_at} (승인 뒤 자동 생성, 승인된 내용만 사용)",
        "",
        "## 기본 정보",
        f"- 고객: {_line(complaint.get('customer', ''))}",
        f"- 접수: {complaint.get('received_at', '')}",
        f"- 제목: {_line(complaint.get('subject', ''))}",
        f"- 분류: {intake.get('category', '')} (경로 {intake.get('route', '')}, 긴급도 {intake.get('urgency', '')})",
        f"- 부품: {', '.join(intake.get('part_numbers') or []) or '미상'}",
        f"- 증상: {', '.join(intake.get('symptoms') or []) or '미상'}",
        f"- 승인 결정: {review.get('action', '')}" + (f" ({_line(review['comment'])})" if review.get("comment") else ""),
        "",
        "## 문의 원문",
        *[f"> {_line(row)}" for row in str(complaint.get("body", "")).splitlines() if row.strip()],
        "",
        "## 접수 요약",
        f"- {_line(intake.get('summary', ''))}",
        "- 확인된 조건: " + (", ".join(f"{key} = {value:g}" for key, value in (intake.get("conditions") or {}).items() if isinstance(value, (int, float))) or "없음"),
        "- 추가로 받을 정보:",
        *["  " + row for row in _bullets(intake.get("missing_info") or [])],
    ]
    hypotheses = diagnosis.get("hypotheses") or []
    if hypotheses:
        lines += ["", "## 원인 가설 (가능성 높은 순)"]
        for rank, hypothesis in zip(ORDINALS, hypotheses, strict=False):
            sources = ", ".join(e.get("source", "") for e in hypothesis.get("evidence") or []) or "근거 없음"
            lines += [f"- **{rank}** {_line(hypothesis.get('cause', ''))}", f"  - 설명: {_line(hypothesis.get('mechanism', ''))}", f"  - 근거: {sources}"]
    solution = diagnosis.get("solution") or {}
    if solution:
        lines += ["", "## 권장 조치", f"- {_line(solution.get('summary', ''))}"]
        lines += [f"- {_line(c.get('target', ''))}: {_line(c.get('before', ''))} → {_line(c.get('after', ''))}" for c in solution.get("changes") or []]
        if solution.get("assumptions"):
            lines += ["- 계산에 쓴 가정:", *["  " + row for row in _bullets(solution["assumptions"])]]
    design = state.get("design_check") or []
    if design:
        lines += ["", "## 설계 점검 (코드 계산)", *[f"- [{f.get('kind', '')} / {f.get('status', '')}] {_line(f.get('text', ''))}" for f in design]]
    steps = (state.get("check_report") or {}).get("steps") or []
    if steps:
        lines += ["", "## 계산·시뮬레이션 확인", *[f"- {_line(s.get('label', ''))}: {s.get('status', '')} ({_line(s.get('notes', ''))})" for s in steps]]
    rating = state.get("rating_check") or {}
    if rating:
        lines += ["", "## 정격 점검 (고객 값 대 데이터시트 한계)", *_bullets([*rating.get("abs_over", []), *rating.get("rec_over", [])])]
    cpr = state.get("cpr") or {}
    if cpr:
        lines += ["", "## 반품(CPR) 점검과 8D 초안", *[f"- {key}: {'있음' if ok else '없음'}" for key, ok in (cpr.get("checklist") or {}).items()]]
        lines += ["- 빠진 자료:", *["  " + row for row in _bullets(cpr.get("missing") or [])]]
        lines += [f"- {key}: {_line(value)}" for key, value in (cpr.get("eight_d_draft") or {}).items()]
    if qa:
        lines += ["", "## 검수", f"- 결과: {'통과' if qa.get('passed') else '미통과'} (재분석 {state.get('qa_retries', 0)}회)"]
        lines += [f"- 지적: {_line(item)}" for item in qa.get("issues") or []] + [f"- 경고: {_line(item)}" for item in qa.get("warnings") or []]
    lines += ["", "## 내부 메모", *[f"- {_line(row)}" for row in str(response.get("internal_note", "")).splitlines() if row.strip()], ""]
    return "\n".join(lines)


def ticket_summary_json(state: dict[str, Any], generated_at: str) -> str:
    complaint, intake = state.get("complaint") or {}, state.get("intake") or {}
    hypotheses = (state.get("diagnosis") or {}).get("hypotheses") or []
    review, qa = state.get("review") or {}, state.get("qa") or {}
    summary = {
        "ticket_id": state.get("ticket_id", ""),
        "received_at": complaint.get("received_at", ""),
        "customer": complaint.get("customer", ""),
        "subject": _line(complaint.get("subject", "")),
        "category": intake.get("category", ""),
        "route": intake.get("route", ""),
        "part_numbers": intake.get("part_numbers") or [],
        "urgency": intake.get("urgency", ""),
        "summary": _line(intake.get("summary", "")),
        "top_cause": _line(hypotheses[0].get("cause", "")) if hypotheses else "",
        "qa_passed": qa.get("passed"),
        "decision": review.get("action", ""),
        "review_comment": _line(review.get("comment", "")),
        "email_status": DRAFT_HEADER,
        "generated_at": generated_at,
    }
    return json.dumps(summary, ensure_ascii=False, indent=2) + "\n"


def build_documents(state: dict[str, Any], generated_at: str) -> dict[str, bytes]:
    """파일 이름 → 내용. 상태(승인된 내용)만 쓴다."""
    texts = {
        "customer_email.md": customer_email_md(state),
        "internal_report.md": internal_report_md(state, generated_at),
        "ticket_summary.json": ticket_summary_json(state, generated_at),
    }
    files = {name: text.encode("utf-8") for name, text in texts.items()}
    files["customer_email.eml"] = customer_email_eml(state)
    return files


def check_documents(state: dict[str, Any], files: dict[str, bytes], generated_at: str) -> list[str]:
    """고객 메일은 승인된 답변·제목만, 보고서·요약은 승인된 상태 전체를 근거로 검사한다."""
    complaint, response = state.get("complaint") or {}, state.get("response") or {}
    allowed = f"{state.get('ticket_id', '')} {generated_at} {complaint.get('customer', '')}"
    email_source = f"{response.get('customer_reply', '')}\n{complaint.get('subject', '')}"
    state_source = json.dumps(state, ensure_ascii=False, default=str)
    issues: list[str] = []
    for name, content in files.items():
        if name == "customer_email.eml":
            continue  # 본문은 customer_email.md와 같은 승인된 답변이고, 머리말은 고정 문구뿐이다
        source = email_source if name == "customer_email.md" else state_source
        issues += [f"{name}: {issue}" for issue in check_document(content.decode("utf-8"), source, allowed)]
    return issues


def run_documents(state: dict[str, Any]) -> dict[str, Any]:
    """승인된 상태로 문서를 만들어 저장한다. 결과(documents)는 상태에 담긴다. 실패해도 그래프를 멈추지 않는다."""
    settings = get_settings()
    if not settings.generate_documents:
        return {"status": "skipped", "files": [], "issues": []}
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        files = build_documents(state, generated_at)
        issues = check_documents(state, files, generated_at)
        if issues:
            return {"status": "blocked", "files": [], "issues": issues}
        ticket = str(state.get("ticket_id") or "no-ticket")
        folder = settings.outbox_dir / ticket
        folder.mkdir(parents=True, exist_ok=True)
        review = state.get("review") or {}
        manifest = {
            "ticket_id": ticket,
            "generated_at": generated_at,
            "status": DRAFT_HEADER,
            "approval": {"action": review.get("action", ""), "comment": review.get("comment", "")},
            "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
        }
        for name, content in files.items():
            (folder / name).write_bytes(content)
        (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"status": "generated", "dir": f"outbox/{ticket}", "files": [*files, "manifest.json"], "issues": [], "generated_at": generated_at}
    except Exception as exc:  # noqa: BLE001 - 문서 저장 실패가 승인된 처리를 되돌리면 안 된다
        return {"status": "error", "files": [], "issues": [f"{type(exc).__name__}: {exc}"[:300]]}

