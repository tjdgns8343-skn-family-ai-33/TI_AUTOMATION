"""에이전트 사이를 오가는 데이터 형식 (Pydantic).

LLM이 직접 채우는 모델(IntakeResult, Diagnosis, ResponseDraft)에는 자유 형식 dict를 두지 않습니다.
일부 제공자의 구조화 출력(structured output)이 dict 필드를 거부하기 때문입니다.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Category = Literal["technical", "quality_claim", "logistics", "quote_sample", "design_request", "other"]
Route = Literal["fae", "cpr", "customer_support", "sales"]
SYMPTOM_CODES = (
    "oscillation",
    "ripple_noise",
    "overheating",
    "wrong_output",
    "startup_fail",
    "communication",
    "damage",
)


class Complaint(BaseModel):
    """고객 컴플레인 원문."""

    customer: str = "고객"
    subject: str = ""
    body: str
    attachments: list[str] = Field(default_factory=list)
    received_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def text(self) -> str:
        return f"{self.subject}\n{self.body}".strip()


class Conditions(BaseModel):
    """고객이 알려 준 동작 조건. 값은 모두 SI 단위 (V, A, °C, Hz, F, H, Ω)."""

    vin_v: float | None = None
    vout_v: float | None = Field(default=None, description="목표 출력 전압")
    vout_meas_v: float | None = Field(default=None, description="실제로 측정된 출력 전압")
    ripple_meas_v: float | None = Field(default=None, description="고객이 잰 출력 리플 p-p (V)")
    tcase_meas_c: float | None = Field(default=None, description="고객이 잰 칩 표면(케이스) 온도 (°C). 접합 온도가 아님")
    css_f: float | None = Field(default=None, description="소프트 스타트(SS 핀) 커패시터 (F)")
    rf_ohm: float | None = Field(default=None, description="비반전 증폭기의 피드백 저항 (Ω)")
    rg_ohm: float | None = Field(default=None, description="비반전 증폭기의 접지 쪽 저항 (Ω)")
    vin_max_v: float | None = Field(default=None, description="입력 전압이 올라가는 최고값 (요구사항의 입력 범위 위쪽). 범위가 있으면 vin_v에도 최고값을 쓴다")
    vin_min_v: float | None = Field(default=None, description="입력 전압이 내려가는 최저값 (예: 배터리 크랭킹 때). 공칭 입력은 vin_v")
    vcc_v: float | None = Field(default=None, description="컨트롤러 VCC(바이어스) 핀에서 잰·공급하는 전압 (V)")
    rilim_ohm: float | None = Field(default=None, description="ILIM(전류 제한 설정) 핀 저항 (Ω)")
    rds_low_ohm: float | None = Field(default=None, description="하단 FET의 RDS(on) (Ω)")
    iinlim_a: float | None = Field(default=None, description="충전기 입력 전류 제한 레지스터(IINLIM)에 쓴 값 (A)")
    ichg_set_a: float | None = Field(default=None, description="충전기에 설정한 충전 전류 (A)")
    vbat_v: float | None = Field(default=None, description="배터리 전압 (V)")
    isys_a: float | None = Field(default=None, description="충전기가 함께 공급하는 시스템 부하 전류 (A)")
    rt1_ohm: float | None = Field(default=None, description="충전기 TS 핀 분압의 REGN~TS 저항 RT1 (Ω)")
    rt2_ohm: float | None = Field(default=None, description="충전기 TS 핀 분압의 TS~GND 저항 RT2 (Ω). 없으면 비워 둔다")
    ntc_r25_ohm: float | None = Field(default=None, description="배터리 NTC의 25 °C 저항 (Ω)")
    iout_a: float | None = None
    ta_c: float | None = Field(default=None, description="주변 온도")
    fsw_hz: float | None = None
    cout_f: float | None = None
    esr_ohm: float | None = None
    l_h: float | None = None
    vdd_v: float | None = None
    pullup_ohm: float | None = None
    cb_f: float | None = Field(default=None, description="I2C 버스 커패시턴스")
    r_top_ohm: float | None = None
    r_bottom_ohm: float | None = None

    def known(self) -> dict[str, float]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class SalesRequest(BaseModel):
    """견적·샘플·설계 상담 문의에서 고객이 알려 준 요청 정보. 없으면 비워 둔다 (지어내지 않는다)."""

    kind: Literal["quote", "sample", "design", ""] = Field(default="", description="quote=견적, sample=샘플, design=설계 상담·부품 추천")
    quantity: int | None = Field(default=None, description="요청 수량 (개)")
    need_by: str = Field(default="", description="필요한 시점·납기 (고객이 쓴 그대로, 예: '6주 안', '11월')")
    application: str = Field(default="", description="사용 용도·제품 (고객이 쓴 그대로)")
    stage: Literal["prototype", "production", ""] = Field(default="", description="prototype=시제품·평가, production=양산")
    notes: str = Field(default="", description="우선순위 같은 요구 (예: 저잡음, 고효율, 소형)")


class IntakeResult(BaseModel):
    category: Category = Field(
        description="technical=동작·설계 문제, quality_claim=불량·반품, logistics=배송·주문 상태, "
        "quote_sample=견적·샘플 요청, design_request=신규 설계 요구사항·부품 추천·대체품 상담, other=기타"
    )
    route: Route = Field(description="fae=기술지원, cpr=품질 클레임 접수, customer_support=고객지원, sales=영업(견적·샘플·설계 상담)")
    part_numbers: list[str] = Field(default_factory=list, description="언급된 부품 번호(대문자)")
    symptoms: list[str] = Field(default_factory=list, description="증상 코드: " + ", ".join(SYMPTOM_CODES))
    conditions: Conditions = Field(default_factory=Conditions)
    request: SalesRequest = Field(default_factory=SalesRequest)
    urgency: Literal["low", "medium", "high"] = "medium"
    missing_info: list[str] = Field(default_factory=list, description="답변 전에 고객에게 더 받아야 할 정보")
    summary: str = ""


class Evidence(BaseModel):
    source: str = Field(description="근거 문서·사례 ID (예: EXAMPLE-BUCK1_notes.md §Output ripple)")
    snippet: str = ""
    score: float = 0.0


class Hypothesis(BaseModel):
    cause: str
    mechanism: str = ""
    confidence: float = Field(default=0.5, description="0~1")
    evidence: list[Evidence] = Field(default_factory=list)


class ComponentChange(BaseModel):
    target: str = Field(description="바꿀 대상 (예: 출력 커패시터 Cout)")
    before: str = ""
    after: str = ""


class Bound(BaseModel):
    """테스트 결과 지표의 허용 범위."""

    metric: str
    min: float | None = None
    max: float | None = None


class CheckStep(BaseModel):
    """테스트 한 단계. purpose: reproduce=현재 조건으로 증상 재현, verify=해결책 확인, info=참고."""

    label: str
    purpose: Literal["reproduce", "verify", "info"] = "verify"
    tool: str = Field(description="예: calc.thermal_estimate, spice.compare_circuit")
    args_json: str = Field(default="{}", description="도구 인자 JSON 문자열")
    expect: list[Bound] = Field(default_factory=list)

    def args(self) -> dict[str, Any]:
        return json.loads(self.args_json or "{}")


class Solution(BaseModel):
    summary: str
    changes: list[ComponentChange] = Field(default_factory=list)
    test_plan: list[CheckStep] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list, description="계산에 쓴 가정값")


class Diagnosis(BaseModel):
    hypotheses: list[Hypothesis] = Field(default_factory=list, description="가능성 높은 순, 최대 3개")
    solution: Solution | None = None


class StepResult(BaseModel):
    label: str
    purpose: str
    tool: str
    status: Literal["pass", "fail", "skipped", "error"]
    outputs: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class CheckReport(BaseModel):
    status: Literal["pass", "fail", "partial", "skipped"]
    steps: list[StepResult] = Field(default_factory=list)


class QAReview(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)
    issue_codes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CPRCheck(BaseModel):
    """품질 클레임(반품) 접수 전 필수 정보 점검과 8D 초안."""

    checklist: dict[str, bool]
    missing: list[str]
    eight_d_draft: dict[str, str]


class ResponseDraft(BaseModel):
    customer_reply: str = Field(description="고객에게 보낼 답변 초안")
    internal_note: str = Field(default="", description="사람 검토자를 위한 내부 메모")
