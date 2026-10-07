"""설계 점검 확대 2: ILIM 골짜기 한계, 최대 듀티, 확장 정격, 충전기 입력 전류·충전 전력·TS 창 (평가 문의 Q-1~Q-4의 정답 숫자와 대조)."""

import json
from pathlib import Path

import pytest
import yaml

from fae_agent.agents.intake import extract_conditions
from fae_agent.agents.qa import run_qa
from fae_agent.design_check import COVERAGE, design_facts, missing_causes
from fae_agent.schemas import CheckReport, Conditions, Diagnosis, Evidence, Hypothesis, IntakeResult, Solution
from fae_agent.tools.calc import CALC_TOOLS

ROOT = Path(__file__).resolve().parent.parent
LM5146 = yaml.safe_load((ROOT / "data" / "parts" / "LM5146.yaml").read_text(encoding="utf-8"))
BQ25895 = yaml.safe_load((ROOT / "data" / "parts" / "BQ25895.yaml").read_text(encoding="utf-8"))
TPS54331 = yaml.safe_load((ROOT / "data" / "parts" / "TPS54331.yaml").read_text(encoding="utf-8"))
CASES = {c["id"]: c for c in map(json.loads, (ROOT / "data" / "eval" / "eval_new_devices.jsonl").read_text(encoding="utf-8").splitlines())}
Q1 = {"vin_v": 48.0, "vout_v": 12.0, "iout_a": 10.0, "l_h": 10e-6, "fsw_hz": 400e3, "cout_f": 188e-6, "rds_low_ohm": 3e-3, "rilim_ohm": 100.0}
Q2 = {"vin_v": 12.0, "vin_min_v": 5.5, "vout_v": 5.0, "iout_a": 3.0, "fsw_hz": 750e3}
Q3 = {"vin_v": 9.0, "rilim_ohm": 470.0, "iinlim_a": 2.0, "ichg_set_a": 3.0, "vbat_v": 3.8, "isys_a": 0.3}
Q4 = {"rt1_ohm": 10e3, "ntc_r25_ohm": 10e3}


def call(tool: str, **kwargs):
    return CALC_TOOLS[tool.split(".", 1)[1]](**kwargs)


def intake(**conditions) -> IntakeResult:
    return IntakeResult(category="technical", route="fae", conditions=Conditions(**conditions))


def facts_for(card, **conditions):
    return {f["kind"]: f for f in design_facts(intake(**conditions), card, call)}


# ---------------------------------------------------------------- ILIM 골짜기


def test_ilim_valley_for_q1_matches_the_ground_truth_numbers():
    fact = facts_for(LM5146, **Q1)["ilim_valley"]
    assert fact["status"] == "too_low"
    assert fact["delta_il_a"] == pytest.approx(2.25) and fact["valley_full_load_a"] == pytest.approx(8.875)
    assert fact["valley_limit_a"] == pytest.approx(6.667, abs=0.001) and fact["avg_limit_a"] == pytest.approx(7.79, abs=0.01)
    assert fact["needed_rilim_ohm"] == pytest.approx(133.1, abs=0.1)
    assert fact["needed_rilim_low_ohm"] == pytest.approx(121.0, abs=0.1) and fact["needed_rilim_high_ohm"] == pytest.approx(147.9, abs=0.1)
    for expected in ("6.67 A", "7.79 A", "133 Ω", "0.32 ms", "20.5 ms", "20.8 ms", "인덕터를 키우면", "ILIM 저항을 키워야", "45 pF"):
        assert expected in fact["text"]
    assert "\n" not in fact["text"]


def test_ilim_valley_other_statuses_and_when_it_is_skipped():
    assert facts_for(LM5146, **{**Q1, "rilim_ohm": 200.0})["ilim_valley"]["status"] == "ok"  # 골짜기 한계 13.3 A
    marginal = facts_for(LM5146, **{**Q1, "rilim_ohm": 140.0})["ilim_valley"]  # 전형은 10.46 A지만 IRDSON 최소에서는 9.5 A
    assert marginal["status"] == "marginal" and marginal["avg_limit_min_a"] < 10 < marginal["avg_limit_a"]
    without_r = {k: v for k, v in Q1.items() if k != "rilim_ohm"}
    info = facts_for(LM5146, **without_r)["ilim_valley"]
    assert info["status"] == "info" and "133 Ω" in info["text"]  # 저항을 모르면 필요한 값만 알려 준다
    assert "ilim_valley" not in facts_for(LM5146, **{k: v for k, v in Q1.items() if k != "rds_low_ohm"})  # RDS(on)을 모르면 점검 안 함
    assert "ilim_valley" not in facts_for(LM5146, **{**Q1, "iout_a": 0.5})  # 불연속(골짜기 0 A)이면 비교 안 함
    assert "ilim_valley" not in facts_for(TPS54331, **Q1)  # 카드에 ILIM 전류가 없으면 점검 안 함


# ---------------------------------------------------------------- 최대 듀티


def test_max_duty_for_q2_cannot_regulate_at_the_minimum_input():
    fact = facts_for(LM5146, **Q2)["max_duty"]
    assert fact["status"] == "cannot_regulate"
    assert fact["duty_required"] == pytest.approx(0.909, abs=0.001)
    assert fact["duty_max_typ"] == pytest.approx(0.895) and fact["duty_max_worst"] == pytest.approx(0.85)
    assert fact["vout_possible_low_v"] == pytest.approx(4.675) and fact["vout_possible_high_v"] == pytest.approx(4.9225)
    assert fact["vin_needed_typ_v"] == pytest.approx(5.59, abs=0.01) and fact["vin_needed_worst_v"] == pytest.approx(5.88, abs=0.01)
    assert fact["fsw_recommended_hz"] == pytest.approx(305e3, rel=0.01)  # 최악 듀티가 필요 듀티보다 3 %p 높아지는 주파수
    for expected in ("90.9 %", "89.5 %", "85 %", "4.67~4.92 V", "305 kHz", "거의 100 % 듀티", "100 kHz 최소 98 %", "400 kHz 최소 90 %"):
        assert expected in fact["text"]


def test_max_duty_other_statuses_and_the_card_without_off_time():
    assert facts_for(LM5146, **{**Q2, "vin_min_v": 6.0})["max_duty"]["status"] == "ok"  # 필요 83.3 % < 최악 85 %
    assert facts_for(LM5146, **{**Q2, "vin_min_v": 5.8})["max_duty"]["status"] == "marginal"  # 86.2 %: 전형 89.5 %와 최악 85 % 사이
    assert facts_for(LM5146, **{**Q2, "vin_min_v": 5.5, "fsw_hz": 250e3})["max_duty"]["status"] == "ok"  # 낮은 주파수면 유지
    nominal = facts_for(LM5146, **{k: v for k, v in Q2.items() if k != "vin_min_v"})["max_duty"]  # 최저 입력을 모르면 공칭 입력
    assert nominal["status"] == "ok" and "최저 입력" not in nominal["text"]
    sync_free = facts_for(TPS54331, vin_v=5.0, vout_v=5.0, fsw_hz=570e3)["max_duty"]  # 최대 듀티만 있는 카드: 전형이 없어 marginal까지만
    assert sync_free["status"] == "marginal" and "최소 90 %" in sync_free["text"] and sync_free["duty_max_typ"] is None


def test_min_on_time_text_uses_microseconds_for_long_on_times():
    fact = facts_for(LM5146, vin_v=5.5, vout_v=5.0, fsw_hz=750e3, iout_a=3, l_h=2.2e-6)["min_on_time"]
    assert "1.21 µs" in fact["text"] and "e+" not in fact["text"]


# ---------------------------------------------------------------- 정격 (VCC 포함)


def test_rating_fact_catches_vcc_above_the_absolute_maximum_and_does_not_flag_the_internal_regulator():
    fact = facts_for(LM5146, vin_v=48, vout_v=12, vcc_v=14.4)["rating"]
    assert fact["status"] == "abs_over" and "VCC 전압 14.4 V가 절대 최대 정격 14 V(권장 최대 13 V)를 넘습니다" in fact["text"]
    assert "rating" not in facts_for(LM5146, vin_v=48, vout_v=12, vcc_v=13.0)  # 권장 최대 13 V 이하
    assert "rating" not in facts_for(LM5146, vin_v=48, vout_v=12, vcc_v=7.5)  # 내부 레귤레이터 값은 외부 바이어스 하한 8 V와 비교하지 않는다
    low = facts_for(LM5146, vin_v=48, vout_v=12, vin_min_v=4.0)["rating"]
    assert low["status"] == "rec_over" and "최저 입력 전압 4 V가 권장 최소 5.5 V보다 작습니다" in low["text"]


def test_qa_does_not_duplicate_rating_facts_but_still_demands_the_rating_cause():
    def diagnosis(cause: str) -> Diagnosis:
        return Diagnosis(hypotheses=[Hypothesis(cause=cause, evidence=[Evidence(source="lm5146.pdf")])], solution=Solution(summary="해결"))

    design = design_facts(intake(vin_v=48, vout_v=12, vcc_v=14.4), LM5146, call)
    review = run_qa(intake(vin_v=48, vout_v=12, vcc_v=14.4), {"part_card": LM5146}, diagnosis("출력 커패시터"), CheckReport(status="skipped"), design=design)
    assert "rating_not_addressed" in review.issue_codes and "design_not_addressed" not in review.issue_codes
    assert not any(w.startswith("설계 점검: 정격 점검") for w in review.warnings)  # 경고는 정격 점검 쪽 한 곳만
    covered = run_qa(intake(vin_v=48, vout_v=12, vcc_v=14.4), {"part_card": LM5146}, diagnosis("VCC가 절대 최대 정격 초과"), CheckReport(status="skipped"), design=design)
    assert "rating_not_addressed" not in covered.issue_codes


# ---------------------------------------------------------------- 충전기


def test_input_current_limit_for_q3_pin_limits_below_the_register():
    fact = facts_for(BQ25895, **Q3)["input_current_limit"]
    assert fact["status"] == "pin_limits"
    assert fact["pin_limit_a"] == pytest.approx(0.755, abs=0.001)
    assert fact["pin_limit_min_a"] == pytest.approx(0.681, abs=0.001) and fact["pin_limit_max_a"] == pytest.approx(0.830, abs=0.001)
    assert fact["rilim_for_register_ohm"] == pytest.approx(177.5)
    for expected in ("355 ÷ 470 = 0.755 A", "0.681~0.83 A", "낮은 값", "1.8~2.2 A"):
        assert expected in fact["text"]


def test_input_current_limit_other_statuses():
    low_register = facts_for(BQ25895, **{**Q3, "iinlim_a": 0.5})["input_current_limit"]  # 레지스터 500 mA가 핀 0.68 A보다 낮다
    assert low_register["status"] == "register_limits"
    similar = facts_for(BQ25895, **{**Q3, "iinlim_a": 0.75})["input_current_limit"]
    assert similar["status"] == "ok"
    info = facts_for(BQ25895, **{k: v for k, v in Q3.items() if k != "iinlim_a"})["input_current_limit"]
    assert info["status"] == "info" and "0.755 A" in info["text"]
    assert "input_current_limit" not in facts_for(BQ25895, **{k: v for k, v in Q3.items() if k != "rilim_ohm"})


def test_charge_power_for_q3_is_about_1_3_amps_and_needs_1_55_amps_for_3_amps():
    fact = facts_for(BQ25895, **Q3)["charge_power"]
    assert fact["status"] == "power_limited"
    assert fact["p_in_w"] == pytest.approx(6.8, abs=0.01) and fact["ichg_possible_a"] == pytest.approx(1.31, abs=0.01)
    assert fact["iin_needed_a"] == pytest.approx(1.55, abs=0.01)
    assert "효율 90 %(가정)" in fact["text"] and "설정을 올려도" in fact["text"]
    ok = facts_for(BQ25895, **{**Q3, "ichg_set_a": 1.0})["charge_power"]
    assert ok["status"] == "ok"
    no_sys = facts_for(BQ25895, **{k: v for k, v in Q3.items() if k != "isys_a"})["charge_power"]
    assert "시스템 부하는 고객이 알려 주지 않아 0으로 두었습니다(가정)" in no_sys["text"]
    register_only = facts_for(BQ25895, vin_v=9.0, iinlim_a=0.5, ichg_set_a=3.0, vbat_v=3.8)["charge_power"]  # 핀 저항 없이 레지스터만
    assert register_only["iin_limit_a"] == 0.5 and register_only["status"] == "power_limited"
    assert "charge_power" not in facts_for(BQ25895, vin_v=9.0, vbat_v=3.8, ichg_set_a=3.0)  # 입력 전류 한계를 모르면 점검 안 함


def test_ts_window_for_q4_narrows_the_hot_side_to_about_31_degrees():
    fact = facts_for(BQ25895, **Q4)["ts_window"]
    assert fact["status"] == "hot_low"
    assert fact["rth_cold_ohm"] == pytest.approx(27.38e3, rel=0.002) and fact["rth_hot_ohm"] == pytest.approx(9.324e3, rel=0.002)
    assert fact["rth_cutoff_ohm"] == pytest.approx(8.10e3, rel=0.002)
    assert fact["t_cold_c"] == pytest.approx(0.2, abs=0.3) and fact["t_hot_c"] == pytest.approx(26.9, abs=0.2) and fact["t_cutoff_c"] == pytest.approx(30.8, abs=0.2)
    for expected in ("RT2 없음", "9.32 kΩ", "8.1 kΩ", "31 °C", "추정", "STAT가 1 Hz로 깜빡입니다"):
        assert expected in fact["text"]


def test_ts_window_with_the_datasheet_example_divider_is_the_0_to_45_degree_window():
    fact = facts_for(BQ25895, rt1_ohm=5.21e3, rt2_ohm=29.87e3, ntc_r25_ohm=10e3)["ts_window"]
    assert fact["status"] == "ok" and fact["t_cold_c"] == pytest.approx(0.0, abs=0.5) and fact["t_cutoff_c"] == pytest.approx(45.0, abs=0.5)
    assumed = facts_for(BQ25895, rt1_ohm=10e3)["ts_window"]  # NTC 저항을 모르면 103AT(25 °C에서 10 kΩ)로 본다
    assert assumed["t_cutoff_c"] == pytest.approx(30.8, abs=0.2)
    assert "ts_window" not in facts_for(BQ25895, rt1_ohm=10e3, rt2_ohm=5e3)  # 병렬 저항이 RT2보다 클 수 없는 분압은 계산 불가
    assert "ts_window" not in facts_for(BQ25895, ntc_r25_ohm=10e3)  # RT1을 모르면 점검 안 함


# ---------------------------------------------------------------- COVERAGE와 검수


def hypothesis_diagnosis(cause: str, mechanism: str = "") -> Diagnosis:
    h = Hypothesis(cause=cause, mechanism=mechanism, evidence=[Evidence(source="lm5146.pdf")])
    return Diagnosis(hypotheses=[h], solution=Solution(summary="해결"))


def test_coverage_requires_the_new_causes_and_accepts_synonyms():
    q1 = design_facts(intake(**Q1), LM5146, call)
    assert [f["kind"] for f, _ in missing_causes(q1, "출력 커패시터 부족")] == ["ilim_valley"]
    assert missing_causes(q1, "ILIM 저항이 작아 히컵이 반복된다") == []
    assert [f["kind"] for f, _ in missing_causes(q1, "큰 출력 커패시터와 짧은 소프트 스타트로 기동 중 과전류 보호가 동작(hiccup)")] == ["ilim_valley"]  # 증상 말만으로는 통과하지 않는다
    q2 = design_facts(intake(**Q2), LM5146, call)
    assert [f["kind"] for f, _ in missing_causes(q2, "FET 발열")] == ["max_duty"]
    assert missing_causes(q2, "최대 듀티 한계로 낮은 입력에서 출력이 처진다") == []
    q3 = design_facts(intake(**Q3), BQ25895, call)
    assert sorted(f["kind"] for f, _ in missing_causes(q3, "감시 타이머 만료")) == ["charge_power", "input_current_limit"]
    assert missing_causes(q3, "ILIM 핀 저항이 입력 전류를 제한하고 입력 전력도 모자람") == []
    q4 = design_facts(intake(**Q4), BQ25895, call)
    assert [f["kind"] for f, _ in missing_causes(q4, "감시 타이머 만료")] == ["ts_window"]
    assert missing_causes(q4, "TS 분압이 NTC 온도 창을 좁힘") == []
    assert missing_causes(q4, "tests가 results를 보고한다") != []  # 'ts' 같은 짧은 조각이 다른 낱말에 걸려 통과하지 않는다
    for (kind, status), (title, words, target, after) in COVERAGE.items():
        assert kind and status and title and words and target and after and all(w == w.lower() for w in words)


def test_qa_sends_back_a_diagnosis_that_misses_the_ilim_cause():
    design = design_facts(intake(**Q1), LM5146, call)
    review = run_qa(intake(**Q1), {"part_card": LM5146}, hypothesis_diagnosis("소프트 스타트 커패시터가 작음"), CheckReport(status="skipped"), design=design)
    assert "design_not_addressed" in review.issue_codes and any("ILIM 저항이 너무 작아" in i for i in review.issues)
    covered = run_qa(intake(**Q1), {"part_card": LM5146}, hypothesis_diagnosis("ILIM 저항 부족으로 히컵"), CheckReport(status="skipped"), design=design)
    assert "design_not_addressed" not in covered.issue_codes and any("골짜기 전류 한계" in w for w in covered.warnings)


# ---------------------------------------------------------------- 접수 규칙 (새 조건)


def test_rule_based_intake_reads_the_new_conditions_from_q1_to_q4():
    q1 = extract_conditions(CASES["Q-1"]["subject"] + "\n" + CASES["Q-1"]["body"])
    assert q1.vcc_v == pytest.approx(14.4) and q1.rilim_ohm == pytest.approx(100.0) and q1.rds_low_ohm == pytest.approx(3e-3)
    q2 = extract_conditions(CASES["Q-2"]["body"])
    assert q2.vin_min_v == pytest.approx(5.5)
    q3 = extract_conditions(CASES["Q-3"]["body"])
    assert (q3.vin_v, q3.rilim_ohm, q3.iinlim_a, q3.ichg_set_a, q3.vbat_v, q3.isys_a) == (9.0, 470.0, 2.0, 3.0, 3.8, 0.3)
    q4 = extract_conditions(CASES["Q-4"]["body"])
    assert q4.rt1_ohm == pytest.approx(10e3) and q4.ntc_r25_ohm == pytest.approx(10e3) and q4.ichg_set_a == pytest.approx(3.008)
    assert q4.rt2_ohm is None  # 'RT2는 없음'은 값이 아니다
    assert extract_conditions("ILIM 핀 근처에서 100kHz 노이즈가 보입니다").rilim_ohm is None  # kHz를 kΩ으로 읽지 않는다
    assert extract_conditions("전류 제한 저항 2.2kΩ").rilim_ohm == pytest.approx(2.2e3)


# ---------------------------------------------------------------- mock 흐름


def run_mock_case(case_id: str) -> dict:
    from fae_agent.graph import build_graph, new_thread_id, start_run
    from fae_agent.schemas import Complaint

    case = CASES[case_id]
    return start_run(build_graph(), Complaint(subject=case["subject"], body=case["body"]), new_thread_id())


def test_mock_flow_puts_the_ilim_cause_first_for_q1_and_shows_the_vcc_rating():
    result = run_mock_case("Q-1")
    kinds = {f["kind"]: f["status"] for f in result["design_check"]}
    assert kinds["ilim_valley"] == "too_low" and kinds["rating"] == "abs_over"
    assert "ILIM" in result["diagnosis"]["hypotheses"][0]["cause"] or any("ILIM" in h["cause"] for h in result["diagnosis"]["hypotheses"])
    assert "design_not_addressed" not in result["qa"]["issue_codes"]
    reply = result["response"]["customer_reply"]
    assert "6.67 A" in reply and "VCC 전압 14.4 V가 절대 최대 정격 14 V" in reply
    assert ".pdf" not in reply and ".yaml" not in reply and "parts/" not in reply


def test_mock_flow_for_the_charger_cases_adds_the_new_causes_without_looping():
    q3 = run_mock_case("Q-3")
    assert {f["kind"] for f in q3["design_check"]} == {"input_current_limit", "charge_power"}
    assert "design_not_addressed" not in q3["qa"]["issue_codes"]
    assert "0.755 A" in q3["response"]["customer_reply"]
    q4 = run_mock_case("Q-4")
    assert [f["kind"] for f in q4["design_check"]] == ["ts_window"] and "design_not_addressed" not in q4["qa"]["issue_codes"]
    assert "9.32 kΩ" in q4["response"]["customer_reply"]
    q2 = run_mock_case("Q-2")
    assert "max_duty" in {f["kind"] for f in q2["design_check"]}
