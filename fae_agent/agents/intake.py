"""접수·분류 에이전트: 부품 번호·증상·동작 조건을 뽑고 처리 경로를 정합니다.

경로는 TI의 실제 분류를 따릅니다: 배송·라벨 → 고객지원, 기술·디버깅 → FAE,
사양 불일치(불량) 의심 → 반품 접수(CPR).
"""

from __future__ import annotations

import re

from ..llm import is_llm_mode, structured_call
from ..schemas import Complaint, Conditions, IntakeResult, SalesRequest
from .prompts import INTAKE_SYSTEM

ROUTES = {
    "technical": "fae", "quality_claim": "cpr", "logistics": "customer_support", "quote_sample": "sales", "design_request": "sales", "other": "fae",
}  # fmt: skip

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
LOGISTICS_WORDS = ["배송", "납기", "라벨", "수량", "주문", "shipping", "delivery", "label", "invoice"]
QUOTE_WORDS = ["견적", "샘플", "단가", "구매하고", "quote", "sample", "quotation"]
# 신규 설계 요구사항·부품 추천·대체품 상담 (증상이 없고 요구사항이 있는 문의)
DESIGN_WORDS = ["어떤 부품", "어느 부품", "무슨 부품", "좋을까요", "만들고 싶", "추천", "대체품", "대체할", "후보", "찾고 있", "찾고있", "선정", "신규 설계", "새로 설계", "새 설계", "설계 중", "요구사항", "recommend", "alternative"]
URGENT_WORDS = ["긴급", "라인 정지", "양산 중단", "urgent", "asap", "line down"]

_NUM = r"(\d+(?:\.\d+)?)"
_P = r"(?:은|는|이|가|을|를|:|=)?"  # 한국어 조사나 : = 기호
# (조건 이름, 정규식, 단위 배율) — 문장은 소문자로 바꾼 뒤 검사합니다 (Ω → ω)
CONDITION_PATTERNS: list[tuple[str, str, float]] = [
    ("vin_v", rf"(?:\bvin|입력(?:\s*전압)?)\s*{_P}\s*{_NUM}\s*v(?![a-z])", 1.0),
    ("vout_v", rf"(?:\bvout|목표|출력(?:\s*전압)?)\s*{_P}\s*{_NUM}\s*v(?![a-z])", 1.0),
    ("vout_meas_v", rf"측정(?:값)?\s*{_P}\s*{_NUM}\s*v(?![a-z])", 1.0),
    # 잰 리플만 읽는다: 규격·목표 값("리플 규격 10mV 이하")은 건너뛴다
    ("ripple_meas_v", rf"(?:리플|\bripple)(?:(?!규격|목표|요구|\bspec)[^0-9\n.]){{0,24}}?{_NUM}\s*mv(?!\s*(?:이하|이내|미만|이상))", 1e-3),
    ("iout_a", rf"(?:\biout|\bload|부하(?:\s*전류)?|출력\s*전류)\s*{_P}\s*{_NUM}\s*a(?![a-z])", 1.0),
    ("ta_c", rf"(?:\bta|주변\s*온도|\bambient)\s*{_P}\s*(-?\d+(?:\.\d+)?)\s*(?:°?\s*c(?![a-z])|도)", 1.0),
    ("fsw_hz", rf"(?:\bfsw|스위칭\s*주파수)\s*{_P}\s*{_NUM}\s*khz", 1e3),
    ("cout_f", rf"(?:\bcout|출력\s*커패시터|출력\s*캡)\s*{_P}\s*{_NUM}\s*[uµμ]f", 1e-6),
    ("esr_ohm", rf"\besr\s*{_P}\s*{_NUM}\s*m(?:ohm|ω)", 1e-3),
    ("l_h", rf"(?:인덕터|\binductor)\s*{_P}\s*{_NUM}\s*[uµμ]h", 1e-6),
    ("vdd_v", rf"(?:\bvdd|전원(?:\s*전압)?)\s*{_P}\s*{_NUM}\s*v(?![a-z])", 1.0),
    ("pullup_ohm", rf"(?:풀업|\bpull-?up)(?:\s*저항)?\s*{_P}\s*{_NUM}\s*k", 1e3),
    ("cb_f", rf"(?:버스\s*커패시턴스|\bbus\s*capacitance|\bcb)\s*{_P}\s*(?:약\s*)?{_NUM}\s*pf", 1e-12),
    ("css_f", rf"(?:\bss\s*핀|\bss\s*커패시터|소프트\s*스타트\s*커패시터|\bcss)(?:[^0-9\n.]){{0,12}}?{_NUM}\s*nf", 1e-9),
    ("css_f", rf"(?:\bss\s*핀|\bss\s*커패시터|소프트\s*스타트\s*커패시터|\bcss)(?:[^0-9\n.]){{0,12}}?{_NUM}\s*[uµμ]f", 1e-6),
    ("rf_ohm", rf"(?:피드백\s*저항|\brf)\s*{_P}\s*{_NUM}\s*k", 1e3),
    ("rg_ohm", rf"(?:접지\s*쪽\s*저항|\brg)\s*{_P}\s*{_NUM}\s*k", 1e3),
    ("tcase_meas_c", rf"(?:칩\s*표면|케이스\s*온도|표면\s*온도)[^0-9\n.]{{0,16}}?{_NUM}\s*(?:도|°\s*c)", 1.0),
    ("vin_min_v", rf"(?:최저|최소)\s*(?:입력\s*(?:전압)?\s*)?{_P}\s*{_NUM}\s*v(?![a-z])", 1.0),
    ("vcc_v", rf"\bvcc\s*핀(?:에서|에는|에)?(?:[^0-9\n.]){{0,8}}?{_NUM}\s*v(?![a-z])", 1.0),
    ("vcc_v", rf"\bvcc\s*{_P}\s*{_NUM}\s*v(?![a-z])", 1.0),
    ("rilim_ohm", rf"(?:\bilim\s*(?:핀|저항)?|전류\s*제한\s*저항)(?:[^0-9\n.]){{0,14}}?{_NUM}\s*k(?![a-z])", 1e3),
    ("rilim_ohm", rf"(?:\bilim\s*(?:핀|저항)?|전류\s*제한\s*저항)(?:[^0-9\n.]){{0,14}}?{_NUM}\s*(?:ω|ohm|옴)", 1.0),
    ("rds_low_ohm", rf"하단\s*(?:fet|mosfet)[^0-9\n.]{{0,16}}?{_NUM}\s*m(?:ω|ohm|옴)", 1e-3),
    ("iinlim_a", rf"iinlim[^0-9\n.]{{0,10}}?{_NUM}\s*a(?![a-z])", 1.0),
    ("ichg_set_a", rf"(?:\bichg|충전\s*전류\s*\(ichg\))[^0-9\n.]{{0,10}}?{_NUM}\s*a(?![a-z])", 1.0),
    ("vbat_v", rf"(?:배터리\s*전압|\bvbat)[^0-9\n.]{{0,10}}?{_NUM}\s*v(?![a-z])", 1.0),
    ("isys_a", rf"시스템\s*부하[^0-9\n.]{{0,10}}?{_NUM}\s*a(?![a-z])", 1.0),
    ("rt1_ohm", rf"(?:regn\s*(?:과|와)?\s*ts\s*사이[^0-9\n.]{{0,6}}?|\brt1\s*{_P}\s*){_NUM}\s*k(?![a-z])", 1e3),
    ("ntc_r25_ohm", rf"{_NUM}\s*k\s*(?:ω|ohm|옴)?\s*ntc", 1e3),
    ("r_top_ohm", rf"상단\s*{_P}\s*{_NUM}\s*k", 1e3),
    ("r_bottom_ohm", rf"하단\s*{_P}\s*{_NUM}\s*k", 1e3),
]
NOT_PARTS = {"ISO9001", "ISO14001", "ISO26262", "ISO16750"}
# 0이면 조건이 아니라 증상 표현인 값 ("입력을 0V로 접지", "출력이 0V로 떨어짐"): 0은 건너뛰고 다음 일치를 쓴다
POSITIVE_ONLY = {
    "vin_v", "vout_v", "vdd_v", "fsw_hz", "cout_f", "l_h", "css_f", "rf_ohm", "rg_ohm", "vin_min_v", "vcc_v", "rilim_ohm",
    "rds_low_ohm", "vbat_v", "rt1_ohm", "ntc_r25_ohm",
}  # fmt: skip


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
        if name in values:  # css_f처럼 단위별 패턴이 여럿이면 먼저 맞은 것을 쓴다
            continue
        for match in re.finditer(pattern, lowered):
            number = float(match.group(1))
            if number > 0 or name not in POSITIVE_ONLY:
                values[name] = number * scale
                break
    ranged = _RANGE.search(lowered)
    if ranged and float(ranged.group(1)) < float(ranged.group(2)):  # "입력 9~15V": 최저·최고, vin_v에는 최고값(정격 비교는 최악 조건)
        values["vin_min_v"], values["vin_max_v"] = float(ranged.group(1)), float(ranged.group(2))
        values["vin_v"] = values["vin_max_v"]
    return Conditions(**values)


_PLAIN_NUM = r"(\d+(?:\.\d+)?)"
_RANGE = re.compile(rf"입력(?:\s*전압)?\s*(?:은|는|이|가|:|=)?\s*{_PLAIN_NUM}\s*v?\s*(?:~|∼|-|에서)\s*{_PLAIN_NUM}\s*v")


_ENDING = re.compile(r"\s*(?:입니다만|입니다|이에요|예요|이고요|이고|이며|이라서|이라)\s*[.!]?$")


def _trim_ending(value: str) -> str:
    """'…충전입니다' 같은 서술 어미를 뗀다."""
    return _ENDING.sub("", value).strip()


_V_TO_V = re.compile(rf"{_PLAIN_NUM}\s*v\s*(?:에서|→|->)\s*{_PLAIN_NUM}\s*v(?![a-z])")
_V_INPUT = re.compile(rf"{_PLAIN_NUM}\s*v\s*입력")
_AMPS = re.compile(rf"(?<![a-z0-9.]){_PLAIN_NUM}\s*a(?![a-z])")


def design_conditions(text: str, conditions: Conditions) -> Conditions:
    """설계 상담의 말투("USB 5V에서 3.3V 0.3A", "5V 입력", "입력 12V에서 9V, 1A")에서 비어 있는 입력·출력·전류를 채운다. 이미 읽은 값은 건드리지 않는다."""
    lowered = text.lower()
    values = conditions.model_dump(exclude_none=True)
    pair = _V_TO_V.search(lowered)
    if pair:
        values.setdefault("vin_v", float(pair.group(1)))
        values.setdefault("vout_v", float(pair.group(2)))
    elif "vin_v" not in values and (single := _V_INPUT.search(lowered)):
        values["vin_v"] = float(single.group(1))
    if "iout_a" not in values and (amps := _AMPS.search(lowered)):
        values["iout_a"] = float(amps.group(1))
    return Conditions(**values)


def extract_request(text: str, quote: bool, design: bool) -> SalesRequest:
    """견적·샘플·설계 상담에서 고객이 쓴 수량·시점·용도·단계를 뽑는다 (없는 것은 비워 둔다)."""
    lowered = text.lower()
    kind = "sample" if ("샘플" in lowered or "sample" in lowered) else "quote" if quote else "design" if design else ""
    quantity = re.search(r"(\d[\d,]*)\s*(?:개|ea|pcs|릴|reel|장)", lowered)
    need_by = re.search(
        r"(\d+\s*주(?:\s*(?:안|내|이내|까지))?|\d+\s*월(?:\s*(?:초|중|말))?(?:\s*까지)?|\d+\s*일\s*(?:안|내|이내|까지)"
        r"|(?:다음|이번|내)\s*(?:달|주|분기)(?:\s*(?:초|중|말))?(?:\s*까지)?|(?:올해|내년)\s*(?:초|말)(?:\s*까지)?|연말(?:\s*까지)?|연초(?:\s*까지)?)",
        text,
    )
    application = re.search(r"(?:용도는?|용도\s*:|사용처는?|적용처는?|(?:적용|사용)\s*제품은?|용으로|에 사용|에 쓸|에 쓰는)\s*([^.,\n]{0,30})", text)
    app_before = re.search(r"([^.,\n]{2,25}?)\s*(?:용으로|에 사용|에 쓸|에 쓰는|에 쓰려)", text)
    stage = "prototype" if re.search(r"양산\s*(?:전|이전|직전|앞)", lowered) else "production" if re.search(r"양산|mass production", lowered) else "prototype" if re.search(r"시제품|프로토|평가용|평가 보드|prototype", lowered) else ""
    notes = [word for word in ("저잡음", "저노이즈", "고효율", "효율", "소형", "저전력", "low noise") if word in lowered]
    return SalesRequest(
        kind=kind,
        quantity=int(quantity.group(1).replace(",", "")) if quantity else None,
        need_by=need_by.group(1).strip() if need_by else "",
        application=_trim_ending(application.group(1).strip() if application else (app_before.group(1).strip() if app_before else "")),
        stage=stage,
        notes=", ".join(notes),
    )


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
    elif not symptoms and _has_any(lowered, QUOTE_WORDS):
        category = "quote_sample"
    elif set(symptoms) <= {"ripple_noise"} and _has_any(lowered, DESIGN_WORDS):  # '저잡음' 같은 요구는 증상이 아니다
        category = "design_request"
    elif _has_any(lowered, LOGISTICS_WORDS) and not symptoms:
        category = "logistics"
    elif symptoms:
        category = "technical"
    else:
        category = "other"

    if category == "design_request":
        conditions = design_conditions(text, conditions)

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
        request=extract_request(text, category == "quote_sample", category == "design_request")
        if category in ("quote_sample", "design_request")
        else SalesRequest(),
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
