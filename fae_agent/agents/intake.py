"""접수·분류 에이전트: 부품 번호·증상·동작 조건을 뽑고 처리 경로를 정합니다.

경로는 TI의 실제 분류를 따릅니다: 배송·라벨 → 고객지원, 기술·디버깅 → FAE,
사양 불일치(불량) 의심 → 반품 접수(CPR).
"""

from __future__ import annotations

import re

from ..llm import is_llm_mode, structured_call
from ..schemas import Complaint, Conditions, IntakeResult
from .prompts import INTAKE_SYSTEM

ROUTES = {"technical": "fae", "quality_claim": "cpr", "logistics": "customer_support", "other": "fae"}

# 가상 샘플 부품(EXAMPLE-*)과 TI에서 흔한 제품군 접두어. 숫자가 반드시 들어가야 부품 번호로 봅니다.
PART_RE = re.compile(
    r"(?<![A-Za-z0-9])(EXAMPLE-[A-Z0-9]+|(?:TPSM|TPS|LMR|LMQ|LM|UCC|OPA|INA|TLV|TL|ADS|DAC|DRV|BQ|MSPM0|"
    r"MSP430|CC|TMS320|TCAN|ISO|SN65|SN74|TXS|TXB|TPD|LP|REF|TMP|HDC|OPT|DP83)[A-Z]*\d[A-Z0-9\-]*)"
    r"(?![A-Za-z0-9\-])",  # 끝에 한국어 조사가 붙어도 인식 (예: TPS54331을)
    re.IGNORECASE,
)

SYMPTOM_KEYWORDS: dict[str, list[str]] = {
    "oscillation": ["발진", "링잉", "진동", "불안정", "ringing", "oscillat", "unstable"],
    "ripple_noise": ["리플", "노이즈", "잡음", "스파이크", "ripple", "noise", "spike"],
    "overheating": ["발열", "뜨거", "뜨겁", "과열", "overheat", "too hot", "thermal"],
    "wrong_output": [
        "출력 전압이",
        "출력이 낮",
        "출력이 높",
        "전압이 낮",
        "전압이 높",
        "wrong output",
        "output is low",
    ],
    "startup_fail": ["안 켜", "켜지지", "기동", "시작하지", "not start", "startup", "start-up", "no output"],
    "communication": ["i2c", "spi", "uart", "통신", "nack"],
    "damage": ["타버", "탔", "파손", "쇼트", "연기", "burn", "damaged", "short"],
}
QUALITY_WORDS = ["불량", "반품", "rma", "불량률", "a-b-a", "교차 검증", "8d", "fa 요청", "defect", "return"]
LOGISTICS_WORDS = ["배송", "납기", "라벨", "수량", "주문", "견적", "shipping", "delivery", "label", "invoice"]
URGENT_WORDS = ["긴급", "라인 정지", "양산 중단", "urgent", "asap", "line down"]

_NUM = r"(\d+(?:\.\d+)?)"
_P = r"(?:은|는|이|가|을|를|:|=)?"  # 한국어 조사나 : = 기호
# (조건 이름, 정규식, 단위 배율) — 문장은 소문자로 바꾼 뒤 검사합니다 (Ω → ω)
CONDITION_PATTERNS: list[tuple[str, str, float]] = [
    ("vin_v", rf"(?:\bvin|입력(?:\s*전압)?)\s*{_P}\s*{_NUM}\s*v(?![a-z])", 1.0),
    ("vout_v", rf"(?:\bvout|목표|출력(?:\s*전압)?)\s*{_P}\s*{_NUM}\s*v(?![a-z])", 1.0),
    ("vout_meas_v", rf"측정(?:값)?\s*{_P}\s*{_NUM}\s*v(?![a-z])", 1.0),
    ("iout_a", rf"(?:\biout|\bload|부하(?:\s*전류)?|출력\s*전류)\s*{_P}\s*{_NUM}\s*a(?![a-z])", 1.0),
    ("ta_c", rf"(?:\bta|주변\s*온도|\bambient)\s*{_P}\s*(-?\d+(?:\.\d+)?)\s*°?\s*c(?![a-z])", 1.0),
    ("fsw_hz", rf"(?:\bfsw|스위칭\s*주파수)\s*{_P}\s*{_NUM}\s*khz", 1e3),
    ("cout_f", rf"(?:\bcout|출력\s*커패시터|출력\s*캡)\s*{_P}\s*{_NUM}\s*[uµμ]f", 1e-6),
    ("esr_ohm", rf"\besr\s*{_P}\s*{_NUM}\s*m(?:ohm|ω)", 1e-3),
    ("l_h", rf"(?:인덕터|\binductor)\s*{_P}\s*{_NUM}\s*[uµμ]h", 1e-6),
    ("vdd_v", rf"\bvdd\s*{_P}\s*{_NUM}\s*v(?![a-z])", 1.0),
    ("pullup_ohm", rf"(?:풀업|\bpull-?up)(?:\s*저항)?\s*{_P}\s*{_NUM}\s*k", 1e3),
    ("cb_f", rf"(?:버스\s*커패시턴스|\bbus\s*capacitance|\bcb)\s*{_P}\s*(?:약\s*)?{_NUM}\s*pf", 1e-12),
    ("r_top_ohm", rf"상단\s*{_P}\s*{_NUM}\s*k", 1e3),
    ("r_bottom_ohm", rf"하단\s*{_P}\s*{_NUM}\s*k", 1e3),
]
NOT_PARTS = {"ISO9001", "ISO14001", "ISO26262", "ISO16750"}


def extract_parts(text: str) -> list[str]:
    found: list[str] = []
    for match in PART_RE.findall(text):
        part = match.upper()
        if part not in found and part not in NOT_PARTS:
            found.append(part)
    return found


def extract_conditions(text: str) -> Conditions:
    values: dict[str, float] = {}
    lowered = text.lower()
    for name, pattern, scale in CONDITION_PATTERNS:
        match = re.search(pattern, lowered)
        if match:
            values[name] = float(match.group(1)) * scale
    return Conditions(**values)


def _has_any(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)


def classify_rules(complaint: Complaint) -> IntakeResult:
    """규칙 기반 분류 (mock 모드)."""
    text = complaint.text
    lowered = text.lower()
    parts = extract_parts(text)
    symptoms = [code for code, words in SYMPTOM_KEYWORDS.items() if _has_any(lowered, words)]
    conditions = extract_conditions(text)

    if _has_any(lowered, QUALITY_WORDS) or "damage" in symptoms:
        category = "quality_claim"
    elif _has_any(lowered, LOGISTICS_WORDS) and not symptoms:
        category = "logistics"
    elif symptoms:
        category = "technical"
    else:
        category = "other"

    missing: list[str] = []
    if category in ("technical", "quality_claim") and not parts:
        missing.append("부품 번호(전체 주문 번호)")
    if category == "technical":
        known = conditions.known()
        if "vin_v" not in known and "vdd_v" not in known:
            missing.append("입력(전원) 전압")
        if "iout_a" not in known and "communication" not in symptoms:
            missing.append("부하 전류")
        if not complaint.attachments and not _has_any(lowered, ["회로도", "schematic"]):
            missing.append("회로도")
        if {"oscillation", "ripple_noise"} & set(symptoms):
            missing.append("측정 파형(오실로스코프 캡처)")

    urgency = "high" if _has_any(lowered, URGENT_WORDS) else "medium"
    summary = f"[{category}] 부품: {', '.join(parts) or '미상'} / 증상: {', '.join(symptoms) or '미상'}"
    return IntakeResult(
        category=category,
        route=ROUTES[category],
        part_numbers=parts,
        symptoms=symptoms,
        conditions=conditions,
        urgency=urgency,
        missing_info=missing,
        summary=summary,
    )


def run_intake(complaint: Complaint) -> IntakeResult:
    if not is_llm_mode():
        return classify_rules(complaint)
    result = structured_call(IntakeResult, INTAKE_SYSTEM, complaint.text)
    # 경로는 분류에서 결정적으로 정하고, 부품 번호는 대문자로 맞춤
    result.route = ROUTES[result.category]
    result.part_numbers = [p.upper() for p in result.part_numbers]
    return result
