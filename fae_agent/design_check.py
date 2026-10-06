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

COVERAGE는 어떤 점검 상태가 원인 가설에 반드시 들어가야 하는지 정하는 표다 (검수와 mock 분석이 함께 쓴다).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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
    return facts


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
    with_diode = (vout + ASSUMED_DIODE_V) / (vin + ASSUMED_DIODE_V) / fsw  # 다이오드 강하를 넣은 듀티
    vout_min = t_max * fsw * vin  # 최소 온타임 최대값을 만족하는 최소 출력 (이상적)
    fact = {
        "kind": "min_on_time",
        "ton_ideal_s": ideal,
        "ton_with_diode_s": with_diode,
        "min_on_time_typ_s": t_typ,
        "min_on_time_max_s": t_max,
        "vout_min_v": vout_min,
    }
    numbers = (
        f"온타임은 이상적 듀티(Vout/Vin)로 {ideal * 1e9:.3g} ns, 다이오드 강하 {ASSUMED_DIODE_V:g} V(가정)를 넣으면 "
        f"{with_diode * 1e9:.3g} ns입니다. 데이터시트의 최소 제어 가능 온타임은 전형 {t_typ * 1e9:.3g} ns, 최대 {t_max * 1e9:.3g} ns "
        "(VIN 12 V, 25 °C 조건의 값)입니다."
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
            f"출력에서 전형 {out_typ:.3g} mV, 최대 {out_max:.3g} mV가 됩니다. 입력을 0 V로 해도 최대값 이하의 출력 전압이 나오는 것은 "
            "스펙 안의 동작이라 불량이 아닙니다."
        ),
    }


CHECKS: dict[str, tuple[Callable[..., dict[str, Any] | None], ...]] = {
    "buck": (_current_limit, _measured_ripple, _soft_start, _min_on_time),
    "ldo": (_ldo_thermal,),
    "opamp": (_offset,),
}


def format_facts(facts: list[dict[str, Any]] | None) -> str:
    """프롬프트·메모에 넣을 줄글 목록."""
    return "\n".join(f"- [{f['kind']} / {f['status']}] {f['text']}" for f in facts or [])
