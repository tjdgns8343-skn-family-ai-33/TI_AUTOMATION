"""고객 조건과 부품 카드로 코드가 직접 계산하는 설계 점검.

LLM이 규칙 목록에서 놓치기 쉬운 수치 비교를 결정론적으로 한다 (ratings.py가 정격을 비교하는 것과 같은 방식).
계산 도구는 `call`(ToolGateway.call 같은 함수)로 부른다. 부품 종류(카드의 type)마다 점검이 다르다.

벅 컨버터
- current_limit: 인덕터 최대 전류(부하 + 리플 전류/2, 불연속이면 피크)를 카드의 전류 제한 최소값과 비교한다.
  인덕터가 작아 전류 리플이 크면 부하가 정격 이내여도 전류 제한에 걸린다 (평가 문의 D-1).
- measured_ripple: 고객이 잰 출력 리플을 같은 조건의 계산과 비교한다. 3배 이상 다르면 계산으로 설명되지 않는 값이라
  원인을 단정하지 않고 측정 방법·레이아웃 같은 다른 원인을 확인하게 한다.
- soft_start: SS 커패시터로 소프트 스타트 시간(CSS × 기준 전압 ÷ 충전 전류)을 계산해 데이터시트 권장 범위와 비교한다 (M-3).
- min_on_time: 온타임을 카드의 최소 제어 가능 온타임(전형·최대)과 비교한다. 이상적 듀티로 계산한 값과 다이오드 강하를 넣은 값이
  최대값을 사이에 두고 갈리면 확정하지 않고 불확실하다고 알린다 (M-3).
LDO
- ldo_thermal: 소모전력과 접합 온도를 열 차단·권장 최대와 비교하고, 고객이 잰 표면 온도가 있으면 접합 온도와의 차이를 알린다 (M-1).
연산증폭기
- offset: 비반전 이득에서 입력 오프셋 전압이 출력에 만드는 전형·최대값을 알린다. 스펙 안의 값이면 불량이 아니다 (M-2).

벅 컨트롤러·충전기와 정격 (2026-10-07, 새 부품 LM5146·BQ25895 측정에서 모델이 못 계산하거나 틀린 것을 코드로 옮김)
- ilim_valley: ILIM 저항이 정하는 골짜기 전류 한계와 평균 한계 부하, 필요한 RILIM을 계산한다. 골짜기 방식이라 인덕터를 키워도 한계 부하는 오르지 않는다 (Q-1).
- max_duty: 최소 오프타임으로 정해지는 최대 듀티(전형·최악)와 낮은 입력에서 필요한 듀티를 비교하고 유지 가능한 주파수를 알린다 (Q-2).
- rating: 고객 조건이 카드의 절대 최대·권장 범위를 넘는지 (VCC 포함). 값 비교를 모델이 틀리게 쓰지 않도록 사실로 넘긴다 (Q-1).
- input_current_limit / charge_power (충전기): ILIM 핀 저항의 IINMAX = KILIM/RILIM과 레지스터 중 낮은 값, 입력 전력이 낼 수 있는 충전 전류 (Q-3).
- ts_window (충전기): TS 분압과 NTC로 충전 시작·차단 문턱 저항과 온도(추정)를 계산한다 (Q-4).

COVERAGE는 어떤 점검 상태가 원인 가설에 반드시 들어가야 하는지 정하는 표다 (검수와 mock 분석이 함께 쓴다).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from .ratings import rating_facts
from .schemas import IntakeResult

Call = Callable[..., dict[str, Any]]
RIPPLE_EXPLAINED_RATIO = 3.0  # 잰 값이 계산의 1/3~3배 안이면 일치로 본다
DEFAULT_COUT_F = 10e-6  # 출력 커패시터를 모르면 쓰는 값 (전류 지표에는 영향이 없다)
ASSUMED_DIODE_V = 0.4  # 비동기 벅의 외부 다이오드 순방향 전압 일반값 (tools/spice.py의 BUCK_DEFAULTS와 같은 값, 가정)
SOFT_START_TARGET_S = 5e-3  # 권장 소프트 스타트 시간을 알릴 때의 목표
RIPPLE_CALC = "calc.buck_ripple_estimate"
LDO_CALC = "calc.ldo_thermal_estimate"

_SOFT_START_WORDS = (
    "소프트 스타트", "소프트스타트", "soft-start", "soft start", "slow-start", "slow start", "ss 핀", "ss 커패시터", "ss 캡",
)  # fmt: skip
_HEAT_WORDS = ("열 차단", "열차단", "열 셧다운", "과열", "접합 온도", "소모전력", "발열", "thermal")
_ILIM_WORDS = ("ilim", "골짜기", "valley", "전류 제한 설정", "전류 제한 저항", "전류제한 설정")  # 히컵·전류 제한은 증상 말이라 다른 원인(소프트 스타트 돌입)의 가설도 걸리므로 설정 값을 가리키는 말만 쓴다
_DUTY_WORDS = ("최대 듀티", "듀티", "duty", "오프타임", "off-time", "드롭아웃", "dropout")
_INPUT_LIMIT_WORDS = ("ilim", "입력 전류 제한", "입력 전류 한계", "iinlim", "입력 전류")
_POWER_WORDS = ("입력 전력", "전력", "입력 전류", "iinlim", "ilim")
_TS_WORDS = ("ts 핀", "ts 분압", "ts 회로", "ts 네트워크", "ts 저항", "서미스터", "ntc", "thermistor", "온도 창", "온도 윈도우")
# (점검 종류, 상태) → (가설 제목, 가설에 있어야 하는 낱말, 해결책 변경 대상, 변경 방향). mock 분석은 제목과 변경안을 쓴다
COVERAGE: dict[tuple[str, str], tuple[str, tuple[str, ...], str, str]] = {
    ("current_limit", "exceeds_min"): (
        "인덕터 전류 리플이 커서 전류 제한에 걸림",
        ("전류 제한", "전류제한", "current limit", "과전류", "피크 전류", "최대 전류"),
        "인덕터",
        "더 큰 값 (최대 전류가 전류 제한 최소값 아래가 되도록)",
    ),
    ("soft_start", "too_slow"): (
        "소프트 스타트 커패시터가 너무 커서 기동이 느림",
        _SOFT_START_WORDS,
        "SS 커패시터",
        "권장 소프트 스타트 시간 범위 안이 되도록 줄임",
    ),
    ("soft_start", "too_fast"): (
        "소프트 스타트 커패시터가 너무 작아 기동이 빠름(돌입 전류)",
        _SOFT_START_WORDS,
        "SS 커패시터",
        "권장 소프트 스타트 시간 범위 안이 되도록 키움",
    ),
    ("min_on_time", "below_typ"): (
        "온타임이 최소 제어 가능 온타임보다 짧아 펄스를 건너뜀",
        ("최소 온타임", "온타임", "on-time", "on time", "펄스"),
        "입력 전압 또는 스위칭 주파수",
        "온타임이 최소 온타임보다 길어지도록 조정",
    ),
    ("min_on_time", "marginal"): (
        "온타임이 최소 제어 가능 온타임 한계에 가까워 일부 부품에서 펄스를 건너뜀",
        ("최소 온타임", "온타임", "on-time", "on time", "펄스"),
        "입력 전압 또는 스위칭 주파수",
        "온타임이 최소 온타임 최대값보다 길어지도록 조정",
    ),
    ("ldo_thermal", "shutdown"): (
        "LDO 소모전력으로 접합 온도가 열 차단 온도를 넘음",
        _HEAT_WORDS,
        "입력 전압",
        "접합 온도가 권장 최대 이내가 되도록 낮추거나 앞단에 벅 레귤레이터를 추가",
    ),
    ("ldo_thermal", "over_recommended"): (
        "LDO 접합 온도가 권장 최대를 넘음",
        _HEAT_WORDS,
        "입력 전압",
        "접합 온도가 권장 최대 이내가 되도록 낮춤",
    ),
    ("ilim_valley", "too_low"): (
        "ILIM 저항이 너무 작아 골짜기 전류 제한에 걸려 히컵이 반복됨",
        _ILIM_WORDS,
        "ILIM 저항",
        "한계가 부하의 골짜기 전류보다 높아지도록 키움 (인덕터를 키우는 것이 아님)",
    ),
    ("max_duty", "cannot_regulate"): (
        "최소 오프타임 때문에 최대 듀티가 모자라 낮은 입력에서 출력을 유지하지 못함",
        _DUTY_WORDS,
        "스위칭 주파수 또는 최저 입력 전압",
        "최악 최대 듀티가 필요 듀티보다 높아지도록 주파수를 낮추거나 최저 입력을 올림",
    ),
    ("max_duty", "marginal"): (
        "최대 듀티 한계에 가까워 일부 부품에서 낮은 입력의 출력이 처짐",
        _DUTY_WORDS,
        "스위칭 주파수 또는 최저 입력 전압",
        "최악 최대 듀티가 필요 듀티보다 높아지도록 조정",
    ),
    ("input_current_limit", "pin_limits"): (
        "ILIM 핀 저항이 입력 전류를 레지스터(IINLIM) 설정보다 낮게 제한함",
        _INPUT_LIMIT_WORDS,
        "ILIM 핀 저항",
        "원하는 입력 전류(KILIM ÷ RILIM)에 맞게 낮춤",
    ),
    ("charge_power", "power_limited"): (
        "입력 전력이 모자라 충전 전류를 설정값까지 올리지 못함",
        _POWER_WORDS,
        "입력 전류 한계 또는 어댑터",
        "설정한 충전 전류에 필요한 입력 전류가 되도록 올림",
    ),
    ("ts_window", "hot_low"): (
        "TS 분압이 충전 허용 온도 창을 좁혀 팩이 따뜻해지면 충전을 멈춤",
        _TS_WORDS,
        "TS 분압 저항(RT1·RT2)",
        "데이터시트 103AT 예(RT1 5.21 kΩ, RT2 29.87 kΩ, 0~45 °C)처럼 창이 0~45 °C가 되도록 다시 정함",
    ),
    ("ts_window", "cold_high"): (
        "TS 분압이 충전 허용 온도 창을 좁혀 낮은 온도에서 충전이 시작되지 않음",
        _TS_WORDS,
        "TS 분압 저항(RT1·RT2)",
        "데이터시트 103AT 예처럼 창이 0~45 °C가 되도록 다시 정함",
    ),
    ("ts_window", "both"): (
        "TS 분압이 충전 허용 온도 창을 좁혀 충전이 멈추거나 시작되지 않음",
        _TS_WORDS,
        "TS 분압 저항(RT1·RT2)",
        "데이터시트 103AT 예처럼 창이 0~45 °C가 되도록 다시 정함",
    ),
}


def design_facts(intake: IntakeResult, card: dict[str, Any] | None, call: Call) -> list[dict[str, Any]]:
    """설계 점검 결과 목록. 각 항목은 kind, status, text(한국어 설명)와 계산한 숫자를 담는다. 점검할 수 없으면 빈 목록."""
    if not card:
        return []
    conditions = intake.conditions.known()
    facts = []
    for check in CHECKS.get(str(card.get("type", "")).lower(), ()):
        try:
            fact = check(conditions, card, call)
        except (ValueError, TypeError, KeyError, ZeroDivisionError):  # 이상한 입력(예: Vout ≥ Vin)은 점검하지 않는다
            fact = None
        if fact:
            facts.append(fact)
    rating = _rating_fact(intake, card)
    if rating:
        facts.append(rating)
    return facts


def _rating_fact(intake: IntakeResult, card: dict[str, Any]) -> dict[str, Any] | None:
    """고객 조건이 부품 카드의 절대 최대·권장 범위를 넘으면 사실로 올린다 (VCC 같은 항목 포함, ratings.py와 같은 로직)."""
    found = rating_facts(intake, card)
    if not (found["abs_over"] or found["rec_over"]):
        return None
    status = "abs_over" if found["abs_over"] else "rec_over"
    text = "정격 점검: " + ". ".join([*found["abs_over"], *found["rec_over"]]) + "."
    if found["abs_over"]:
        text += " 절대 최대를 넘으면 소자가 영구 손상될 수 있습니다. 값을 비교할 때 한계보다 큰지 작은지를 정확히 쓰세요."
    return {"kind": "rating", "status": status, "abs_over": found["abs_over"], "rec_over": found["rec_over"], "text": text}


def missing_causes(facts: list[dict[str, Any]] | None, hypotheses_text: str) -> list[tuple[dict[str, Any], tuple]]:
    """원인 가설(소문자 글)에 들어가야 하는데 빠진 점검 항목과 그 규칙(COVERAGE)을 돌려준다."""
    text = hypotheses_text.lower()
    missing = []
    for fact in facts or []:
        rule = COVERAGE.get((fact["kind"], fact["status"]))
        if rule and not any(word in text for word in rule[1]):
            missing.append((fact, rule))
    return missing


def _frequency(conditions: dict[str, float], card: dict[str, Any]) -> tuple[float | None, bool]:
    """(스위칭 주파수, 카드의 전형값으로 가정했는지)."""
    if conditions.get("fsw_hz"):
        return conditions["fsw_hz"], False
    typical = (card.get("typical") or {}).get("fsw_hz")
    return (float(typical), True) if typical else (None, False)


def _assumed_frequency(fsw: float) -> str:
    shown = f"{fsw / 1e6:.3g} MHz" if fsw >= 1e6 else f"{fsw / 1e3:.3g} kHz"
    return f" 스위칭 주파수는 고객이 알려 주지 않아 데이터시트의 전형값 {shown}로 가정했습니다."


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


# ---------------------------------------------------------------- 벅 컨버터


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
    note = _assumed_frequency(fsw) if assumed else ""
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


def _soft_start(conditions: dict[str, float], card: dict[str, Any], call: Call) -> dict[str, Any] | None:
    typical = card.get("typical") or {}
    css = conditions.get("css_f")
    ramp, current = typical.get("ss_ramp_v"), typical.get("ss_charge_current_a")
    if not css or not ramp or not current:
        return None
    ramp, current = float(ramp), float(current)
    tss = css * ramp / current  # 데이터시트 식: TSS = CSS × (기준 전압) ÷ 충전 전류
    low, high, cap_max = typical.get("ss_time_min_s"), typical.get("ss_time_max_s"), typical.get("ss_cap_max_f")
    fact: dict[str, Any] = {"kind": "soft_start", "css_f": css, "tss_s": tss}
    text = (
        f"SS 커패시터 {css * 1e9:.3g} nF의 소프트 스타트 시간은 {css * 1e9:.3g} nF × {ramp:g} V ÷ {current * 1e6:g} µA "
        f"= {tss * 1e3:.3g} ms입니다."
    )
    if high is not None and low is not None and (tss > high or (cap_max and css > cap_max)):
        target = min(max(SOFT_START_TARGET_S, low), high)
        fact["css_target_f"] = target * current / ramp
        fact["status"] = "too_slow"
        limit = f", 커패시터 {cap_max * 1e9:g} nF 이하" if cap_max else ""
        text += (
            f" 데이터시트 권장은 {low * 1e3:g}~{high * 1e3:g} ms{limit}라서 넘습니다. 기동이 느려 뒤쪽 회로가 리셋될 수 있습니다. "
            f"약 {target * 1e3:g} ms가 되려면 SS 커패시터는 약 {fact['css_target_f'] * 1e9:.3g} nF입니다."
        )
    elif low is not None and tss < low:
        fact["status"] = "too_fast"
        text += f" 데이터시트 권장 {low * 1e3:g}~{high * 1e3:g} ms보다 짧아 기동 돌입 전류가 커질 수 있습니다."
    else:
        fact["status"] = "ok"
        text += (
            f" 데이터시트 권장 {low * 1e3:g}~{high * 1e3:g} ms 안입니다."
            if low is not None and high is not None
            else " 이 부품은 소프트 스타트 시간의 권장 범위가 데이터시트에 없습니다."
        )
    fact["text"] = text
    return fact


def _min_on_time(conditions: dict[str, float], card: dict[str, Any], call: Call) -> dict[str, Any] | None:
    typical = card.get("typical") or {}
    t_max, t_typ = typical.get("min_on_time_s"), typical.get("min_on_time_typ_s")
    fsw, assumed = _frequency(conditions, card)
    if not t_max or not t_typ or fsw is None or conditions.get("vin_v") is None or conditions.get("vout_v") is None:
        return None
    vin, vout = conditions["vin_v"], conditions["vout_v"]
    if vout >= vin:
        return None
    ideal = vout / vin / fsw  # 이상적 듀티 Vout/Vin
    vout_min = t_max * fsw * vin  # 최소 온타임 최대값을 만족하는 최소 출력 (이상적)
    note = typical.get("min_on_time_note")  # 카드에 적힌 측정 조건 (예: "VIN 12 V, 25 °C 조건의 값")
    conditions_text = f" ({note})" if note else ""
    if card.get("synchronous") or typical.get("rdson_low_ohm") is not None:
        return _min_on_time_synchronous(ideal, vin, t_typ, t_max, vout_min, conditions_text, fsw, assumed)
    with_diode = (vout + ASSUMED_DIODE_V) / (vin + ASSUMED_DIODE_V) / fsw  # 다이오드 강하를 넣은 듀티
    fact = {
        "kind": "min_on_time",
        "ton_ideal_s": ideal,
        "ton_with_diode_s": with_diode,
        "min_on_time_typ_s": t_typ,
        "min_on_time_max_s": t_max,
        "vout_min_v": vout_min,
    }
    numbers = (
        f"온타임은 이상적 듀티(Vout/Vin)로 {_ns_text(ideal)}, 다이오드 강하 {ASSUMED_DIODE_V:g} V(가정)를 넣으면 "
        f"{_ns_text(with_diode)}입니다. 데이터시트의 최소 제어 가능 온타임은 전형 {t_typ * 1e9:.3g} ns, 최대 {t_max * 1e9:.3g} ns"
        f"{conditions_text}입니다."
    )
    if with_diode >= t_max:
        if ideal >= t_max:
            fact["status"] = "ok"
            text = f"{numbers} 두 값 모두 최대값 이상이라 최소 온타임에 걸리지 않습니다."
        else:
            fact["status"] = "uncertain"
            text = (
                f"{numbers} 이상적 값은 최대값보다 짧고 다이오드 강하를 넣은 값은 길어서, 실제 다이오드 강하에 달려 있어 "
                f"펄스 건너뜀 여부를 확정할 수 없습니다. 입력 {vin:g} V에서 최소 온타임 최대값을 이상적으로 만족하는 최소 출력은 "
                f"약 {vout_min:.3g} V입니다."
            )
    elif with_diode < t_typ:
        fact["status"] = "below_typ"
        text = f"{numbers} 두 값 모두 전형값보다 짧아 펄스를 건너뛸 가능성이 큽니다. 최소 출력(이상적)은 약 {vout_min:.3g} V입니다."
    else:
        fact["status"] = "marginal"
        text = (
            f"{numbers} 전형값보다 길지만 최대값보다 짧아, 최소 온타임이 큰 쪽 부품에서만 펄스를 건너뛸 수 있습니다 "
            f"(같은 설계라도 보드마다 다를 수 있음). 최소 출력(이상적)은 약 {vout_min:.3g} V입니다."
        )
    fact["text"] = text + (_assumed_frequency(fsw) if assumed else "")
    return fact


def _min_on_time_synchronous(
    ideal: float, vin: float, t_typ: float, t_max: float, vout_min: float, conditions_text: str, fsw: float, assumed: bool
) -> dict[str, Any]:
    """동기 벅(하단이 다이오드가 아니라 MOSFET)은 다이오드 강하를 가정하지 않고 듀티 × 주기 하나로 비교한다."""
    fact: dict[str, Any] = {
        "kind": "min_on_time",
        "ton_ideal_s": ideal,
        "min_on_time_typ_s": t_typ,
        "min_on_time_max_s": t_max,
        "vout_min_v": vout_min,
    }
    numbers = (
        f"온타임은 듀티(Vout/Vin) ÷ 스위칭 주파수로 {_ns_text(ideal)}입니다 (외부 FET 강하·데드타임은 넣지 않은 값). "
        f"데이터시트의 최소 제어 가능 온타임은 전형 {t_typ * 1e9:.3g} ns, 최대 {t_max * 1e9:.3g} ns{conditions_text}입니다."
    )
    if ideal >= t_max:
        fact["status"] = "ok"
        text = f"{numbers} 최대값 이상이라 최소 온타임에 걸리지 않습니다."
    elif ideal < t_typ:
        fact["status"] = "below_typ"
        text = (
            f"{numbers} 전형값보다 짧아 펄스를 건너뛸 가능성이 큽니다. 입력 {vin:g} V에서 최소 온타임 최대값을 만족하는 "
            f"최소 출력은 약 {vout_min:.3g} V입니다."
        )
    else:
        fact["status"] = "marginal"
        text = (
            f"{numbers} 전형값보다 길지만 최대값보다 짧아, 최소 온타임이 큰 쪽 부품에서만 펄스를 건너뛸 수 있습니다 "
            f"(같은 설계라도 보드마다 다를 수 있음). 입력 {vin:g} V에서 최소 온타임 최대값을 만족하는 최소 출력은 약 {vout_min:.3g} V입니다."
        )
    fact["text"] = text + (_assumed_frequency(fsw) if assumed else "")
    return fact


def _ns_text(seconds: float) -> str:
    ns = seconds * 1e9
    return f"{ns / 1e3:.3g} µs" if ns >= 1000 else f"{ns:.3g} ns"


def _ilim_valley(conditions: dict[str, float], card: dict[str, Any], call: Call) -> dict[str, Any] | None:
    """ILIM 저항으로 골짜기(valley) 전류 제한을 정하는 컨트롤러(LM5146 같은 외부 FET 벅): 한계와 필요한 저항을 계산한다 (Q-1)."""
    typical = card.get("typical") or {}
    i_rds = typical.get("ilim_current_rdson_a")
    fsw, assumed = _frequency(conditions, card)
    needed = ("vin_v", "vout_v", "iout_a", "l_h", "rds_low_ohm")
    if not i_rds or fsw is None or any(conditions.get(key) is None for key in needed):
        return None
    i_rds = float(i_rds)
    i_min, i_max = float(typical.get("ilim_current_rdson_a_min") or i_rds), float(typical.get("ilim_current_rdson_a_max") or i_rds)
    iout, rds = conditions["iout_a"], conditions["rds_low_ohm"]
    result = _ripple(conditions, fsw, call, esr=0.0, iout=iout)
    if result.get("dcm") == 1:  # 불연속이면 골짜기가 0 A라 이 식으로 비교하지 않는다
        return None
    ripple_a = result["delta_il_a"]
    valley_full = iout - ripple_a / 2
    needed_r = valley_full * rds / i_rds  # 식 (6): 한계가 전부하 골짜기와 같아지는 저항
    low_r, high_r = valley_full * rds / i_max, valley_full * rds / i_min
    fact: dict[str, Any] = {
        "kind": "ilim_valley",
        "delta_il_a": ripple_a,
        "valley_full_load_a": valley_full,
        "needed_rilim_ohm": needed_r,
        "needed_rilim_low_ohm": low_r,
        "needed_rilim_high_ohm": high_r,
    }
    text = (
        f"인덕터 리플 전류는 {ripple_a:.3g} A p-p이고 부하 {iout:g} A에서 인덕터 전류의 골짜기는 {valley_full:.4g} A입니다. "
        f"식 RILIM = (Iout − ΔIL/2) × RDS(on) ÷ IRDSON으로 한계를 전부하 골짜기에 맞추는 RILIM은 {needed_r:.3g} Ω"
        f"(IRDSON {i_min * 1e6:g}~{i_max * 1e6:g} µA 편차로 {low_r:.3g}~{high_r:.3g} Ω)입니다. 이 값은 한계가 전부하와 같아지는 점이라 "
        "부품·온도 편차 여유를 더 둔 값을 골라야 합니다."
    )
    tau = typical.get("ilim_rc_time_constant_s")
    if tau:
        text += f" RILIM × CILIM ≈ {tau * 1e9:g} ns이므로 RILIM {needed_r:.3g} Ω이면 CILIM ≈ {tau / needed_r * 1e12:.2g} pF입니다."
    rilim = conditions.get("rilim_ohm")
    if rilim is None:
        fact["status"] = "info"
        fact["text"] = text + (_assumed_frequency(fsw) if assumed else "")
        return fact
    valley_limit = i_rds * rilim / rds
    avg_limit = valley_limit + ripple_a / 2  # 한계에서 평균 부하 전류 = 골짜기 + ΔIL/2
    avg_limit_min = i_min * rilim / rds + ripple_a / 2
    fact.update(rilim_ohm=rilim, valley_limit_a=valley_limit, avg_limit_a=avg_limit, avg_limit_min_a=avg_limit_min, iout_a=iout)
    head = (
        f"ILIM 저항 {rilim:g} Ω이면 골짜기 전류 한계는 {i_rds * 1e6:g} µA × {rilim:g} Ω ÷ {rds * 1e3:g} mΩ = {valley_limit:.3g} A, "
        f"평균 부하로는 {avg_limit:.3g} A입니다 (RDS(on)은 고객이 알려 준 값을 25 °C 값으로 봄). "
    )
    if avg_limit < iout:
        fact["status"] = "too_low"
        delay, off = typical.get("hiccup_delay_cycles"), typical.get("hiccup_off_cycles")
        hiccup = ""
        if delay and off:
            hiccup = (
                f" 한계가 {delay}클럭 연속이면 히컵에 들어가 {off}클럭 동안 쉽니다 ({fsw / 1e3:.3g} kHz면 동작 {delay / fsw * 1e3:.3g} ms, "
                f"정지 {off / fsw * 1e3:.3g} ms, 주기 {(delay + off) / fsw * 1e3:.3g} ms)."
            )
        fact["text"] = (
            f"{head}부하 {iout:g} A가 이보다 커서 한계에 걸립니다. 부하가 약 {avg_limit:.3g} A를 넘으면 전류 제한으로 출력이 꺼졌다 켜집니다."
            f"{hiccup} 이 방식은 골짜기 전류를 제한하므로 인덕터를 키우면 리플 전류가 줄어 평균 한계 부하가 오히려 낮아지니, "
            f"ILIM 저항을 키워야 합니다. {text}"
        )
    elif avg_limit_min < iout:
        fact["status"] = "marginal"
        fact["text"] = f"{head}IRDSON 최소값({i_min * 1e6:g} µA)에서는 평균 한계가 {avg_limit_min:.3g} A로 부하 {iout:g} A보다 낮아져 부품에 따라 한계에 걸릴 수 있습니다. {text}"
    else:
        fact["status"] = "ok"
        fact["text"] = f"{head}부하 {iout:g} A보다 높아 전류 제한에 걸리지 않습니다 (IRDSON 최소값에서도 {avg_limit_min:.3g} A). {text}"
    if assumed:
        fact["text"] += _assumed_frequency(fsw)
    return fact


MAX_DUTY_MARGIN = 0.03  # 권장 주파수를 알릴 때 최악 최대 듀티가 필요 듀티보다 이만큼(3 %p) 높게


def _max_duty(conditions: dict[str, float], card: dict[str, Any], call: Call) -> dict[str, Any] | None:
    """최소 오프타임으로 정해지는 최대 듀티와 낮은 입력에서 필요한 듀티를 비교한다 (Q-2)."""
    typical = card.get("typical") or {}
    t_typ, t_max = typical.get("min_off_time_typ_s"), typical.get("min_off_time_s")
    fsw, assumed = _frequency(conditions, card)
    vout = conditions.get("vout_v")
    vin = conditions.get("vin_min_v") or conditions.get("vin_v")
    if fsw is None or vout is None or vin is None:
        return None
    if t_typ and t_max:
        d_typ, d_worst = 1 - t_typ * fsw, 1 - t_max * fsw
        source = f"최소 오프타임 전형 {_ns_text(t_typ)}, 최대 {_ns_text(t_max)}라서 최대 듀티는 1 − tOFF × fsw = 전형 {d_typ * 100:.3g} %, 최악 {d_worst * 100:.3g} %입니다"
    elif typical.get("max_duty"):  # 주파수와 무관하게 데이터시트가 최소 보장 최대 듀티만 주는 부품
        d_worst = float(typical["max_duty"])
        d_typ = float(typical["max_duty_typ"]) if typical.get("max_duty_typ") else None
        source = f"데이터시트의 최대 듀티는 최소 {d_worst * 100:.3g} %" + (f", 전형 {d_typ * 100:.3g} %입니다" if d_typ else "입니다")
    else:
        return None
    d_req = vout / vin
    basis = f"최저 입력 {vin:g} V" if conditions.get("vin_min_v") else f"입력 {vin:g} V"
    fact: dict[str, Any] = {
        "kind": "max_duty",
        "duty_required": d_req,
        "duty_max_typ": d_typ,
        "duty_max_worst": d_worst,
        "vin_v": vin,
    }
    text = f"{basis}에서 출력 {vout:g} V를 내려면 듀티 {d_req * 100:.3g} %가 필요합니다 (손실 전 이상적 값). {source}."
    if t_typ and t_max and typical.get("max_duty_100khz_min") and typical.get("max_duty_400khz_min"):
        text += (
            f" 데이터시트 표의 최대 듀티는 100 kHz 최소 {typical['max_duty_100khz_min'] * 100:g} %, 400 kHz 최소 "
            f"{typical['max_duty_400khz_min'] * 100:g} %뿐이라 다른 주파수는 이 식으로 추정한 값입니다. 낮은 입력에서 거의 100 % 듀티로 "
            "동작한다는 설명은 낮은 주파수의 값이고, 주파수가 높을수록 최대 듀티가 줄어듭니다."
        )
    if d_typ is not None and d_req > d_typ:
        fact["status"] = "cannot_regulate"
        v_low, v_high = vin * d_worst, vin * d_typ
        fact["vout_possible_low_v"], fact["vout_possible_high_v"] = v_low, v_high
        fact["vin_needed_typ_v"], fact["vin_needed_worst_v"] = vout / d_typ, vout / d_worst
        text += (
            f" 필요 듀티가 전형 최대 듀티보다 커서 이 입력에서는 출력을 유지하지 못합니다. 출력은 약 {v_low:.3g}~{v_high:.3g} V(손실 전)까지만 "
            f"나옵니다. 유지하려면 최저 입력을 {vout / d_typ:.3g} V(전형)~{vout / d_worst:.3g} V(최악) 이상으로 하거나 주파수를 낮춰야 합니다."
        )
        if t_max and 1 - (d_req + MAX_DUTY_MARGIN) > 0:
            fact["fsw_recommended_hz"] = (1 - (d_req + MAX_DUTY_MARGIN)) / t_max
            text += (
                f" 최악 오프타임에서도 필요 듀티보다 {MAX_DUTY_MARGIN * 100:g} %p 높은 최대 듀티가 되려면 fsw는 약 "
                f"{fact['fsw_recommended_hz'] / 1e3:.3g} kHz 이하입니다."
            )
    elif d_req > d_worst:
        fact["status"] = "marginal"
        text += f" 전형 부품은 유지하지만 최대 듀티가 낮은 부품은 이 입력에서 약 {vin * d_worst:.3g} V밖에 못 낼 수 있습니다."
    else:
        fact["status"] = "ok"
        text += " 필요 듀티가 최악 최대 듀티 이하라 듀티 한계에 걸리지 않습니다."
    fact["text"] = text + (_assumed_frequency(fsw) if assumed else "")
    return fact


# ---------------------------------------------------------------- LDO


def _ldo_thermal(conditions: dict[str, float], card: dict[str, Any], call: Call) -> dict[str, Any] | None:
    theta = (card.get("thermal") or {}).get("theta_ja_c_per_w")
    needed = ("vin_v", "vout_v", "iout_a", "ta_c")
    if not theta or any(conditions.get(key) is None for key in needed):
        return None
    vin, vout, iout, ta = (conditions[key] for key in needed)
    result = call(LDO_CALC, vin_v=vin, vout_v=vout, iout_a=iout, t_ambient_c=ta, theta_ja_c_per_w=float(theta))
    power, tj = result["pdiss_w"], result["tj_c"]
    typical, recommended = card.get("typical") or {}, card.get("recommended") or {}
    shutdown, tj_rec = typical.get("thermal_shutdown_c"), recommended.get("tj_max_c", 125)
    theta = float(theta)
    imax = (tj_rec - ta) / (theta * (vin - vout))
    vin_max = vout + (tj_rec - ta) / (theta * iout)
    fact: dict[str, Any] = {
        "kind": "ldo_thermal",
        "pdiss_w": power,
        "tj_c": tj,
        "imax_at_tj_rec_a": imax,
        "vin_max_at_tj_rec_v": vin_max,
    }
    text = (
        f"소모전력 ({vin:g} − {vout:g}) × {iout:g} = {power:.3g} W, 접합 온도는 주변 {ta:g} °C + {power:.3g} W × θJA {theta:g} °C/W "
        f"≈ {tj:.3g} °C입니다 (효율 {vout / vin * 100:.3g} %)."
    )
    if shutdown and tj >= shutdown:
        fact["status"] = "shutdown"
        text += f" 열 차단 온도 {shutdown:g} °C를 넘어 출력이 꺼졌다 켜졌다 할 수 있습니다."
    elif tj > tj_rec:
        fact["status"] = "over_recommended"
        text += f" 권장 접합 온도 {tj_rec:g} °C를 넘습니다."
    else:
        fact["status"] = "ok"
        text += f" 권장 접합 온도 {tj_rec:g} °C 안입니다."
    if fact["status"] != "ok":
        if imax > 0:
            text += (
                f" 접합 {tj_rec:g} °C 이내이려면 이 조건에서 부하는 약 {imax:.3g} A 이하, 또는 부하 {iout:g} A에서 입력은 약 "
                f"{vin_max:.3g} V 이하여야 합니다."
            )
        iout_max = recommended.get("iout_max_a")
        if iout_max and iout <= iout_max:
            text += f" 부하가 전류 정격({iout_max:g} A) 이내여도 열이 한계일 수 있습니다."
    tcase = conditions.get("tcase_meas_c")
    if tcase is not None:
        fact["tcase_c"] = tcase
        if tcase < tj:
            text += (
                f" 고객이 잰 표면(케이스) 온도 {tcase:g} °C는 접합 온도가 아닙니다. 접합은 표면보다 항상 높아서 계산한 접합 "
                f"{tj:.3g} °C가 표면 {tcase:g} °C보다 높은 것은 모순이 아닙니다 (θJA는 표준 기판 값). 열 차단이 반복되면 평균 "
                "온도는 더 낮게 보입니다."
            )
        else:
            text += (
                f" 고객이 잰 표면 온도 {tcase:g} °C가 계산한 접합 온도보다 높으니 θJA나 입력 조건을 다시 확인해야 합니다."
            )
    fact["text"] = text
    return fact


# ---------------------------------------------------------------- 연산증폭기


def _mv_text(mv: float) -> str:
    return f"{mv / 1e3:.3g} V" if mv >= 1000 else f"{mv:.3g} mV"


def _offset(conditions: dict[str, float], card: dict[str, Any], call: Call) -> dict[str, Any] | None:
    typical = card.get("typical") or {}
    vos_typ, vos_max = typical.get("vos_typ_mv"), typical.get("vos_max_mv")
    rf, rg = conditions.get("rf_ohm"), conditions.get("rg_ohm")
    if not vos_typ or not vos_max or not rf or not rg:
        return None
    gain = 1 + rf / rg  # 비반전 증폭기 이득
    out_typ, out_max = float(vos_typ) * gain, float(vos_max) * gain
    return {
        "kind": "offset",
        "status": "info",
        "gain": gain,
        "offset_out_typ_mv": out_typ,
        "offset_out_max_mv": out_max,
        "text": (
            f"비반전 이득 {gain:.4g}배에서 입력 오프셋 전압(전형 {float(vos_typ):g} mV, 최대 {float(vos_max):g} mV, 데이터시트 조건 25 °C)은 "
            f"출력에서 전형 {_mv_text(out_typ)}, 최대 {_mv_text(out_max)}가 됩니다. 입력을 0 V로 해도 최대값 이하의 출력 전압이 나오는 것은 "
            "스펙 안의 동작이라 불량이 아닙니다."
        ),
    }



# ---------------------------------------------------------------- 배터리 충전기

ASSUMED_CHARGER_EFFICIENCY = 0.90  # 충전기 변환 효율 가정 (데이터시트 BQ25895: 2 A에서 93 %, 3 A에서 91 %)
TS_HOT_LOW_C = 40.0  # 충전 중 차단 온도가 이보다 낮으면 데이터시트 예(0~45 °C)보다 창이 좁다고 본다
TS_COLD_HIGH_C = 5.0


def _input_current(conditions: dict[str, float], card: dict[str, Any], call: Call) -> dict[str, Any] | None:
    """ILIM 핀 저항이 정하는 입력 전류 한계(IINMAX = KILIM/RILIM)와 레지스터 값 중 낮은 쪽을 비교한다 (Q-3)."""
    typical = card.get("typical") or {}
    k_min, k_typ, k_max = typical.get("kilim_a_ohm_min"), typical.get("kilim_a_ohm"), typical.get("kilim_a_ohm_max")
    rilim = conditions.get("rilim_ohm")
    if not (k_min and k_typ and k_max and rilim):
        return None
    pin_typ, pin_lo, pin_hi = k_typ / rilim, k_min / rilim, k_max / rilim
    register = conditions.get("iinlim_a")
    fact: dict[str, Any] = {
        "kind": "input_current_limit",
        "rilim_ohm": rilim,
        "pin_limit_a": pin_typ,
        "pin_limit_min_a": pin_lo,
        "pin_limit_max_a": pin_hi,
        "iinlim_a": register,
    }
    text = (
        f"ILIM 핀 저항 {rilim:g} Ω이 정하는 입력 전류 한계는 IINMAX = KILIM ÷ RILIM = {k_typ:g} ÷ {rilim:g} = {pin_typ:.3g} A입니다 "
        f"(KILIM {k_min:g}~{k_max:g}이면 {pin_lo:.3g}~{pin_hi:.3g} A). 실제 입력 전류 한계는 레지스터(IINLIM)와 ILIM 핀 중 낮은 값입니다."
    )
    if register is None:
        fact["status"] = "info"
    elif pin_typ < register:
        fact["status"] = "pin_limits"
        target_r = k_typ / register
        fact["rilim_for_register_ohm"] = target_r
        text += (
            f" 레지스터를 {register:g} A로 써도 핀 한계가 더 낮아 입력 전류는 약 {pin_typ:.3g} A에서 멈춥니다. "
            f"{register:g} A를 원하면 RILIM ≈ {k_typ:g} ÷ {register:g} = {target_r:.3g} Ω입니다 "
            f"(KILIM 편차로 {register * k_min / k_typ:.3g}~{register * k_max / k_typ:.3g} A)."
        )
    elif register < pin_lo:
        fact["status"] = "register_limits"
        text += f" 레지스터 {register:g} A가 핀 한계 {pin_lo:.3g}~{pin_hi:.3g} A보다 낮아 레지스터 값이 입력 전류를 정합니다."
    else:
        fact["status"] = "ok"
        text += f" 레지스터 {register:g} A와 핀 한계가 비슷한 수준이라 어느 쪽이 먼저 걸릴지는 편차에 달려 있습니다."
    fact["text"] = text
    return fact


def _charge_power(conditions: dict[str, float], card: dict[str, Any], call: Call) -> dict[str, Any] | None:
    """입력 전력이 낼 수 있는 충전 전류를 계산해 설정한 충전 전류와 비교한다 (Q-3)."""
    typical = card.get("typical") or {}
    vin, vbat, ichg = conditions.get("vin_v"), conditions.get("vbat_v"), conditions.get("ichg_set_a")
    limits = [value for value in (conditions.get("iinlim_a"),) if value is not None]
    k_typ, rilim = typical.get("kilim_a_ohm"), conditions.get("rilim_ohm")
    if k_typ and rilim:
        limits.append(k_typ / rilim)
    if vin is None or vbat is None or ichg is None or not limits:
        return None
    iin = min(limits)  # 실제 입력 전류 한계 (레지스터와 ILIM 핀 중 낮은 값)
    isys = conditions.get("isys_a")
    p_sys = (isys or 0.0) * vbat
    p_in = vin * iin
    efficiency = ASSUMED_CHARGER_EFFICIENCY
    possible = (efficiency * p_in - p_sys) / vbat
    needed_in = (ichg * vbat + p_sys) / efficiency / vin
    fact: dict[str, Any] = {
        "kind": "charge_power",
        "iin_limit_a": iin,
        "p_in_w": p_in,
        "ichg_possible_a": possible,
        "ichg_set_a": ichg,
        "iin_needed_a": needed_in,
    }
    text = (
        f"입력 {vin:g} V × 입력 전류 한계 {iin:.3g} A = {p_in:.3g} W이고, 효율 {efficiency * 100:g} %(가정)이면 {efficiency * p_in:.3g} W에서 "
        f"시스템 {p_sys:.3g} W를 빼고 배터리 {vbat:g} V에 쓸 수 있는 충전 전류는 약 {possible:.3g} A입니다. "
        f"설정한 충전 전류 {ichg:g} A를 내려면 입력 전류가 약 {needed_in:.3g} A 필요합니다."
    )
    if isys is None:
        text += " 시스템 부하는 고객이 알려 주지 않아 0으로 두었습니다(가정)."
    if possible < ichg * 0.9:
        fact["status"] = "power_limited"
        text += " 그래서 충전 전류 설정을 올려도 입력 전력 한계에서 멈춥니다. 충전 전류를 올리려면 입력 전류 한계(ILIM 핀·IINLIM)와 어댑터 용량부터 올려야 합니다."
    else:
        fact["status"] = "ok"
    fact["text"] = text
    return fact


def _ts_window(conditions: dict[str, float], card: dict[str, Any], call: Call) -> dict[str, Any] | None:
    """TS 핀 분압(RT1, RT2)과 NTC로 충전 시작·차단 문턱 저항과 온도를 계산한다 (Q-4)."""
    typical = card.get("typical") or {}
    pct_cold, pct_hot, pct_cut = (typical.get(key) for key in ("ts_cold_threshold_pct", "ts_hot_threshold_pct", "ts_cutoff_pct"))
    points = typical.get("ts_ntc_points")
    rt1 = conditions.get("rt1_ohm")
    if not rt1 or None in (pct_cold, pct_hot, pct_cut) or not points or len(points) < 2:
        return None
    rt2 = conditions.get("rt2_ohm")  # 없으면 열림(RT2 없음)으로 계산한다
    r25 = conditions.get("ntc_r25_ohm") or typical.get("ts_ntc_r25_ohm")
    if not r25:
        return None

    def threshold_ohm(pct: float) -> float | None:
        ratio = pct / 100
        parallel = ratio * rt1 / (1 - ratio)  # NTC와 RT2의 병렬 저항
        if not rt2:
            return parallel
        return None if parallel >= rt2 else 1 / (1 / parallel - 1 / rt2)

    (t0, r0), (t1, r1) = points[0], points[1]
    beta = math.log(r0 / r1) / (1 / (t0 + 273.15) - 1 / (t1 + 273.15))  # 두 점으로 맞춘 β (온도는 추정)

    def temp_c(r_ohm: float) -> float:
        return 1 / (1 / 298.15 + math.log(r_ohm / r25) / beta) - 273.15

    thresholds = {name: threshold_ohm(pct) for name, pct in (("cold", pct_cold), ("hot", pct_hot), ("cut", pct_cut))}
    if any(value is None or value <= 0 for value in thresholds.values()):
        return None
    temps = {name: temp_c(value) for name, value in thresholds.items()}
    fact: dict[str, Any] = {
        "kind": "ts_window",
        "rth_cold_ohm": thresholds["cold"],
        "rth_hot_ohm": thresholds["hot"],
        "rth_cutoff_ohm": thresholds["cut"],
        "t_cold_c": temps["cold"],
        "t_hot_c": temps["hot"],
        "t_cutoff_c": temps["cut"],
    }
    network = f"RT1 {rt1 / 1e3:.3g} kΩ" + (f", RT2 {rt2 / 1e3:.3g} kΩ" if rt2 else " (RT2 없음)")
    text = (
        f"TS 분압 {network}이면 TS 전압/REGN 비율은 NTC 저항 Rth의 함수입니다. 충전을 시작하려면 NTC 저항이 {thresholds['cold'] / 1e3:.3g} kΩ"
        f"({pct_cold:g} %) 이하 {thresholds['hot'] / 1e3:.3g} kΩ({pct_hot:g} %) 이상이어야 하고, 충전 중에는 {thresholds['cut'] / 1e3:.3g} kΩ"
        f"({pct_cut:g} %) 아래로 내려가면 충전을 멈춥니다. 25 °C에서 {r25 / 1e3:g} kΩ인 NTC면 대략 {temps['cold']:.2g} °C~{temps['hot']:.2g} °C에서 "
        f"충전을 시작하고 {temps['cut']:.2g} °C를 넘으면 차단합니다 (온도는 데이터시트 103AT의 {t0:g} °C·{t1:g} °C 두 점으로 맞춘 곡선으로 추정한 값이고 "
        "데이터시트에는 온도표가 없습니다). 데이터시트의 103AT 예(RT1 5.21 kΩ, RT2 29.87 kΩ)는 0~45 °C입니다."
    )
    hot_low, cold_high = temps["cut"] < TS_HOT_LOW_C, temps["cold"] > TS_COLD_HIGH_C
    fact["status"] = "both" if hot_low and cold_high else "hot_low" if hot_low else "cold_high" if cold_high else "ok"
    if hot_low:
        text += f" 팩이 약 {temps['cut']:.2g} °C만 넘어도 충전이 멈추고 STAT가 1 Hz로 깜빡입니다(TS 고장). 식으면 다시 시작합니다."
    if cold_high:
        text += f" 약 {temps['cold']:.2g} °C 이하에서는 충전이 시작되지 않습니다."
    fact["text"] = text
    return fact


CHECKS: dict[str, tuple[Callable[..., dict[str, Any] | None], ...]] = {
    "buck": (_current_limit, _ilim_valley, _measured_ripple, _soft_start, _min_on_time, _max_duty),
    "ldo": (_ldo_thermal,),
    "opamp": (_offset,),
    "charger": (_input_current, _charge_power, _ts_window),
}


def format_facts(facts: list[dict[str, Any]] | None) -> str:
    """프롬프트·메모에 넣을 줄글 목록."""
    return "\n".join(f"- [{f['kind']} / {f['status']}] {f['text']}" for f in facts or [])
