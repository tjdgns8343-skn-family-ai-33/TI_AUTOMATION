import math

import pytest

from fae_agent.tools import calc


def test_buck_output_voltage():
    assert calc.buck_output_voltage(0.6, 45e3, 10e3)["vout_v"] == pytest.approx(3.3)


def test_buck_ripple_estimate():
    r = calc.buck_ripple_estimate(12, 3.3, 4.7e-6, 500e3, 22e-6, esr_ohm=0.005, iout_a=2)
    delta_il = (12 - 3.3) * 0.275 / (4.7e-6 * 500e3)
    assert r["duty"] == pytest.approx(0.275)
    assert r["delta_il_a"] == pytest.approx(delta_il)
    assert r["ripple_cap_v"] == pytest.approx(delta_il / (8 * 500e3 * 22e-6))
    assert r["ripple_esr_v"] == pytest.approx(delta_il * 0.005)
    assert r["ripple_ratio"] == pytest.approx(delta_il / 2)


def test_buck_ripple_rejects_vout_above_vin():
    with pytest.raises(ValueError):
        calc.buck_ripple_estimate(3.3, 5.0, 4.7e-6, 500e3, 22e-6)


def test_lc_resonance():
    r = calc.lc_resonance(4.7e-6, 22e-6)
    assert r["f0_hz"] == pytest.approx(1 / (2 * math.pi * math.sqrt(4.7e-6 * 22e-6)))
    assert r["z0_ohm"] == pytest.approx(math.sqrt(4.7 / 22))


def test_thermal_estimate():
    r = calc.thermal_estimate(15.0, 0.9, 60.0, 40.0)
    assert r["ploss_w"] == pytest.approx(15 * (1 / 0.9 - 1))
    assert r["tj_c"] == pytest.approx(60 + r["ploss_w"] * 40)


def test_ldo_thermal_estimate():
    r = calc.ldo_thermal_estimate(7.0, 3.3, 0.3, 25.0, 180.0)
    assert r["pdiss_w"] == pytest.approx(1.11)
    assert r["tj_c"] == pytest.approx(25 + 1.11 * 180)


def test_i2c_pullup_check():
    r = calc.i2c_pullup_check(3.3, 300e-12, 10e3)
    assert r["r_min_ohm"] == pytest.approx(2.9 / 3e-3)
    assert r["r_max_ohm"] == pytest.approx(300e-9 / (0.8473 * 300e-12))
    assert r["weak_ratio"] > 1  # 10 kΩ은 300 pF 버스에 너무 큼


def test_startup_inrush():
    r = calc.startup_inrush(100e-6, 5.0, 1e-3, iout_a=1.0)
    assert r["i_inrush_a"] == pytest.approx(0.5)
    assert r["i_total_a"] == pytest.approx(1.5)


def test_opamp_closed_loop_gain_reproduces_bandwidth_limit():
    """reproduce: 이득 10, GBW 1 MHz는 폐루프 대역폭 100 kHz. 200 kHz에서는 이득이 약 4.5로 줄어 출력이 약 0.9 Vpp."""
    r = calc.opamp_closed_loop_gain(9e3, 1e3, 1e6, 200e3, vin_pp=0.2, slew_rate_v_per_us=1.7)
    assert r["dc_gain"] == pytest.approx(10)
    assert r["bw_hz"] == pytest.approx(100e3)
    assert r["gain_at_freq"] == pytest.approx(10 / math.sqrt(5))  # 약 4.47
    assert r["gain_ratio"] == pytest.approx(1 / math.sqrt(5)) and r["gain_ratio"] < 0.9
    assert r["vout_pp"] == pytest.approx(0.894, abs=1e-3)
    # 필요한 슬루율은 약 0.56 V/µs로 규격 1.7 V/µs보다 작다 → 슬루율은 원인이 아니다
    assert r["slew_needed_v_per_us"] == pytest.approx(0.562, abs=1e-3)
    assert r["slew_ratio"] == pytest.approx(0.562 / 1.7, abs=1e-3) and r["slew_ratio"] < 1


def test_opamp_closed_loop_gain_verifies_fixes():
    """verify: 주파수를 낮추거나 이득을 낮추면 설정 이득이 유지된다 (gain_ratio ≥ 0.95)."""
    lower_freq = calc.opamp_closed_loop_gain(9e3, 1e3, 1e6, 20e3)  # 이득 10, 20 kHz
    assert lower_freq["gain_ratio"] == pytest.approx(1 / math.sqrt(1 + 0.2**2)) and lower_freq["gain_ratio"] >= 0.95
    lower_gain = calc.opamp_closed_loop_gain(1e3, 1e3, 1e6, 200e3)  # 이득 2, 대역폭 500 kHz, 200 kHz
    assert lower_gain["bw_hz"] == pytest.approx(500e3)
    assert lower_gain["gain_ratio"] == pytest.approx(1 / math.sqrt(1 + 0.4**2))
    assert lower_gain["gain_ratio"] < 0.95  # 이득 2로도 200 kHz에서는 7 % 줄어든다 (검증이 통과 판정을 그냥 주지 않음)


def test_prompt_example_of_opamp_back_calculation_holds():
    """프롬프트(DIAGNOSE_SYSTEM 규칙 5)의 역산식 예가 실제 계산과 맞아야 한다: 이득 20, GBW 1 MHz, 100 kHz."""
    base = calc.opamp_closed_loop_gain(19e3, 1e3, 1e6, 100e3)
    assert base["bw_hz"] == pytest.approx(50e3) and base["gain_ratio"] < 0.95
    factor = math.sqrt(1 / 0.95**2 - 1)  # f ≤ 0.329 × BW
    assert factor == pytest.approx(0.329, abs=1e-3)
    assert 100e3 / factor == pytest.approx(304e3, rel=1e-2)  # BW ≥ 304 kHz가 필요하다
    fixes = {
        "이득 3 이하": calc.opamp_closed_loop_gain(2e3, 1e3, 1e6, 100e3),
        "GBW 6.1 MHz 이상": calc.opamp_closed_loop_gain(19e3, 1e3, 6.1e6, 100e3),
        "주파수 16 kHz 이하": calc.opamp_closed_loop_gain(19e3, 1e3, 1e6, 16e3),
    }
    for name, fix in fixes.items():
        assert fix["gain_ratio"] >= 0.95, name
    # 이득을 절반(10)으로 "조금 낮추는" 것으로는 모자란다 (H-A1 검증에서 실제로 틀린 해결책 유형)
    assert calc.opamp_closed_loop_gain(9e3, 1e3, 1e6, 100e3)["gain_ratio"] < 0.95
    assert calc.opamp_closed_loop_gain(4e3, 1e3, 1e6, 200e3)["gain_ratio"] == pytest.approx(0.7071, abs=1e-4)


def test_prompt_example_of_slew_back_calculation_holds():
    """프롬프트 규칙 5의 슬루율 역산 예: SR 1 V/µs, 4 Vpp → f ≤ 79.6 kHz. 재현은 slew_ratio ≥ 1, 확인은 ≤ 0.8."""
    f_max = 1.0 * 1e6 / (math.pi * 4)
    assert f_max == pytest.approx(79.6e3, rel=1e-3)

    def slew_ratio(freq_hz: float) -> float:  # 이득 2(Rf=Rg), 입력 2 Vpp → 이상 출력 4 Vpp, GBW가 충분히 커서 이득 감소는 무시
        return calc.opamp_closed_loop_gain(1e3, 1e3, 10e6, freq_hz, vin_pp=2.0, slew_rate_v_per_us=1.0)["slew_ratio"]

    assert slew_ratio(100e3) > 1  # f_max를 넘으면 슬루율 한계가 재현된다
    assert 0.99 < slew_ratio(79e3) < 1  # f_max 바로 아래는 한계 직전
    assert slew_ratio(63e3) <= 0.8  # 확인 목표(≤ 0.8)는 f_max의 약 0.8배 = 63.7 kHz 이하에서 만족


def test_prompt_rule_for_ruling_out_slew_rate_by_hand():
    """이상 출력 진폭으로 π × f × Vpp / 1e6 (V/µs)을 어림한 값이 계산 도구의 결과와 맞고, 규격보다 작으면 슬루율은 원인이 아니다."""
    vin_pp, gain, freq, slew_spec = 0.2, 10.0, 200e3, 1.7
    by_hand = math.pi * freq * (vin_pp * gain) / 1e6  # 이상 출력 2 Vpp 기준
    assert by_hand == pytest.approx(1.257, abs=1e-3) and by_hand < slew_spec
    result = calc.opamp_closed_loop_gain(9e3, 1e3, 1e6, freq, vin_pp=vin_pp, slew_rate_v_per_us=slew_spec)
    assert result["slew_needed_v_per_us"] <= by_hand  # 실제 출력은 이상 출력보다 작아서 필요 슬루율도 작다
    assert result["slew_ratio"] < 1


def test_opamp_closed_loop_gain_optional_outputs_and_errors():
    plain = calc.opamp_closed_loop_gain(9e3, 1e3, 1e6, 200e3)
    assert not {"vout_pp", "slew_needed_v_per_us", "slew_ratio"} & set(plain)
    no_slew_spec = calc.opamp_closed_loop_gain(9e3, 1e3, 1e6, 200e3, vin_pp=0.2)
    assert "vout_pp" in no_slew_spec and "slew_ratio" not in no_slew_spec
    with pytest.raises(ValueError):
        calc.opamp_closed_loop_gain(9e3, 1e3, 0, 200e3)
    with pytest.raises(ValueError):
        calc.opamp_closed_loop_gain(9e3, 1e3, 1e6, 200e3, vin_pp=-0.1)


def test_check_value():
    assert calc.check_value(5, max_value=6)["ok"] is True
    assert calc.check_value(7, max_value=6.5)["ok"] is False


def test_invalid_input_raises():
    with pytest.raises(ValueError):
        calc.lc_resonance(0, 22e-6)


def test_calc_metrics_목록이_도구와_일치한다():
    """LLM에게 알려 주는 지표 이름이 모든 calc 도구에 있고, 실제 반환 키를 포함해야 한다."""
    from fae_agent.agents.checker import CALC_METRICS, describe_test_tools
    from fae_agent.tools.calc import CALC_TOOLS, ldo_thermal_estimate

    assert set(CALC_METRICS) == set(CALC_TOOLS)
    assert "지표 pdiss_w, tj_c" in describe_test_tools()
    assert {"pdiss_w", "tj_c"} <= set(ldo_thermal_estimate(7.0, 3.3, 0.3, 25.0, 180.0))


def test_buck_ripple_estimate_reports_on_time_and_conduction_mode():
    r = calc.buck_ripple_estimate(12, 5, 4.7e-6, 150e3, 220e-6, esr_ohm=0.1, iout_a=2.0)
    assert r["on_time_s"] == pytest.approx(5 / 12 / 150e3)
    assert r["iout_crit_a"] == pytest.approx(r["delta_il_a"] / 2)  # 리플의 절반이 임계 부하 전류
    assert r["dcm"] == 1 and "DCM" in r["mode"]  # 2 A < 2.07 A: 연속 모드 식의 전제가 깨진다
    heavy = calc.buck_ripple_estimate(12, 5, 4.7e-6, 150e3, 220e-6, esr_ohm=0.1, iout_a=3.0)
    assert heavy["dcm"] == 0 and heavy["mode"].startswith("CCM")
    no_load = calc.buck_ripple_estimate(12, 5, 4.7e-6, 150e3, 220e-6)  # 부하를 모르면 모드는 판정하지 않는다
    assert "dcm" not in no_load and "on_time_s" in no_load


def test_prompt_example_of_buck_ripple_back_calculation_holds():
    delta_il, fsw, target = 0.5, 300e3, 0.05
    assert target / delta_il == pytest.approx(0.1)  # ESR ≤ ΔV / ΔIL
    assert delta_il / (8 * fsw * target) == pytest.approx(4.2e-6, rel=0.01)  # 용량 성분만 볼 때 Cout ≥ ΔIL / (8 fsw ΔV)
    assert 2 / (400e3 * 150e-9) == pytest.approx(33.3, rel=0.01)  # Vin ≤ Vout / (fsw × 최소 온타임)
