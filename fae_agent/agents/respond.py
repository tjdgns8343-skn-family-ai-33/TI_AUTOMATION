"""응답 에이전트: 경로(기술지원·품질 클레임·고객지원)에 맞는 고객 답변 초안과 내부 메모를 씁니다."""

from __future__ import annotations

import json
from typing import Any

from ..llm import is_llm_mode, structured_call
from ..schemas import CheckReport, Complaint, CPRCheck, Diagnosis, IntakeResult, QAReview, ResponseDraft
from ..waveform import strip_waveforms
from .prompts import RESPOND_SYSTEM

STATUS_KO = {"pass": "통과", "fail": "미통과", "skipped": "생략", "error": "오류"}


def _technical_reply(
    complaint: Complaint,
    intake: IntakeResult,
    diagnosis: Diagnosis,
    report: CheckReport,
    research: dict[str, Any],
    design: list[dict[str, Any]] | None = None,
) -> str:
    parts = ", ".join(intake.part_numbers) or "문의 부품"
    lines = [f"{complaint.customer}님, 안녕하세요.", f"문의하신 {parts} 건 검토 결과를 안내드립니다.", ""]
    lines.append("■ 추정 원인 (가능성 높은 순)")
    for i, h in enumerate(diagnosis.hypotheses, start=1):
        sources = ", ".join(e.source for e in h.evidence)
        lines.append(f"{i}. {h.cause}")
        if h.mechanism:
            lines.append(f"   - {h.mechanism}")
        if sources:
            lines.append(f"   - 근거: {sources}")
    if diagnosis.solution:
        lines += ["", "■ 권장 조치", f"- {diagnosis.solution.summary}"]
        lines += [f"- {c.target}: {c.before} → {c.after}" for c in diagnosis.solution.changes]
    notable = [f for f in design or [] if f["status"] not in ("ok", "skipped")]
    if notable:
        lines += ["", "■ 설계 점검 (코드 계산)"] + [f"- {f['text']}" for f in notable]
    if report.steps:
        lines += ["", "■ 계산·시뮬레이션 확인"]
        lines += [f"- {s.label}: {STATUS_KO[s.status]} ({s.notes})" for s in report.steps]
    if diagnosis.solution and diagnosis.solution.assumptions:
        lines += ["", "■ 계산에 쓴 가정"] + [f"- {a}" for a in diagnosis.solution.assumptions]
    cases = research.get("cases") or []
    if cases:
        lines += ["", "■ 참고한 비슷한 사례"] + [f"- {c['title']}" for c in cases[:2]]
    if intake.missing_info:
        lines += ["", "■ 정확한 확인을 위해 아래 자료를 보내 주세요"] + [f"- {m}" for m in intake.missing_info]
    lines += ["", "감사합니다."]
    return "\n".join(lines)


def _cpr_reply(complaint: Complaint, cpr: CPRCheck, rating: dict[str, Any] | None = None) -> str:
    lines = [
        f"{complaint.customer}님, 안녕하세요. 사용 중 문제가 생겨 불편을 드려 죄송합니다.",
        "품질 문제로 접수하여 불량 분석(FA)을 진행하려고 합니다.",
    ]
    if rating and rating.get("abs_over"):
        lines += ["", "먼저 확인된 점이 있습니다 (알려 주신 값 기준)."]
        lines += [f"- {text}." for text in rating["abs_over"]]
        source = (rating.get("source_text") or "").split(":")[0].strip()
        if source and not source.startswith("예시"):  # 가상 부품(EXAMPLE-*)의 안내 문구는 출처로 쓰지 않는다
            lines.append(f"  (출처: {source})")
        lines += [
            "  데이터시트 기준으로는 부품 불량이 아니라 사용 조건에 의한 손상(과전기 스트레스)일 가능성이 있습니다.",
            "  반품 접수 전에 실제 값(시동 시 서지 포함)을 측정해 확인해 주세요.",
        ]
    if cpr.missing:
        lines += ["", "정확한 분석을 위해 아래 자료를 보내 주세요."] + [f"- {m}" for m in cpr.missing]
    lines += [
        "",
        "자료가 준비되면 반품(CPR) 접수를 진행하고, 분석 결과는 8D 보고서로 회신드리겠습니다.",
        "",
        "감사합니다.",
    ]
    return "\n".join(lines)


def _simple_reply(complaint: Complaint, route: str) -> str:
    if route == "customer_support":
        body = "배송·주문 관련 문의는 고객지원팀에서 확인한 뒤 회신드리겠습니다."
    else:
        body = "정확한 검토를 위해 부품 번호, 동작 조건(입력·출력·부하), 회로도, 증상 설명을 보내 주세요."
    return f"{complaint.customer}님, 안녕하세요.\n{body}\n\n감사합니다."


def _internal_note(
    intake: IntakeResult,
    qa: QAReview | None,
    retries: int,
    revise_comment: str,
    rating: dict[str, Any] | None = None,
    design: list[dict[str, Any]] | None = None,
) -> str:
    lines = [f"경로: {intake.route} / 긴급도: {intake.urgency}"]
    if rating:  # 품질 클레임 경로의 정격 점검 결과 (고객이 알려 준 값 기준)
        if not rating.get("found"):
            lines.append("- 경고: 정격 점검을 하지 못했습니다 (부품 카드 없음). 사람이 데이터시트로 확인해야 합니다.")
        lines += [f"- 정격 점검: {text} (절대 최대 초과, 사용 조건 문제 가능성이 높아 불량 클레임 신뢰도 낮음)" for text in rating.get("abs_over", [])]
        lines += [f"- 정격 점검: {text} (권장 범위 밖)" for text in rating.get("rec_over", [])]
    lines += [f"- 설계 점검 [{f['status']}]: {f['text']}" for f in design or [] if f["status"] != "ok"]
    if qa:
        lines.append(f"검수: {'통과' if qa.passed else '미통과'} (재분석 {retries}회)")
        lines += [f"- 지적: {i}" for i in qa.issues]
        lines += [f"- 경고: {w}" for w in qa.warnings]
    if revise_comment:
        lines.append(f"검토자 수정 요청 반영: {revise_comment}")
    return "\n".join(lines)


def run_respond(state: dict[str, Any]) -> ResponseDraft:
    complaint = Complaint.model_validate(state["complaint"])
    intake = IntakeResult.model_validate(state["intake"])
    diagnosis = Diagnosis.model_validate(state["diagnosis"]) if state.get("diagnosis") else None
    report = (
        CheckReport.model_validate(state["check_report"])
        if state.get("check_report")
        else CheckReport(status="skipped")
    )
    qa = QAReview.model_validate(state["qa"]) if state.get("qa") else None
    cpr = CPRCheck.model_validate(state["cpr"]) if state.get("cpr") else None
    review = state.get("review") or {}
    revise_comment = review.get("comment", "") if review.get("action") == "revise" else ""
    rating = state.get("rating_check")
    design = state.get("design_check")
    note = _internal_note(intake, qa, state.get("qa_retries", 0), revise_comment, rating, design)

    if is_llm_mode():
        keys = ("design_check", "intake", "research", "diagnosis", "check_report", "qa", "cpr", "rating_check")
        payload = {key: state.get(key) for key in keys}
        payload = strip_waveforms(payload)  # 시뮬레이션 파형은 숫자가 많아 프롬프트(12000자 제한)를 채우므로 뺀다
        user = (
            f"# 고객 문의\n{complaint.text}\n\n# 분석 결과(JSON)\n{json.dumps(payload, ensure_ascii=False, default=str)[:12000]}"
            f"\n\n# 검토자 수정 요청\n{revise_comment or '없음'}"
        )
        draft = structured_call(ResponseDraft, RESPOND_SYSTEM, user)
        draft.internal_note = f"{note}\n{draft.internal_note}".strip()
        return draft

    if intake.route == "fae" and diagnosis and intake.category == "technical":
        reply = _technical_reply(complaint, intake, diagnosis, report, state.get("research") or {}, design)
    elif intake.route == "cpr" and cpr:
        reply = _cpr_reply(complaint, cpr, rating)
    else:
        reply = _simple_reply(complaint, intake.route)
    if revise_comment:  # mock 모드는 문장을 다시 쓰지 못하므로 메모로만 남김
        note += "\n(mock 모드: 초안은 자동으로 고쳐지지 않습니다. 직접 편집하거나 FAE_MODE=llm을 쓰세요.)"
    return ResponseDraft(customer_reply=reply, internal_note=note)
