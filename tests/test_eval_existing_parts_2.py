"""기존 부품으로 만든 새 평가 문의 R-1~R-4의 정답 숫자를 부품 카드와 계산으로 다시 구해 대조한다 (측정 전에 정답을 확정하는 근거)."""

import json
import math
from pathlib import Path

import pytest
import yaml

from fae_agent.agents.intake import classify_rules
from fae_agent.design_check import design_facts
from fae_agent.schemas import Complaint, Conditions, IntakeResult
from fae_agent.tools import calc

ROOT = Path(__file__).resolve().parent.parent
PARTS = ROOT / "data" / "parts"
CASES = {c["id"]: c for c in map(json.loads, (ROOT / "data" / "eval" / "eval_existing_parts_2.jsonl").read_text(encoding="utf-8").splitlines())}


def card(name: str) -> dict:
    return yaml.safe_load((PARTS / f"{name}.yaml").read_text(encoding="utf-8"))


def facts_for(part: str, **conditions):
    intake = IntakeResult(category="technical", route="fae", conditions=Conditions(**conditions))
    call = lambda tool, **kw: calc.CALC_TOOLS[tool.split(".", 1)[1]](**kw)
    return {f["kind"]: f for f in design_facts(intake, card(part), call)}


def gt(case_id: str) -> str:
    return CASES[case_id]["ground_truth"]


def test_four_cases_with_a_rubric_and_a_card_each():
    assert list(CASES) == ["R-1", "R-2", "R-3", "R-4"]
    assert [c["part"] for c in CASES.values()] == ["TPS7A02", "TPS62130A", "LM358", "LM5146"]
    for case in CASES.values():
        assert case["verifiable"] is True and len(case["rubric"]) == 7 and case["rubric"][0].startswith("분류 technical")
        assert case["expected_numbers"] and all("any" in n for n in case["expected_numbers"])
        intake = classify_rules(Complaint(subject=case["subject"], body=case["body"]))
        assert case["part"] in intake.part_numbers and intake.category != "quality_claim"  # 품질 클레임으로 갈리지 않는 문의


def test_r1_thermal_numbers_and_the_peak_current_against_the_card_limits():
    tps7a02 = card("TPS7A02")
    theta = tps7a02["thermal"]["theta_ja_c_per_w"]
    assert theta == 181.9
    power = (5 - 1.8) * 0.15
    tj = 45 + power * theta
    assert power == pytest.approx(0.48) and tj == pytest.approx(132.3, abs=0.1) and tj > tps7a02["recommended"]["tj_max_c"] and tj < tps7a02["typical"]["thermal_shutdown_c"]
    assert (125 - 45) / (theta * 3.2) == pytest.approx(0.137, abs=0.001) and 1.8 + (125 - 45) / (theta * 0.15) == pytest.approx(4.73, abs=0.01)
    assert 45 + (3.3 - 1.8) * 0.15 * theta == pytest.approx(85.9, abs=0.1)
    peak = 0.3
    assert peak > tps7a02["recommended"]["iout_max_a"] == 0.2 and peak > tps7a02["typical"]["current_limit_a_min"] == 0.24  # 카드에 최소 제한 240 mA가 데이터로 있다
    assert (5 - 1.8) * peak == pytest.approx(0.96)
    fact = facts_for("TPS7A02", vin_v=5, vout_v=1.8, iout_a=0.15, ta_c=45, tcase_meas_c=95)["ldo_thermal"]
    assert fact["status"] == "over_recommended" and fact["tj_c"] == pytest.approx(132.3, abs=0.1)
    assert fact["imax_at_tj_rec_a"] == pytest.approx(0.137, abs=0.001) and fact["vin_max_at_tj_rec_v"] == pytest.approx(4.73, abs=0.01)
    assert "접합 온도가 아닙니다" in fact["text"]
    for expected in ("0.48 W", "132 °C", "170 °C", "0.137 A", "4.73 V", "86 °C", "0.96 W", "240 mA", "200 mA"):
        assert expected in gt("R-1")


def test_r2_dropout_numbers_and_the_current_limit_bait():
    tps62130a = card("TPS62130A")
    rds, rl = tps62130a["typical"]["rdson_high_ohm_vin3v"], 0.06
    assert rds == 0.12 and "VOUT + IOUT" in tps62130a["typical"]["vin_min_100pct_duty"]
    assert 3.3 + 2 * (rds + rl) == pytest.approx(3.66)
    assert 3.4 - 2 * (rds + rl) == pytest.approx(3.04) and 2.9 - 2 * (rds + rl) == pytest.approx(2.54)
    assert 3.3 + 0.5 * (rds + rl) == pytest.approx(3.39)
    ripple = calc.buck_ripple_estimate(3.4, 3.3, 2.2e-6, 2.5e6, 44e-6, 0.0, 2.0)
    assert ripple["delta_il_a"] == pytest.approx(0.0176, abs=0.0005) and ripple["il_max_a"] == pytest.approx(2.009, abs=0.001)
    assert ripple["il_max_a"] < tps62130a["typical"]["current_limit_a_min"] == 3.6  # 전류 제한은 미끼
    facts = facts_for("TPS62130A", vin_v=3.4, vin_min_v=2.9, vout_v=3.3, iout_a=2.0, l_h=2.2e-6, fsw_hz=2.5e6, cout_f=44e-6)
    assert facts["rating"]["status"] == "rec_over" and "최저 입력 전압 2.9 V가 권장 최소 3 V보다 작습니다" in facts["rating"]["text"]
    assert facts["current_limit"]["status"] == "ok" and "dropout" not in facts and "max_duty" not in facts  # 이 부품은 드롭아웃 점검이 없다(통제)
    for expected in ("3.66 V", "3.04 V", "2.54 V", "2.01 A", "3.6 A", "3.39 V", "권장 최소 3 V"):
        assert expected in gt("R-2")


def test_r3_offset_swing_and_bandwidth_numbers():
    lm358 = card("LM358")["typical"]
    gain = 1 + 1e6 / 1e3
    assert gain == 1001 and lm358["vos_typ_mv"] == 3 and lm358["vos_max_mv"] == 7
    assert lm358["vos_typ_mv"] * gain / 1e3 == pytest.approx(3.0, abs=0.01) and lm358["vos_max_mv"] * gain / 1e3 == pytest.approx(7.0, abs=0.01)
    assert 3.4 / gain * 1e3 == pytest.approx(3.4, abs=0.01) and 3.4 / gain * 1e3 < lm358["vos_max_mv"]  # 입력 환산 3.4 mV는 스펙 안
    headroom = 5 - lm358["output_swing_high_from_rail_v_5v_2k"]
    assert headroom == pytest.approx(3.5) and headroom - 3.4 == pytest.approx(0.1)
    bandwidth = lm358["gbw_hz"] / gain
    gain_1k = gain / math.sqrt(1 + (1000 / bandwidth) ** 2)
    assert bandwidth == pytest.approx(699, abs=1) and gain_1k == pytest.approx(574, abs=1) and gain_1k / gain == pytest.approx(0.573, abs=0.005)
    assert 1 / (2 * math.pi * 1e3 * 10e-6) == pytest.approx(15.9, abs=0.1) and lm358["gbw_hz"] / math.sqrt(gain) == pytest.approx(22.1e3, rel=0.01)
    fact = facts_for("LM358", rf_ohm=1e6, rg_ohm=1e3, vdd_v=5)["offset"]
    assert fact["offset_out_typ_mv"] == pytest.approx(3003) and fact["offset_out_max_mv"] == pytest.approx(7007)
    assert "전형 3 V, 최대 7.01 V" in fact["text"] and "e+" not in fact["text"]
    for expected in ("3.0 V", "7.0 V", "3.5 V", "699 Hz", "574배", "57 %", "16 Hz", "22 kHz", "3.4 mV"):
        assert expected in gt("R-3")


def test_r4_uvlo_hysteresis_numbers_and_the_ilim_bait():
    lm5146 = card("LM5146")["typical"]
    v_en, i_hys = lm5146["en_operating_threshold_v"], lm5146["en_hysteresis_current_a"]
    assert (v_en, i_hys) == (1.2, 10e-6)
    r_top, r_bottom = 100e3, 6.2e3
    turn_on = v_en * (1 + r_top / r_bottom)
    hysteresis = i_hys * r_top
    assert turn_on == pytest.approx(20.55, abs=0.01) and hysteresis == pytest.approx(1.0) and turn_on - hysteresis == pytest.approx(19.55, abs=0.01)
    assert turn_on - hysteresis > 18.5  # 입력이 18.5 V로 처지면 꺼짐 전압 아래
    low_on, high_on = 1.164 * (1 + r_top / r_bottom), 1.236 * (1 + r_top / r_bottom)  # 데이터시트 VEN 편차와 IHYS 9~11 µA
    assert low_on - 9e-6 * r_top == pytest.approx(19.04, abs=0.01) and high_on - 11e-6 * r_top == pytest.approx(20.07, abs=0.01)
    ruv1 = (20 - 17) / i_hys
    ruv2 = v_en * ruv1 / (20 - v_en)
    assert ruv1 == pytest.approx(300e3) and ruv2 == pytest.approx(19.15e3, rel=0.001)
    facts = facts_for("LM5146", vin_v=24, vout_v=5, iout_a=6, l_h=6.8e-6, fsw_hz=300e3, cout_f=100e-6, rds_low_ohm=2.5e-3, rilim_ohm=200)
    ilim = facts["ilim_valley"]
    assert ilim["status"] == "ok" and ilim["valley_full_load_a"] == pytest.approx(5.03, abs=0.01)
    assert ilim["valley_limit_a"] == pytest.approx(16.0) and ilim["avg_limit_a"] == pytest.approx(16.97, abs=0.01)
    assert facts["min_on_time"]["status"] == "ok" and facts["max_duty"]["status"] == "ok"
    assert "uvlo" not in facts  # UVLO 점검은 없다(통제)
    for expected in ("20.6 V", "1.0 V", "19.6 V", "300 kΩ", "19.1 kΩ", "16 A", "5.03 A", "694 ns", "18.5 V"):
        assert expected in gt("R-4")
