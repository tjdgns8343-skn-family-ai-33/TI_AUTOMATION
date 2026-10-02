"""고객 조건과 부품 카드로 코드가 직접 계산하는 설계 점검.

LLM이 규칙 목록에서 놓치기 쉬운 수치 비교를 결정론적으로 한다 (ratings.py가 정격을 비교하는 것과 같은 방식).
계산 도구는 `call`(ToolGateway.call 같은 함수)로 부른다. 지금은 벅 컨버터만 다룬다.

- current_limit: 인덕터 최대 전류(부하 + 리플 전류/2, 불연속이면 피크)를 카드의 전류 제한 최소값과 비교한다.
  인덕터가 작아 전류 리플이 크면 부하가 정격 이내여도 전류 제한에 걸린다 (평가 문의 D-1 사례).
- measured_ripple: 고객이 잰 출력 리플을 같은 조건의 계산과 비교한다. 3배 이상 다르면 계산으로 설명되지 않는 값이라
  원인을 단정하지 않고 측정 방법·레이아웃 같은 다른 원인을 확인하게 한다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .schemas import IntakeResult

Call = Callable[..., dict[str, Any]]
RIPPLE_EXPLAINED_RATIO = 3.0  # 잰 값이 계산의 1/3~3배 안이면 일치로 본다
DEFAULT_COUT_F = 10e-6  # 출력 커패시터를 모르면 쓰는 값 (전류 지표에는 영향이 없다)
RIPPLE_CALC = "calc.buck_ripple_estimate"


def design_facts(intake: IntakeResult, card: dict[str, Any] | None, call: Call) -> list[dict[str, Any]]:
    """설계 점검 결과 목록. 각 항목은 kind, status, text(한국어 설명)와 계산한 숫자를 담는다. 점검할 수 없으면 빈 목록."""
    if not card or str(card.get("type", "")).lower() != "buck":
        return []
    conditions = intake.conditions.known()
    facts = []
    for check in (_current_limit, _measured_ripple):
        try:
            fact = check(conditions, card, call)
        except (ValueError, TypeError, KeyError, ZeroDivisionError):  # 이상한 입력(예: Vout ≥ Vin)은 점검하지 않는다
            fact = None
        if fact:
            facts.append(fact)
    return facts


def _frequency(conditions: dict[str, float], card: dict[str, Any]) -> tuple[float | None, bool]:
    """(스위칭 주파수, 카드의 전형값으로 가정했는지)."""
    if conditions.get("fsw_hz"):
        return conditions["fsw_hz"], False
    typical = (card.get("typical") or {}).get("fsw_hz")
    return (float(typical), True) if typical else (None, False)


def _ripple(conditions: dict[str, float], fsw: float, call: Call, *, esr: float, iout: float) -> dict[str, Any]:
    return call(
        RIPPLE_CALC,
        vin_v=conditions["vin_v"],
        vout_v=conditions["vout_v"],
        l_h=conditions["l_h"],
        fsw_hz=fsw,
        cout_f=conditions.get("cout_f") or DEFAULT_COUT_F,
        esr_ohm=esr,
        iout_a=iout,
    )


def _current_limit(conditions: dict[str, float], card: dict[str, Any], call: Call) -> dict[str, Any] | None:
    typical = card.get("typical") or {}
    limit_min, limit_typ = typical.get("current_limit_a_min"), typical.get("current_limit_a")
    fsw, assumed = _frequency(conditions, card)
    needed = ("vin_v", "vout_v", "iout_a", "l_h")
    if not limit_min or fsw is None or any(conditions.get(key) is None for key in needed):
        return None
    iout = conditions["iout_a"]
    result = _ripple(conditions, fsw, call, esr=0.0, iout=iout)
    peak, ripple_a, dcm = result["il_max_a"], result["delta_il_a"], result.get("dcm") == 1
    note = f" 스위칭 주파수는 카드의 전형값 {fsw:.3g} Hz로 가정했습니다." if assumed else ""
    fact: dict[str, Any] = {
        "kind": "current_limit",
        "il_max_a": peak,
        "delta_il_a": ripple_a,
        "limit_min_a": limit_min,
        "iout_a": iout,
        "dcm": int(dcm),
    }
    if peak >= limit_min:
        what = f"인덕터 피크 전류 {peak:.3g} A(불연속 전도)" if dcm else (
            f"인덕터 최대 전류 {peak:.3g} A(부하 {iout:g} A + 리플 전류 {ripple_a:.3g} A p-p의 절반)"
        )
        text = f"계산한 {what}가 상단 스위치의 정적 전류 제한 최소값 {limit_min:g} A 이상입니다."
        if not dcm:
            fact["onset_load_a"] = limit_min - ripple_a / 2
            text += f" 부하가 약 {fact['onset_load_a']:.3g} A를 넘으면 최소 제한 부품은 전류 제한에 걸릴 수 있습니다"
            if limit_typ:
                text += f" (전형 제한 {limit_typ:g} A 기준으로는 약 {limit_typ - ripple_a / 2:.3g} A)"
            text += "."
        fact["status"] = "exceeds_min"
    else:
        text = f"계산한 인덕터 최대 전류 {peak:.3g} A는 최소 전류 제한 {limit_min:g} A보다 {limit_min - peak:.3g} A 낮아 여유가 있습니다."
        fact["status"] = "ok"
    fact["text"] = text + note
    return fact


def _measured_ripple(conditions: dict[str, float], card: dict[str, Any], call: Call) -> dict[str, Any] | None:
    measured = conditions.get("ripple_meas_v")
    fsw, _ = _frequency(conditions, card)
    needed = ("vin_v", "vout_v", "l_h", "cout_f")
    if not measured or fsw is None or any(conditions.get(key) is None for key in needed):
        return None
    esr = conditions.get("esr_ohm")
    result = _ripple(conditions, fsw, call, esr=esr or 0.0, iout=conditions.get("iout_a") or 0.0)
    m_mv = measured * 1e3
    fact: dict[str, Any] = {"kind": "measured_ripple", "measured_v": measured, "calc_v": result["ripple_v"]}
    if result.get("dcm") == 1:
        fact["status"] = "skipped"
        fact["text"] = (
            f"고객이 잰 리플 {m_mv:.3g} mV는 부하가 임계 전류 {result['iout_crit_a']:.3g} A보다 작아 불연속 전도라서 "
            "연속 모드 식으로 비교할 수 없습니다. 시뮬레이션(buck_switch)으로 확인하세요."
        )
        return fact
    cap_v, ripple_a = result["ripple_cap_v"], result["delta_il_a"]
    low, high = 1 / RIPPLE_EXPLAINED_RATIO, RIPPLE_EXPLAINED_RATIO
    if esr is not None:
        ratio = measured / result["ripple_v"]
        fact["ratio"] = ratio
        if low <= ratio <= high:
            fact["status"] = "consistent"
            fact["text"] = (
                f"고객이 잰 리플 {m_mv:.3g} mV는 같은 조건의 계산 {result['ripple_v'] * 1e3:.3g} mV"
                f"(용량 성분 {cap_v * 1e3:.3g} mV + ESR 성분 {result['ripple_esr_v'] * 1e3:.3g} mV)와 {ratio:.2g}배로 일치합니다."
            )
        else:
            fact["status"] = "unexplained"
            fact["text"] = (
                f"고객이 잰 리플 {m_mv:.3g} mV는 같은 조건의 계산 {result['ripple_v'] * 1e3:.3g} mV와 {ratio:.2g}배 차이가 납니다. "
                "계산으로는 설명되지 않으니 이 값을 근거로 원인을 단정하지 말고, 측정 방법(프로브 접지선), 레이아웃, "
                "다른 동작(전류 제한 반복 등)을 확인해야 합니다."
            )
        return fact
    # ESR을 모르면 계산은 용량 성분만 본 하한이다. 잰 값이 그보다 크면 설명에 필요한 ESR을 역산해 알려 준다
    if measured < cap_v * low:
        fact["status"] = "unexplained"
        fact["text"] = (
            f"고객이 잰 리플 {m_mv:.3g} mV는 용량 성분만 계산한 하한 {cap_v * 1e3:.3g} mV보다 {high:g}배 이상 작습니다. "
            "측정 방법이나 입력 조건(주파수, 커패시터 값)을 확인해야 합니다."
        )
    elif measured <= cap_v * high:
        fact["status"] = "consistent"
        fact["text"] = f"고객이 잰 리플 {m_mv:.3g} mV는 용량 성분만 계산한 {cap_v * 1e3:.3g} mV와 같은 수준입니다 (ESR 미입력)."
    else:
        implied = (measured - cap_v) / ripple_a
        fact["implied_esr_ohm"] = implied
        fact["status"] = "needs_esr"
        fact["text"] = (
            f"고객이 ESR을 알려 주지 않았습니다. 잰 리플 {m_mv:.3g} mV는 용량 성분 {cap_v * 1e3:.3g} mV보다 크고, "
            f"ESR이 약 {implied * 1e3:.3g} mΩ이어야 설명됩니다 (인덕터 전류 리플 {ripple_a:.3g} A p-p). 커패시터의 실제 ESR이 "
            "이보다 훨씬 낮으면 계산으로는 설명되지 않으니, 이 값을 근거로 원인을 단정하지 말고 측정 방법(프로브 접지선), "
            "레이아웃, 다른 동작(전류 제한 반복 등)을 확인해야 합니다."
        )
    return fact


def format_facts(facts: list[dict[str, Any]] | None) -> str:
    """프롬프트·메모에 넣을 줄글 목록."""
    return "\n".join(f"- [{f['kind']} / {f['status']}] {f['text']}" for f in facts or [])
