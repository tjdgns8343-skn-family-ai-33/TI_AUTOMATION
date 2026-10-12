"""영업 문의(견적·샘플, 설계 상담) 답변 초안 검사: 코드가 계산한 결과를 LLM이 답변에서 빼먹거나 어기지 않았는지 본다.

2026-10-12 측정(docs/sales_measurement_2026-10-12.md)에서 LLM 답변이 탈락한 후보를 빼먹고, "통과 없음"을 말하지 않고,
정보가 충분한데 불필요한 질문을 붙이고, 자리표시(`[source]`)를 새게 했다. 프롬프트로만 막기 어려워 코드가 마지막에 검사한다.
어기면 호출하는 쪽(respond)이 코드가 만든 템플릿 답변으로 바꾼다 (토큰 0, 재시도 없음).

규칙(정규식·키워드)이라 문장의 어색함이나 어조는 보지 못한다. 키워드가 안내 문구에도 나올 수 있어 "빠진 항목을 되물었나"는 관대한 편이다.
"""

from __future__ import annotations

import re
from typing import Any

# 코드(sales.CHECKLIST)의 항목 키 → 답변에서 그 항목을 되물었다고 볼 수 있는 말
ASK_WORDS = {
    "부품 번호": ("부품 번호", "품번", "주문 번호", "파트 넘버"),
    "필요 수량": ("수량",),
    "사용 용도": ("용도", "적용 제품", "사용 제품"),
    "필요한 시점": ("시점", "희망일", "납기", "일정"),
    "시제품": ("시제품", "양산", "평가"),
    "출력 전압": ("출력 전압", "출력전압"),
    "부하 전류": ("부하 전류", "출력 전류", "전류"),
}
# 체크리스트에 없는 정보를 더 달라는 말 (정보가 충분한데 붙이면 고객만 번거롭다)
EXTRA_ASK_WORDS = (
    "회사명", "배송지", "우편번호", "연락처", "전화번호", "이메일 주소", "사업자", "담당자 성함", "담당자명",
    "구매 방식", "연간", "납품 희망", "납품처", "배송 주소",
)
_PLACEHOLDER = re.compile(r"\[(?:source|출처|citation)\]|\{\{?[a-z_]+\}?\}|\bTODO\b|\bXXX\b", re.IGNORECASE)
_PRICE_PROMISE = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s*(?:원(?!하)|달러|USD|EUR)|[$€]\s*\d|리드\s*타임\s*\d|납기\s*\d|\d\s*주\s*(?:내|안)?\s*(?:출고|납품)\s*(?:가능|보장)",
    re.IGNORECASE,
)
_NONE_WORDS = ("만족하는 부품이 없", "통과하는 부품이 없", "맞는 부품이 없", "해당하는 부품이 없", "적합한 부품이 없", "후보가 없")
_SCOPE_WORDS = ("안에서만", "전체 제품", "종 부품", "종 안", "정리된")
_PART_NUMBER = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,}[A-Z]?\d{3,}[A-Z0-9]*(?![A-Za-z0-9])")
_STAGE_AS_APPLICATION = re.compile(r"용도[^\n:：]{0,6}[:：]\s*(?:양산|시제품)(?:\s|$|[.,])")


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def _missing_label_key(label: str) -> str:
    for key in ASK_WORDS:
        if key in label or label in key:
            return key
    return ""


def _common(reply: str, complaint_text: str) -> list[str]:
    issues = []
    if _PLACEHOLDER.search(reply):
        issues.append("placeholder: 채우지 않은 자리표시가 답변에 남아 있음")
    for match in _PRICE_PROMISE.finditer(reply):
        if match.group(0) not in complaint_text:  # 고객이 쓴 표현을 되풀이한 것은 허용
            issues.append(f"price_or_lead_time: 가격·납기 표현 '{match.group(0).strip()}'")
            break
    return issues


def _check_quote(reply: str, sales: dict[str, Any]) -> list[str]:
    issues = []
    missing = sales.get("missing") or []
    not_asked = [m for m in missing if (key := _missing_label_key(m)) and not _has(reply, ASK_WORDS[key])]
    if not_asked:
        issues.append(f"missing_not_asked: 빠진 정보를 되묻지 않음: {', '.join(not_asked)}")
    quantity = re.sub(r"\D", "", str((sales.get("collected") or {}).get("필요 수량", "")))
    if quantity and quantity not in reply.replace(",", ""):
        issues.append(f"collected_dropped: 고객이 알려 준 수량 {quantity}이(가) 답변에 없음")
    extras = [w for w in EXTRA_ASK_WORDS if w in reply]
    if extras:
        issues.append(f"extra_questions: 체크리스트에 없는 정보를 요구함: {', '.join(extras[:4])}")
    if _STAGE_AS_APPLICATION.search(reply):
        issues.append("stage_as_application: 시제품/양산을 사용 용도 항목에 적음")
    return issues


def _check_design(reply: str, rec: dict[str, Any]) -> list[str]:
    issues = []
    status = rec.get("status")
    if status == "unsupported":
        if _PART_NUMBER.search(reply):
            issues.append("unsupported_with_parts: 지원하지 않는 요구인데 부품 번호를 적음")
        if "담당자" not in reply:
            issues.append("handoff_missing: 지원 범위 밖이면 담당자가 검토한다고 안내해야 함")
        if not _has(reply, ("LDO", "전원")):
            issues.append("scope_missing: 지원 범위(전원 추천만)를 밝히지 않음")
        return issues
    if status == "need_info":
        asked = [m for m in rec.get("missing") or [] if (key := _missing_label_key(m)) and not _has(reply, ASK_WORDS[key])]
        if asked:
            issues.append(f"missing_not_asked: 빠진 정보를 되묻지 않음: {', '.join(asked)}")
        if any(c["part"] in reply for c in rec.get("candidates") or []) or _PART_NUMBER.search(reply):
            issues.append("recommended_without_info: 정보가 부족한데 부품을 언급함")
        return issues
    candidates = rec.get("candidates") or []
    absent = [c["part"] for c in candidates if c["part"] not in reply]
    if absent:
        issues.append(f"candidate_missing: 비교한 부품이 답변에 없음 (탈락·주의 포함): {', '.join(absent)}")
    if candidates and not any(c["verdict"] != "fail" for c in candidates):
        if not _has(reply, _NONE_WORDS):
            issues.append("none_not_stated: 통과하는 부품이 없는데 없다고 말하지 않음")
        if "담당자" not in reply:
            issues.append("handoff_missing: 통과하는 부품이 없으면 담당자가 다른 제품군을 검토한다고 안내해야 함")
    if candidates and not _has(reply, _SCOPE_WORDS):
        issues.append("scope_missing: 비교 범위(이 시스템에 정리된 부품 안)를 밝히지 않음")
    if rec.get("assumptions") and "가정" not in reply:
        issues.append("assumption_missing: 계산에 쓴 가정을 밝히지 않음")
    return issues


def check_sales_reply(reply: str, category: str, state: dict[str, Any]) -> list[str]:
    """답변 초안의 문제 목록('코드: 설명')을 돌려준다. 없으면 빈 목록. category는 quote_sample·design_request만 본다."""
    complaint = state.get("complaint") or {}
    complaint_text = f"{complaint.get('subject', '')}\n{complaint.get('body', '')}"
    issues = _common(reply, complaint_text)
    if category == "quote_sample" and state.get("sales"):
        issues += _check_quote(reply, state["sales"])
    elif category == "design_request" and state.get("recommendation"):
        issues += _check_design(reply, state["recommendation"])
    return issues
