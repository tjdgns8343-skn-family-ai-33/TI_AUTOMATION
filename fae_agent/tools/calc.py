"""데이터시트·앱노트에 나오는 설계 공식을 계산하는 도구.

모든 값은 SI 단위(V, A, W, Ω, F, H, Hz, s, °C)이고, 결과는 dict로 돌려줍니다.
테스트 단계(tester)가 이 결과의 지표(metric)를 기대 범위와 비교합니다.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any


def _positive(**values: float) -> None:
    for name, value in values.items():
        if value is None or value <= 0:
            raise ValueError(f"{name} 값은 0보다 커야 합니다 (받은 값: {value})")


def buck_output_voltage(vref_v: float, r_top_ohm: float, r_bottom_ohm: float) -> dict[str, Any]:
    """피드백 분압으로 정해지는 벅 컨버터 출력 전압. Vout = Vref × (1 + Rtop / Rbottom)."""
    _positive(vref_v=vref_v, r_top_ohm=r_top_ohm, r_bottom_ohm=r_bottom_ohm)
    return {"vout_v": vref_v * (1 + r_top_ohm / r_bottom_ohm)}


def buck_ripple_estimate(
    vin_v: float,
    vout_v: float,
    l_h: float,
    fsw_hz: float,
    cout_f: float,
    esr_ohm: float = 0.0,
    iout_a: float = 0.0,
) -> dict[str, Any]:
    """연속 전도 모드(CCM) 벅의 인덕터 전류 리플과 출력 전압 리플 근사 (iout_a를 주면 불연속 모드인지도 판정).

    ΔIL = (Vin − Vout) × D / (L × fsw), D = Vout / Vin
    ΔVout ≈ ΔIL × ESR + ΔIL / (8 × fsw × Cout)  (두 성분을 더한 보수적 추정)
    부하 전류가 ΔIL/2보다 작으면 인덕터 전류가 0까지 내려가는 불연속 모드(DCM)라서 이 식은 맞지 않는다.
    """
    _positive(vin_v=vin_v, vout_v=vout_v, l_h=l_h, fsw_hz=fsw_hz, cout_f=cout_f)
    if vout_v >= vin_v:
        raise ValueError("벅 컨버터는 Vout < Vin 이어야 합니다.")
    duty = vout_v / vin_v
    delta_il = (vin_v - vout_v) * duty / (l_h * fsw_hz)
    ripple_cap = delta_il / (8 * fsw_hz * cout_f)
    ripple_esr = delta_il * max(esr_ohm, 0.0)
    ripple = ripple_cap + ripple_esr
    result: dict[str, Any] = {
        "duty": duty,
        "delta_il_a": delta_il,
        "ripple_cap_v": ripple_cap,
        "ripple_esr_v": ripple_esr,
        "ripple_v": ripple,
        "ripple_pct": ripple / vout_v * 100,
    }
    result["on_time_s"] = duty / fsw_hz  # 스위치가 켜져 있는 시간. 부품의 최소 온타임보다 짧으면 펄스를 건너뛴다
    if iout_a and iout_a > 0:
        result["ripple_ratio"] = delta_il / iout_a  # 보통 0.2~0.4를 목표로 설계
        result["iout_crit_a"] = delta_il / 2  # 부하가 이 값보다 작으면 불연속 모드
        result["dcm"] = int(iout_a < delta_il / 2)
        result["mode"] = "DCM(불연속, 이 식은 맞지 않음)" if result["dcm"] else "CCM(연속)"
        if result["dcm"]:  # 불연속이면 전류가 0에서 올라가는 삼각파라 피크 = sqrt(2·Iout·Vout·(Vin−Vout) / (L·fsw·Vin)) (이상적 소자)
            result["il_max_a"] = math.sqrt(2 * iout_a * vout_v * (vin_v - vout_v) / (l_h * fsw_hz * vin_v))
        else:  # 연속이면 부하 전류 + 리플 전류의 절반. 부품의 전류 제한과 비교하는 값 (식 IL(max) = IOUT + ΔIL/2)
            result["il_max_a"] = iout_a + delta_il / 2
    return result


def lc_resonance(l_h: float, c_f: float) -> dict[str, Any]:
    """LC 출력 필터의 공진 주파수 f0 = 1 / (2π√(LC))와 특성 임피던스 Z0 = √(L/C)."""
    _positive(l_h=l_h, c_f=c_f)
    return {"f0_hz": 1 / (2 * math.pi * math.sqrt(l_h * c_f)), "z0_ohm": math.sqrt(l_h / c_f)}


def thermal_estimate(pout_w: float, efficiency: float, t_ambient_c: float, theta_ja_c_per_w: float) -> dict[str, Any]:
    """스위칭 컨버터 손실과 접합 온도. Ploss = Pout × (1/η − 1), Tj = Ta + Ploss × θJA."""
    _positive(pout_w=pout_w, theta_ja_c_per_w=theta_ja_c_per_w)
    if not 0 < efficiency <= 1:
        raise ValueError(f"efficiency는 0~1 사이여야 합니다 (받은 값: {efficiency})")
    ploss = pout_w * (1 / efficiency - 1)
    return {"ploss_w": ploss, "tj_c": t_ambient_c + ploss * theta_ja_c_per_w}


def ldo_thermal_estimate(
    vin_v: float,
    vout_v: float,
    iout_a: float,
    t_ambient_c: float,
    theta_ja_c_per_w: float,
    iq_a: float = 0.0,
) -> dict[str, Any]:
    """LDO 손실과 접합 온도. P = (Vin − Vout) × Iout + Vin × Iq, Tj = Ta + P × θJA."""
    _positive(vin_v=vin_v, vout_v=vout_v, iout_a=iout_a, theta_ja_c_per_w=theta_ja_c_per_w)
    if vout_v >= vin_v:
        raise ValueError("LDO는 Vout < Vin 이어야 합니다.")
    pdiss = (vin_v - vout_v) * iout_a + vin_v * max(iq_a, 0.0)
    return {"pdiss_w": pdiss, "tj_c": t_ambient_c + pdiss * theta_ja_c_per_w}


def startup_inrush(cout_f: float, vout_v: float, t_ss_s: float, iout_a: float = 0.0) -> dict[str, Any]:
    """소프트스타트 동안 출력 커패시터 충전 전류. I = Cout × Vout / t_ss (+ 부하 전류)."""
    _positive(cout_f=cout_f, vout_v=vout_v, t_ss_s=t_ss_s)
    inrush = cout_f * vout_v / t_ss_s
    return {"i_inrush_a": inrush, "i_total_a": inrush + max(iout_a, 0.0)}


def i2c_pullup_check(
    vdd_v: float,
    cb_f: float,
    pullup_ohm: float,
    rise_time_s: float = 300e-9,
    vol_max_v: float = 0.4,
    iol_a: float = 3e-3,
) -> dict[str, Any]:
    """I2C 풀업 저항 허용 범위 확인.

    Rmin = (VDD − VOL) / IOL, Rmax = tr / (0.8473 × Cb)
    tr 기본값 300 ns는 Fast-mode(400 kHz) 기준 (Standard-mode는 1000 ns).
    weak_ratio > 1 이면 풀업이 너무 커서 상승이 느림, strong_ratio > 1 이면 너무 작음.
    """
    _positive(vdd_v=vdd_v, cb_f=cb_f, pullup_ohm=pullup_ohm, rise_time_s=rise_time_s, iol_a=iol_a)
    r_min = (vdd_v - vol_max_v) / iol_a
    r_max = rise_time_s / (0.8473 * cb_f)
    return {
        "r_min_ohm": r_min,
        "r_max_ohm": r_max,
        "pullup_ohm": pullup_ohm,
        "weak_ratio": pullup_ohm / r_max,
        "strong_ratio": r_min / pullup_ohm,
        "feasible": r_min <= r_max,
    }


def noninverting_gain(rf_ohm: float, rg_ohm: float) -> dict[str, Any]:
    """비반전 증폭기 이득. G = 1 + Rf / Rg."""
    _positive(rf_ohm=rf_ohm, rg_ohm=rg_ohm)
    return {"gain": 1 + rf_ohm / rg_ohm}


def opamp_closed_loop_gain(
    rf_ohm: float,
    rg_ohm: float,
    gbw_hz: float,
    freq_hz: float,
    vin_pp: float = 0.0,
    slew_rate_v_per_us: float = 0.0,
) -> dict[str, Any]:
    """비반전 증폭기의 이득-대역폭 한계. 폐루프 대역폭 = GBW / 이득, 주파수 f의 이득 = 이득 / √(1 + (f / 대역폭)²).

    gbw_hz, slew_rate_v_per_us는 부품 카드의 typical 값을 쓴다. vin_pp(입력 진폭, Vpp)를 주면 출력 진폭과
    그 출력이 사인파일 때 필요한 슬루율을 함께 계산한다 (slew_ratio > 1이면 슬루율 한계). 1차 모델이라
    대역폭 근처의 위상·피킹은 다루지 않는다.
    """
    _positive(rf_ohm=rf_ohm, rg_ohm=rg_ohm, gbw_hz=gbw_hz, freq_hz=freq_hz)
    if vin_pp < 0 or slew_rate_v_per_us < 0:
        raise ValueError(f"vin_pp, slew_rate_v_per_us 값은 0 이상이어야 합니다 (받은 값: {vin_pp}, {slew_rate_v_per_us})")
    dc_gain = 1 + rf_ohm / rg_ohm
    bw_hz = gbw_hz / dc_gain
    gain_at_freq = dc_gain / math.sqrt(1 + (freq_hz / bw_hz) ** 2)
    result = {
        "dc_gain": dc_gain,
        "bw_hz": bw_hz,
        "gain_at_freq": gain_at_freq,
        "gain_ratio": gain_at_freq / dc_gain,  # 1이면 설정 이득 그대로, 0.7 근처면 대역폭(-3 dB) 부근
    }
    if vin_pp > 0:
        vout_pp = vin_pp * gain_at_freq
        slew_needed = math.pi * freq_hz * vout_pp / 1e6  # 사인파 최대 기울기 2πf × (Vpp/2), V/s → V/µs
        result.update(vout_pp=vout_pp, slew_needed_v_per_us=slew_needed)
        if slew_rate_v_per_us > 0:
            result["slew_ratio"] = slew_needed / slew_rate_v_per_us
    return result


def rc_cutoff(r_ohm: float, c_f: float) -> dict[str, Any]:
    """RC 필터 차단 주파수. fc = 1 / (2πRC)."""
    _positive(r_ohm=r_ohm, c_f=c_f)
    return {"fc_hz": 1 / (2 * math.pi * r_ohm * c_f)}


def check_value(value: float, min_value: float | None = None, max_value: float | None = None) -> dict[str, Any]:
    """값이 범위 안에 있는지 확인 (정격·권장 조건 비교용)."""
    ok = (min_value is None or value >= min_value) and (max_value is None or value <= max_value)
    return {"value": value, "min": min_value, "max": max_value, "ok": ok}


# 게이트웨이·MCP 서버가 쓰는 도구 목록 (이름 → 함수)
CALC_TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    fn.__name__: fn
    for fn in (
        buck_output_voltage,
        buck_ripple_estimate,
        lc_resonance,
        thermal_estimate,
        ldo_thermal_estimate,
        startup_inrush,
        i2c_pullup_check,
        noninverting_gain,
        opamp_closed_loop_gain,
        rc_cutoff,
        check_value,
    )
}
