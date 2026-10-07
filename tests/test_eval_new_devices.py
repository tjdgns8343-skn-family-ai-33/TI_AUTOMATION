"""새 부품(LM5146, BQ25895) 평가 문의 Q-1~Q-4의 정답 숫자를 부품 카드와 계산으로 다시 구해 대조한다 (측정 전에 정답을 확정하는 근거)."""

import json
import math
from pathlib import Path

import pytest
import yaml

from fae_agent.agents.intake import classify_rules
from fae_agent.schemas import Complaint
from fae_agent.tools import calc

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "data" / "eval" / "eval_new_devices.jsonl"
CASES = {c["id"]: c for c in map(json.loads, EVAL.read_text(encoding="utf-8").splitlines())}
LM5146 = yaml.safe_load((ROOT / "data" / "parts" / "LM5146.yaml").read_text(encoding="utf-8"))
BQ25895 = yaml.safe_load((ROOT / "data" / "parts" / "BQ25895.yaml").read_text(encoding="utf-8"))


def gt(case_id: str) -> str:
    return CASES[case_id]["ground_truth"]


def test_four_cases_with_a_rubric_and_a_card_each():
    assert list(CASES) == ["Q-1", "Q-2", "Q-3", "Q-4"]
    for case in CASES.values():
        assert case["part"] in ("LM5146", "BQ25895") and case["verifiable"] is True
        assert len(case["rubric"]) == 7 and case["rubric"][0].startswith("분류 technical")
        assert case["expected_numbers"] and all("any" in n for n in case["expected_numbers"])
        intake = classify_rules(Complaint(subject=case["subject"], body=case["body"]))
        assert intake.category == "technical" and case["part"] in intake.part_numbers  # 품질 클레임으로 갈리지 않는 문의


def test_q1_ilim_valley_limit_hiccup_period_and_soft_start_numbers():
    ripple = calc.buck_ripple_estimate(48, 12, 10e-6, 400e3, 188e-6, 0.0, 10)
    delta_il = ripple["delta_il_a"]
    typical = LM5146["typical"]
    valley_full_load = 10 - delta_il / 2
    assert delta_il == pytest.approx(2.25) and valley_full_load == pytest.approx(8.875)
    valley_limit = typical["ilim_current_rdson_a"] * 100 / 3e-3  # RILIM 100 Ω, 하단 FET 3 mΩ
    assert valley_limit == pytest.approx(6.667, abs=0.001)
    assert valley_limit + delta_il / 2 == pytest.approx(7.79, abs=0.01)  # 이 부하부터 한계에 걸린다
    needed = valley_full_load * 3e-3 / typical["ilim_current_rdson_a"]  # 식 (6)
    assert needed == pytest.approx(133.1, abs=0.1)
    assert 6e-9 / 133 == pytest.approx(45e-12, rel=0.01)  # RILIM × CILIM ≈ 6 ns
    on_ms = typical["hiccup_delay_cycles"] / 400e3 * 1e3
    off_ms = typical["hiccup_off_cycles"] / 400e3 * 1e3
    assert on_ms == pytest.approx(0.32) and off_ms == pytest.approx(20.48)  # 고객 관찰 약 0.3 ms / 21 ms
    tss = 47e-9 * typical["ss_ramp_v"] / typical["ss_charge_current_a"]
    assert tss == pytest.approx(3.76e-3)
    assert calc.startup_inrush(188e-6, 12, tss)["i_inrush_a"] == pytest.approx(0.6)  # 한계보다 훨씬 작다: 미끼
    vcc_measured = 15 - 0.6  # 보조 15 V에서 다이오드 0.6 V 강하
    assert vcc_measured == pytest.approx(14.4) and vcc_measured > LM5146["abs_max"]["vcc_v"] == 14
    assert LM5146["recommended"]["vcc_ext_max_v"] == 13
    expected_in_text = ("6.67 A", "7.8 A", "2.25 A", "8.875 A", "133 Ω", "20.5 ms", "0.32 ms", "3.76 ms", "0.6 A", "14 V", "45 pF")
    for expected in expected_in_text:
        assert expected in gt("Q-1")


def test_q2_maximum_duty_from_the_minimum_off_time_matches_the_datasheet_table():
    t_off_typ, t_off_max = LM5146["typical"]["min_off_time_typ_s"], LM5146["typical"]["min_off_time_s"]
    # 식이 데이터시트 표의 최대 듀티(100 kHz 최소 98 %·전형 99 %, 400 kHz 최소 90 %·전형 94 %)와 맞는지 확인한다
    assert 1 - t_off_typ * 100e3 == pytest.approx(0.986, abs=0.001) and 1 - t_off_max * 100e3 >= LM5146["typical"]["max_duty_100khz_min"]
    assert 1 - t_off_typ * 400e3 == pytest.approx(0.944, abs=0.001) and 1 - t_off_max * 400e3 >= LM5146["typical"]["max_duty_400khz_min"]
    d_typ, d_worst = 1 - t_off_typ * 750e3, 1 - t_off_max * 750e3
    assert d_typ == pytest.approx(0.895) and d_worst == pytest.approx(0.85)
    assert 5 / 5.5 == pytest.approx(0.909, abs=0.001) and 5 / 6 < d_worst < d_typ < 5 / 5.5  # 6 V는 겨우 유지, 5.5 V는 불가
    assert 5.5 * d_worst == pytest.approx(4.675) and 5.5 * d_typ == pytest.approx(4.9225)
    assert 1 - t_off_max * 250e3 == pytest.approx(0.95) and LM5146["typical"]["max_duty_400khz_min"] < 5 / 5.5  # 400 kHz(최소 90 %)도 모자람
    on_time = 5 / 12 / 750e3
    assert on_time == pytest.approx(556e-9, rel=0.01) and on_time > 9 * LM5146["typical"]["min_on_time_s"]  # 최소 온타임은 미끼
    assert 40e-9 * 750e3 == pytest.approx(0.030)  # 게이트 구동 전류 30 mA (VCC 시험 조건 20 mA보다 큼)
    for expected in ("89.5 %", "85 %", "90.9 %", "556 ns", "5.8 V", "30 mA", "250 kHz"):
        assert expected in gt("Q-2")


def test_q2_min_on_time_design_fact_for_the_lm5146_card_says_the_on_time_is_fine():
    from fae_agent.design_check import design_facts
    from fae_agent.schemas import Conditions, IntakeResult

    intake = IntakeResult(
        category="technical", route="fae", conditions=Conditions(vin_v=12, vout_v=5, fsw_hz=750e3, iout_a=3, l_h=2.2e-6)
    )
    facts = {f["kind"]: f for f in design_facts(intake, LM5146, lambda tool, **kw: calc.CALC_TOOLS[tool.split(".", 1)[1]](**kw))}
    assert facts["min_on_time"]["status"] == "ok" and "current_limit" not in facts


def test_q3_ilim_pin_limit_and_the_charge_current_the_input_power_allows():
    k_min, k_max = BQ25895["typical"]["kilim_a_ohm_min"], BQ25895["typical"]["kilim_a_ohm_max"]
    assert (k_min, k_max) == (320, 390)
    assert 355 / 470 == pytest.approx(0.755, abs=0.001) and k_min / 470 == pytest.approx(0.681, abs=0.001) and k_max / 470 == pytest.approx(0.830, abs=0.001)
    assert 355 / 2 == pytest.approx(177.5) and k_min / 177.5 == pytest.approx(1.80, abs=0.01) and k_max / 177.5 == pytest.approx(2.20, abs=0.01)
    assert BQ25895["typical"]["iinlim_default_a"] == 0.5 and BQ25895["typical"]["regn_enable_delay_s"] == 0.22
    p_in = 9 * 0.755
    assert p_in == pytest.approx(6.8, abs=0.01)
    charge_a = (p_in * 0.9 - 0.3 * 3.9) / 3.8  # 효율 90 %, 시스템 0.3 A × 3.9 V, 배터리 3.8 V (가정)
    assert charge_a == pytest.approx(1.3, abs=0.02)  # 고객 관찰 약 1.3 A
    assert (3 * 3.8 + 0.3 * 3.9) / 0.9 / 9 == pytest.approx(1.55, abs=0.01)  # 3 A 충전에 필요한 입력 전류
    assert (2000 - 100) / 50 == 38  # IINLIM 2.0 A는 레지스터 값 38
    for expected in ("0.755 A", "177 Ω", "1.3 A", "6.8 W", "1.55 A", "500 mA"):
        assert expected in gt("Q-3")


def test_q4_ts_thresholds_watchdog_defaults_and_the_datasheet_divider_example():
    typical = BQ25895["typical"]
    assert typical["watchdog_default_s"] == 40 and typical["ichg_default_a"] == 2.048 and typical["vreg_default_v"] == 4.208
    assert (4352 - 3840) / 16 == 32  # VREG 4.352 V는 레지스터 값 100000
    assert 3008 / 64 == 47  # ICHG 3.008 A는 64 mA 단계의 정수배
    pct = {"vltf": typical["ts_cold_threshold_pct"] / 100, "vhtf": typical["ts_hot_threshold_pct"] / 100, "vtco": typical["ts_cutoff_pct"] / 100}
    rth = {name: 10e3 * p / (1 - p) for name, p in pct.items()}  # REGN-TS 10 kΩ만 있을 때의 문턱 저항
    assert rth["vltf"] == pytest.approx(27.38e3, rel=0.002) and rth["vhtf"] == pytest.approx(9.324e3, rel=0.002) and rth["vtco"] == pytest.approx(8.10e3, rel=0.002)
    beta = math.log(27.28 / 4.91) / (1 / 273.15 - 1 / 318.15)  # 데이터시트 103AT 두 점(0 °C 27.28 kΩ, 45 °C 4.91 kΩ)으로 맞춘 β

    def temp_c(r_ohm: float) -> float:  # 25 °C에서 10 kΩ인 NTC (103AT의 정의)
        return 1 / (1 / 298.15 + math.log(r_ohm / 10e3) / beta) - 273.15

    assert temp_c(rth["vhtf"]) == pytest.approx(26.9, abs=0.2) and temp_c(rth["vtco"]) == pytest.approx(30.8, abs=0.2)
    assert temp_c(rth["vltf"]) == pytest.approx(0.2, abs=0.3)  # 저온 문턱은 우연히 약 0 °C로 맞는다
    rt1, rt2 = 5.21e3, 29.87e3  # 데이터시트 §8.2.7.5.1 예 (0~45 °C)
    for r_ohm, expected in ((27.28e3, 0.7325), (4.91e3, 0.4475)):
        parallel = rt2 * r_ohm / (rt2 + r_ohm)
        assert parallel / (rt1 + parallel) == pytest.approx(expected, abs=0.002)
    for expected in ("40 s", "4.208 V", "2.048 A", "9.32 kΩ", "8.10 kΩ", "27 °C", "31 °C", "5.21 kΩ", "29.87 kΩ", "73.2 %", "44.7 %", "WD_RST"):
        assert expected in gt("Q-4")
