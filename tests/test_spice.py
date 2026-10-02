import json
import os
import sys

import pytest

from fae_agent.config import get_settings
from fae_agent.tools import spice

BASE = {"vout_v": 3.3, "l_h": 4.7e-6, "c_f": 22e-6, "esr_ohm": 0.005, "rbase_ohm": 100.0, "istep_a": 2.0}

SAMPLE_OUTPUT = """
Circuit: * LC output filter load-step test
Doing analysis at TEMP = 27.000000 and TNOM = 27.000000
vmin                =  2.445612e+00 at=  1.320000e-04
vmax                =  4.154321e+00 at=  1.640000e-04
"""


def test_parse_measures_keeps_only_declared_names():
    measures = spice.parse_measures(SAMPLE_OUTPUT, ["vmin", "vmax"])
    assert measures == {"vmin": pytest.approx(2.445612), "vmax": pytest.approx(4.154321)}


def test_render_circuit_fills_every_placeholder():
    netlist = spice.render_circuit("lc_load_step", BASE)
    assert "$" not in netlist
    assert "L1 in out 4.7e-06" in netlist


def test_run_circuit_is_skipped_without_ngspice(monkeypatch):
    monkeypatch.setattr(spice, "find_ngspice", lambda: None)
    result = spice.compare_circuit("lc_load_step", BASE, {**BASE, "c_f": 66e-6})
    assert result["skipped"] is True


@pytest.mark.skipif(os.name == "nt", reason="가짜 실행 파일 테스트는 POSIX에서만")
def test_run_netlist_with_fake_ngspice(tmp_path, monkeypatch):
    fake = tmp_path / "fake_ngspice"
    fake.write_text(f"#!{sys.executable}\nprint('''{SAMPLE_OUTPUT}''')\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("NGSPICE_PATH", str(fake))
    get_settings.cache_clear()
    result = spice.run_circuit("lc_load_step", BASE)
    assert result["ok"] is True
    assert result["undershoot_v"] == pytest.approx(3.3 - 2.445612)


@pytest.mark.skipif(spice.find_ngspice() is None, reason="ngspice 미설치")
def test_lc_load_step_with_real_ngspice():
    result = spice.compare_circuit("lc_load_step", BASE, {**BASE, "c_f": 3 * BASE["c_f"]})
    assert result["before"]["undershoot_v"] > 0
    # Cout 3배 → 특성 임피던스 √(L/C)가 1/√3로 줄어 언더슈트가 크게 준다
    assert result["improvement_pct"] > 10


WRDATA_SAMPLE = """ 0.00000000e+00  3.30000000e+00
 1.00000000e-06  3.30000000e+00
 2.00000000e-06  2.30000000e+00
 not a data line
 4.00000000e-06  3.30000000e+00
"""


def test_parse_wrdata_skips_broken_lines():
    times, values = spice.parse_wrdata(WRDATA_SAMPLE)
    assert times == [0.0, 1e-6, 2e-6, 4e-6]
    assert values == [3.3, 3.3, 2.3, 3.3]


def test_resample_waveform_uses_even_time_grid_in_us():
    times, values = spice.parse_wrdata(WRDATA_SAMPLE)
    wave = spice.resample_waveform(times, values, points=5)
    assert wave["time_us"] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert wave["v"] == [3.3, 3.3, 2.3, 2.8, 3.3]  # 3µs는 2µs(2.3V)와 4µs(3.3V)의 중간


def test_resample_waveform_is_empty_without_enough_points():
    assert spice.resample_waveform([0.0], [1.0]) == {"time_us": [], "v": []}


def test_extract_and_strip_waveforms():
    from fae_agent.waveform import extract_waveforms, strip_waveforms

    wave = {"time_us": [0.0, 1.0], "v": [3.3, 3.2]}
    single = {"waveform": wave, "undershoot_pct": 5.0}
    compared = {"before": {"waveform": wave}, "after": {"waveform": wave}, "improvement_pct": 40.0}
    assert [name for name, _ in extract_waveforms(single)] == ["결과"]
    assert [name for name, _ in extract_waveforms(compared)] == ["변경 전", "변경 후"]
    assert extract_waveforms({"skipped": True}) == []
    stripped = strip_waveforms({"steps": [{"outputs": compared}]})
    assert "waveform" not in str(stripped) and stripped["steps"][0]["outputs"]["improvement_pct"] == 40.0


@pytest.mark.skipif(spice.find_ngspice() is None, reason="ngspice 미설치")
def test_lc_load_step_returns_waveform_matching_measures():
    result = spice.run_circuit("lc_load_step", BASE)
    wave = result["waveform"]
    assert len(wave["time_us"]) == len(wave["v"]) == spice.WAVE_POINTS
    assert wave["ref_v"] == BASE["vout_v"]
    # 다시 뽑은 파형의 최저점은 .meas 최저값에 가깝고, 그보다 낮을 수는 없다
    assert min(wave["v"]) >= result["measures"]["vmin"] - 1e-6
    assert min(wave["v"]) - result["measures"]["vmin"] < 0.2


OPAMP = {"rf_ohm": 9e3, "rg_ohm": 1e3, "gbw_hz": 1e6, "freq_hz": 200e3, "vin_pp": 0.2}  # 이득 10, GBW 1 MHz, 200 kHz


def test_render_opamp_ac_fills_placeholders_and_default_open_loop_gain():
    netlist = spice.render_circuit("opamp_ac", {k: v for k, v in OPAMP.items() if k != "vin_pp"})
    assert "$" not in netlist
    assert ".param a0=100000" in netlist and ".param gbw=1e+06" in netlist  # a0는 기본값(100 dB)
    assert "$" not in spice.render_circuit("opamp_ac", {**OPAMP, "a0": 5.6e5})  # 카드의 개방 이득(115 dB)을 줄 수도 있다
    assert ".param a0=560000" in spice.render_circuit("opamp_ac", {**OPAMP, "a0": 5.6e5})


def test_resample_response_uses_log_grid_and_window():
    freqs, gains = [1.0, 10.0, 100.0, 1000.0], [0.0, -20.0, -40.0, -60.0]  # 한 자리마다 -20 dB
    full = spice.resample_response(freqs, gains, points=4)
    assert full == {"freq_hz": [1.0, 10.0, 100.0, 1000.0], "gain_db": [0.0, -20.0, -40.0, -60.0]}
    part = spice.resample_response(freqs, gains, points=3, f_min=10, f_max=100)
    assert part["freq_hz"] == [10.0, 31.623, 100.0]
    assert part["gain_db"] == [-20.0, -30.0, -40.0]  # 로그 주파수 중간(31.6 Hz)은 dB의 중간
    assert spice.resample_response([1.0], [0.0]) == {"freq_hz": [], "gain_db": []}
    assert spice.resample_response(freqs, gains, f_min=5000) == {"freq_hz": [], "gain_db": []}


def test_extract_and_strip_frequency_responses():
    from fae_agent.waveform import extract_responses, extract_waveforms, strip_waveforms

    response = {"freq_hz": [1.0, 10.0], "gain_db": [20.0, 19.9]}
    single = {"freq_response": response, "gain_at_freq": 4.47}
    compared = {"before": {"freq_response": response}, "after": {"freq_response": response}}
    assert [name for name, _ in extract_responses(single)] == ["결과"]
    assert [name for name, _ in extract_responses(compared)] == ["변경 전", "변경 후"]
    assert extract_responses({"skipped": True}) == [] and extract_waveforms(single) == []  # 두 종류는 섞이지 않는다
    stripped = strip_waveforms({"steps": [{"outputs": single}], "response": {"customer_reply": "안녕"}})
    assert "freq_response" not in str(stripped) and stripped["steps"][0]["outputs"]["gain_at_freq"] == 4.47
    assert stripped["response"] == {"customer_reply": "안녕"}  # 상태의 response(답변 초안)는 건드리지 않는다


@pytest.mark.skipif(spice.find_ngspice() is None, reason="ngspice 미설치")
def test_opamp_ac_reproduces_bandwidth_limit_and_matches_calc():
    """reproduce: 이득 10, GBW 1 MHz의 200 kHz 이득이 줄어드는 것을 시뮬레이션으로 재현하고, 공식 계산과 대조한다."""
    from fae_agent.tools import calc

    result = spice.run_circuit("opamp_ac", OPAMP)
    assert result["ok"] is True and result["returncode"] == 0
    formula = calc.opamp_closed_loop_gain(9e3, 1e3, 1e6, 200e3, vin_pp=0.2)
    assert result["gain_ratio"] < 0.9  # 설정 이득의 90 % 아래로 줄어든다
    # 개방 이득이 유한(100 dB)해서 생기는 0.01~0.02 %의 차이만 있다
    for metric in ("dc_gain", "bw_hz", "gain_at_freq", "gain_ratio", "vout_pp"):
        value = result[metric] if metric in result else result["measures"][metric]
        assert value == pytest.approx(formula[metric], rel=1e-3), metric
    assert result["bw_hz"] == pytest.approx(100e3, rel=1e-3)
    assert result["vout_pp"] == pytest.approx(0.894, abs=1e-3)


@pytest.mark.skipif(spice.find_ngspice() is None, reason="ngspice 미설치")
def test_opamp_ac_verifies_fixes():
    """verify: 주파수를 낮추면 설정 이득이 유지되고(gain_ratio ≥ 0.95), 이득을 낮추면 대역폭이 늘어난다."""
    lower_freq = spice.run_circuit("opamp_ac", {**OPAMP, "freq_hz": 20e3})
    assert lower_freq["gain_ratio"] >= 0.95
    lower_gain = spice.run_circuit("opamp_ac", {**OPAMP, "rf_ohm": 1e3})  # 이득 2
    assert lower_gain["bw_hz"] == pytest.approx(500e3, rel=1e-3)
    assert lower_gain["gain_ratio"] > spice.run_circuit("opamp_ac", OPAMP)["gain_ratio"]


@pytest.mark.skipif(spice.find_ngspice() is None, reason="ngspice 미설치")
def test_opamp_ac_returns_frequency_response_around_the_bandwidth():
    result = spice.run_circuit("opamp_ac", OPAMP)
    response = result["freq_response"]
    assert len(response["freq_hz"]) == len(response["gain_db"]) == spice.RESPONSE_POINTS
    # 측정한 대역폭(100 kHz) 앞뒤 3자리 범위만 남는다
    assert response["freq_hz"][0] == pytest.approx(100, rel=0.02)
    assert response["freq_hz"][-1] == pytest.approx(100e6, rel=0.02)
    assert response["gain_db"][0] == pytest.approx(20.0, abs=0.01) and response["gain_db"][-1] < 0
    assert response["marks"]["freq_hz"] == OPAMP["freq_hz"] and response["marks"]["bw_hz"] == result["bw_hz"]
    assert "waveform" not in result  # 시간 영역 파형과 섞이지 않는다


@pytest.mark.skipif(spice.find_ngspice() is None, reason="ngspice 미설치")
def test_opamp_calc_and_simulation_steps_are_cross_checked_end_to_end():
    """계산 단계와 시뮬레이션 단계를 실제로 실행해 검수의 교차 확인까지 이어 본다."""
    from fae_agent.agents.checker import run_checks
    from fae_agent.agents.qa import run_qa
    from fae_agent.schemas import Bound, CheckStep, Conditions, Diagnosis, Hypothesis, IntakeResult, Solution
    from fae_agent.tools.gateway import ToolGateway

    args = {"rf_ohm": 9e3, "rg_ohm": 1e3, "gbw_hz": 1e6, "freq_hz": 200e3, "vin_pp": 0.2}
    bound = [Bound(metric="gain_ratio", max=0.9)]
    plan = [
        CheckStep(
            label="계산", purpose="reproduce", tool="calc.opamp_closed_loop_gain", expect=bound,
            args_json=json.dumps(args),
        ),
        CheckStep(
            label="시뮬레이션", purpose="reproduce", tool="spice.run_circuit", expect=bound,
            args_json=json.dumps({"circuit": "opamp_ac", "params": args}),
        ),
    ]  # fmt: skip
    solution = Solution(summary="주파수를 낮춘다", test_plan=plan)
    report = run_checks(solution, ToolGateway("local"))
    assert [s.status for s in report.steps] == ["pass", "pass"]
    diagnosis = Diagnosis(hypotheses=[Hypothesis(cause="대역폭 한계", evidence=[])], solution=solution)
    intake = IntakeResult(category="technical", route="fae", conditions=Conditions())
    review = run_qa(intake, {"part_card": {"type": "opamp"}}, diagnosis, report)
    assert "test_mismatch" not in review.issue_codes
    assert any(w.startswith("교차 확인:") and "일치" in w for w in review.warnings)


@pytest.mark.skipif(spice.find_ngspice() is None, reason="ngspice 미설치")
def test_opamp_ac_metrics_are_reachable_by_step_expectations():
    """LLM이 expect에 쓰는 지표 이름(calc와 같은 이름)이 시뮬레이션 결과의 최상위에서 찾아져야 한다."""
    from fae_agent.agents.checker import run_checks
    from fae_agent.schemas import Bound, CheckStep, Solution
    from fae_agent.tools.gateway import ToolGateway

    expect = [
        Bound(metric="bw_hz", min=90e3, max=110e3),
        Bound(metric="gain_at_freq", min=4.0, max=5.0),
        Bound(metric="vout_pp", max=1.0),
    ]
    args = json.dumps({"circuit": "opamp_ac", "params": OPAMP})
    plan = [CheckStep(label="응답", purpose="reproduce", tool="spice.run_circuit", args_json=args, expect=expect)]
    report = run_checks(Solution(summary="s", test_plan=plan), ToolGateway("local"))
    assert report.steps[0].status == "pass", report.steps[0].notes


@pytest.mark.skipif(spice.find_ngspice() is None, reason="ngspice 미설치")
def test_compare_circuit_waveforms_share_time_axis():
    result = spice.compare_circuit("lc_load_step", BASE, {**BASE, "c_f": 3 * BASE["c_f"]})
    assert result["before"]["waveform"]["time_us"] == result["after"]["waveform"]["time_us"]
    assert min(result["after"]["waveform"]["v"]) > min(result["before"]["waveform"]["v"])  # Cout을 키우면 덜 떨어진다


BUCK_PARAMS = {  # 평가 문의 L-02(LM2596, 12→5 V, 1 A)의 조건
    "vin_v": 12, "vout_v": 5, "iout_a": 1.0, "l_h": 33e-6, "fsw_hz": 150e3, "cout_f": 100e-6, "esr_ohm": 0.3,
}  # fmt: skip
BUCK_LIGHT = {**BUCK_PARAMS, "iout_a": 0.1, "cout_f": 220e-6, "esr_ohm": 0.1}  # 같은 부품, 부하가 가벼운 경우
needs_ngspice = pytest.mark.skipif(spice.find_ngspice() is None, reason="ngspice 미설치")


def test_buck_switch_rejects_missing_or_impossible_inputs():
    with pytest.raises(ValueError, match="필요한 값"):
        spice.run_circuit("buck_switch", {"vin_v": 12})
    with pytest.raises(ValueError, match="입력 전압이 출력 전압보다"):
        spice.run_circuit("buck_switch", {**BUCK_PARAMS, "vout_v": 12})
    with pytest.raises(ValueError, match="iout_a"):
        spice.run_circuit("buck_switch", {**BUCK_PARAMS, "iout_a": 0})


def test_buck_switch_is_skipped_without_ngspice(monkeypatch):
    monkeypatch.setattr(spice, "find_ngspice", lambda: None)
    result = spice.run_circuit("buck_switch", BUCK_PARAMS)
    assert result["skipped"] is True and result["circuit"] == "buck_switch"


def test_buck_switch_netlist_pins_the_output_only_while_searching_the_duty():
    p = spice._buck_inputs(BUCK_PARAMS)
    for pinned in (True, False):
        values = {
            **p, "ton_s": 1e-6, "period_s": 6.7e-6, "step_s": 1e-7, "tstart_s": 0.0, "tstop_s": 1e-5,
            "rload_ohm": 5.0, "il0_a": 0.7, "pin_block": "Rpin out vp 0.0001" if pinned else "* 출력 고정 없음",
        }  # fmt: skip
        netlist = spice.render_circuit("buck_switch", values)
        assert "$" not in netlist and ("Rpin out vp" in netlist) is pinned


@needs_ngspice
def test_buck_switch_matches_the_ccm_formula_and_the_measured_case():
    r = spice.run_circuit("buck_switch", BUCK_PARAMS)
    assert r["ok"] and r["converged"] and r["dcm"] == 0
    assert r["vout_avg_v"] == pytest.approx(5.0, rel=0.01)
    # 인덕터 전류 리플: 해석식 (Vout + Vd)(1 − D) / (L fsw), Vd는 기본 다이오드 강하 0.4 V
    expected_il = (5 + 0.4) * (1 - r["duty"]) / (33e-6 * 150e3)
    assert r["delta_il_a"] == pytest.approx(expected_il, rel=0.03)
    # 출력 리플: 평가 문의 L-02의 정답(ESR 0.3 Ω × 0.59 A ≈ 177 mV)과 같은 값
    assert r["ripple_v"] == pytest.approx(0.177, rel=0.1)
    assert r["on_time_s"] == pytest.approx(r["duty"] / 150e3)
    assert r["assumed"] == {"rds_ohm": 0.1, "dcr_ohm": 0.02, "vd_v": 0.4}


@needs_ngspice
def test_buck_switch_ripple_follows_esr_and_capacitance():
    low_esr = spice.run_circuit("buck_switch", {**BUCK_PARAMS, "cout_f": 470e-6, "esr_ohm": 0.02})  # H-L1의 조건
    assert low_esr["ripple_v"] == pytest.approx(0.0128, rel=0.1)
    assert low_esr["ripple_v"] < spice.run_circuit("buck_switch", BUCK_PARAMS)["ripple_v"] / 10


@needs_ngspice
def test_buck_switch_light_load_is_discontinuous_and_matches_the_dcm_formula():
    r = spice.run_circuit("buck_switch", BUCK_LIGHT)
    assert r["ok"] and r["converged"] and r["dcm"] == 1 and r["il_min_a"] == pytest.approx(0.0, abs=1e-3)
    vin, vout, vd, fl = 12, 5, 0.4, 33e-6 * 150e3
    duty = (2 * fl * 0.1 * (vout + vd) / ((vin - vout) * (vin + vd))) ** 0.5  # 불연속 모드 해석식
    assert r["duty"] == pytest.approx(duty, rel=0.03)
    assert r["delta_il_a"] == pytest.approx((vin - vout) * duty / fl, rel=0.03)  # 전류 피크 = 변화폭
    assert r["delta_il_a"] < 0.7 * 0.589  # 연속 모드 식(0.589 A)보다 훨씬 작다


@needs_ngspice
def test_buck_switch_inductor_swap_case_sits_on_the_ccm_dcm_boundary():
    from fae_agent.tools import calc

    swap = {**BUCK_PARAMS, "iout_a": 2.0, "l_h": 4.7e-6, "cout_f": 220e-6, "esr_ohm": 0.1}  # L-03: 인덕터를 4.7 µH로 교체
    r = spice.run_circuit("buck_switch", swap)
    est = calc.buck_ripple_estimate(12, 5, 4.7e-6, 150e3, 220e-6, esr_ohm=0.1, iout_a=2.0)
    assert r["ok"] and r["dcm"] == 1 and est["dcm"] == 1  # 계산도 시뮬레이션도 불연속 경계로 본다
    assert r["delta_il_a"] > 4.0  # 부하 2 A의 두 배가 넘는 전류 변화폭


@needs_ngspice
def test_buck_switch_returns_the_last_cycles_as_waveform():
    r = spice.run_circuit("buck_switch", BUCK_PARAMS)
    wave = r["waveform"]
    assert wave["time_us"][0] == 0 and wave["time_us"][-1] == pytest.approx(10 / 150e3 * 1e6, rel=0.01)  # 10주기
    assert wave["ref_v"] == 5 and max(wave["v"]) - min(wave["v"]) == pytest.approx(r["ripple_v"], rel=0.05)


@needs_ngspice
def test_compare_circuit_reports_ripple_improvement_for_buck_switch():
    result = spice.compare_circuit("buck_switch", BUCK_PARAMS, {**BUCK_PARAMS, "esr_ohm": 0.05})
    assert result["improvement_pct"] > 70  # ESR 0.3 → 0.05 Ω: ESR 성분이 줄어 리플이 크게 준다
    assert result["after_ripple_pct"] == pytest.approx(result["after"]["ripple_pct"])


def buck_review(params: dict, bound):
    """계산 단계와 buck_switch 단계를 실제로 실행하고 검수까지 돌린다."""
    from fae_agent.agents.checker import run_checks
    from fae_agent.agents.qa import run_qa
    from fae_agent.schemas import CheckStep, Conditions, Diagnosis, Hypothesis, IntakeResult, Solution
    from fae_agent.tools.gateway import ToolGateway

    plan = [
        CheckStep(label="계산", purpose="reproduce", tool="calc.buck_ripple_estimate", expect=[bound],
                  args_json=json.dumps(params)),
        CheckStep(label="시뮬레이션", purpose="reproduce", tool="spice.run_circuit", expect=[bound],
                  args_json=json.dumps({"circuit": "buck_switch", "params": params})),
    ]  # fmt: skip
    solution = Solution(summary="ESR이 낮은 커패시터로 교체", test_plan=plan)
    report = run_checks(solution, ToolGateway("local"))
    diagnosis = Diagnosis(hypotheses=[Hypothesis(cause="ESR 성분 리플", evidence=[])], solution=solution)
    intake = IntakeResult(category="technical", route="fae", conditions=Conditions())
    return report, run_qa(intake, {"part_card": {"type": "buck"}}, diagnosis, report)


@needs_ngspice
def test_buck_calc_and_simulation_steps_are_cross_checked_end_to_end():
    from fae_agent.schemas import Bound

    report, review = buck_review(BUCK_PARAMS, Bound(metric="ripple_v", min=0.1))
    assert [s.status for s in report.steps] == ["pass", "pass"], [s.notes for s in report.steps]
    assert "test_mismatch" not in review.issue_codes
    assert any(w.startswith("교차 확인:") and "일치" in w for w in review.warnings)
    assert any("가정했습니다" in w and "vd_v=0.4" in w for w in review.warnings)


@needs_ngspice
def test_buck_light_load_is_flagged_as_discontinuous_in_the_cross_check():
    from fae_agent.schemas import Bound

    report, review = buck_review(BUCK_LIGHT, Bound(metric="ripple_v", min=0.01))
    assert [s.status for s in report.steps] == ["pass", "pass"], [s.notes for s in report.steps]
    assert any(w.startswith("교차 확인 불일치") and "불연속" in w for w in review.warnings)


@needs_ngspice
def test_buck_switch_metrics_are_reachable_by_step_expectations():
    from fae_agent.agents.checker import run_checks
    from fae_agent.schemas import Bound, CheckStep, Solution
    from fae_agent.tools.gateway import ToolGateway

    expect = [Bound(metric=m, min=lo, max=hi) for m, lo, hi in (
        ("ripple_v", 0.1, 0.25), ("ripple_pct", 2.0, 5.0), ("delta_il_a", 0.5, 0.7), ("ripple_ratio", 0.5, 0.7),
        ("duty", 0.4, 0.5), ("on_time_s", 2.5e-6, 3.5e-6), ("dcm", 0, 0), ("vout_avg_v", 4.9, 5.1),
    )]  # fmt: skip
    args = json.dumps({"circuit": "buck_switch", "params": BUCK_PARAMS})
    plan = [CheckStep(label="리플", purpose="reproduce", tool="spice.run_circuit", args_json=args, expect=expect)]
    report = run_checks(Solution(summary="s", test_plan=plan), ToolGateway("local"))
    assert report.steps[0].status == "pass", report.steps[0].notes
