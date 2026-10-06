"""설계 점검 확대: 소프트 스타트, 최소 온타임, LDO 열, 오프셋 (평가 문의 M-1~M-3의 정답 숫자와 대조)."""

import json
from pathlib import Path

import pytest

from fae_agent.agents.qa import run_qa
from fae_agent.design_check import COVERAGE, design_facts, missing_causes
from fae_agent.schemas import CheckReport, Conditions, Diagnosis, Evidence, Hypothesis, IntakeResult, Solution
from fae_agent.tools.calc import CALC_TOOLS

HARD = "data/eval/eval_hard_multi.jsonl"
M3 = {"vin_v": 28.0, "vout_v": 1.8, "iout_a": 2.5, "l_h": 4.7e-6, "fsw_hz": 570e3, "cout_f": 22e-6, "esr_ohm": 0.005}
M1 = {"vin_v": 5.5, "vout_v": 1.8, "iout_a": 0.25, "ta_c": 50.0}
TPS54331 = {
    "type": "buck",
    "typical": {
        "current_limit_a_min": 3.5, "fsw_hz": 570000, "min_on_time_s": 130e-9, "min_on_time_typ_s": 105e-9,
        "ss_charge_current_a": 2e-6, "ss_ramp_v": 0.8, "ss_time_min_s": 1e-3, "ss_time_max_s": 10e-3, "ss_cap_max_f": 27e-9,
    },
}  # fmt: skip
TPS62130A = {"type": "buck", "typical": {"current_limit_a_min": 3.6, "fsw_hz": 2.5e6}}
TPS7A20 = {
    "type": "ldo",
    "typical": {"thermal_shutdown_c": 165},
    "recommended": {"tj_max_c": 125, "iout_max_a": 0.3},
    "thermal": {"theta_ja_c_per_w": 187.1},
}
LMV321A = {"type": "opamp", "typical": {"vos_typ_mv": 1, "vos_max_mv": 4}}


def call(tool: str, **kwargs):
    return CALC_TOOLS[tool.split(".", 1)[1]](**kwargs)


def intake(**conditions) -> IntakeResult:
    return IntakeResult(category="technical", route="fae", conditions=Conditions(**conditions))


def facts_for(card, **conditions):
    return {f["kind"]: f for f in design_facts(intake(**conditions), card, call)}


def hypothesis_diagnosis(cause: str, mechanism: str = "") -> Diagnosis:
    h = Hypothesis(cause=cause, mechanism=mechanism, evidence=[Evidence(source="tps54331.pdf")])
    return Diagnosis(hypotheses=[h], solution=Solution(summary="해결"))


def qa_issues(conditions: dict, card: dict, design: list, diagnosis: Diagnosis):
    return run_qa(intake(**conditions), {"part_card": card}, diagnosis, CheckReport(status="skipped"), design=design)


def hard_case(case_id: str) -> dict:
    for line in (Path(__file__).parent.parent / HARD).read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        if case["id"] == case_id:
            return case
    raise KeyError(case_id)


# ---------------------------------------------------------------- 소프트 스타트


def test_soft_start_100nf_on_tps54331_is_40ms_and_too_slow():
    fact = facts_for(TPS54331, **M3, css_f=100e-9)["soft_start"]
    assert fact["status"] == "too_slow" and fact["tss_s"] == pytest.approx(0.040)  # 100 nF × 0.8 V ÷ 2 µA
    assert fact["css_target_f"] == pytest.approx(12.5e-9)  # 5 ms가 되는 값
    for expected in ("40 ms", "1~10 ms", "27 nF"):
        assert expected in fact["text"]


def test_soft_start_in_range_too_fast_and_cap_limit():
    ok = facts_for(TPS54331, **M3, css_f=10e-9)["soft_start"]
    assert ok["status"] == "ok" and ok["tss_s"] == pytest.approx(0.004)  # 해결값 예: 10 nF에서 4 ms
    assert facts_for(TPS54331, **M3, css_f=1e-9)["soft_start"]["status"] == "too_fast"  # 0.4 ms
    assert facts_for(TPS54331, **M3, css_f=33e-9)["soft_start"]["status"] == "too_slow"  # 13.2 ms, 27 nF 초과
    cap_only = {**TPS54331, "typical": {**TPS54331["typical"], "ss_time_max_s": 20e-3}}
    assert facts_for(cap_only, **M3, css_f=33e-9)["soft_start"]["status"] == "too_slow"  # 시간은 범위 안이어도 27 nF 초과


def test_soft_start_without_a_datasheet_range_is_ok_info():
    card = {"type": "buck", "typical": {"ss_charge_current_a": 2.5e-6, "ss_ramp_v": 1.25}}
    fact = facts_for(card, **M3, css_f=10e-9)["soft_start"]
    assert fact["status"] == "ok" and fact["tss_s"] == pytest.approx(0.005)
    assert "권장 범위가 데이터시트에 없습니다" in fact["text"]
    assert "soft_start" not in facts_for(TPS54331, **M3)  # SS 커패시터를 모르면 점검하지 않는다


# ---------------------------------------------------------------- 최소 온타임


def test_min_on_time_is_uncertain_when_the_diode_drop_decides_it():
    fact = facts_for(TPS54331, **M3)["min_on_time"]  # M-3: 28 V → 1.8 V, 570 kHz
    assert fact["status"] == "uncertain"
    assert fact["ton_ideal_s"] == pytest.approx(112.8e-9, rel=0.001)
    assert fact["ton_with_diode_s"] == pytest.approx(136e-9, rel=0.01)
    assert fact["vout_min_v"] == pytest.approx(2.07, abs=0.01)  # 최소 온타임 최대 130 ns를 이상적으로 만족하는 최소 출력
    assert "확정할 수 없습니다" in fact["text"] and "113 ns" in fact["text"] and "136 ns" in fact["text"]


def test_min_on_time_other_statuses():
    assert facts_for(TPS54331, **{**M3, "vout_v": 5.0})["min_on_time"]["status"] == "ok"  # 313 ns
    assert facts_for(TPS54331, **{**M3, "vout_v": 1.5})["min_on_time"]["status"] == "marginal"  # 94 ns / 117 ns
    assert facts_for(TPS54331, **{**M3, "vout_v": 1.0})["min_on_time"]["status"] == "below_typ"  # 63 ns / 86 ns
    assert "min_on_time" not in facts_for(TPS62130A, **M3)  # 카드에 최소 온타임이 없으면 점검하지 않는다


def test_min_on_time_uses_the_card_frequency_when_the_customer_gave_none():
    conditions = {k: v for k, v in M3.items() if k != "fsw_hz"}
    fact = facts_for(TPS54331, **conditions)["min_on_time"]
    assert fact["status"] == "uncertain" and "데이터시트의 전형값 570 kHz로 가정했습니다" in fact["text"]


# ---------------------------------------------------------------- LDO 열, 오프셋


def test_ldo_thermal_for_m1_conditions_matches_the_ground_truth_numbers():
    fact = facts_for(TPS7A20, **M1, tcase_meas_c=80.0)["ldo_thermal"]
    assert fact["status"] == "shutdown" and fact["pdiss_w"] == pytest.approx(0.925)
    assert fact["tj_c"] == pytest.approx(223.1, abs=0.1)
    assert fact["imax_at_tj_rec_a"] == pytest.approx(0.108, abs=0.001)  # 접합 125 °C 안이려면 이 부하 이하
    assert fact["vin_max_at_tj_rec_v"] == pytest.approx(3.4, abs=0.05)  # 부하 0.25 A에서 이 입력 이하
    for expected in ("32.7 %", "165 °C", "전류 정격(0.3 A) 이내여도 열이 한계", "접합 온도가 아닙니다", "모순이 아닙니다"):
        assert expected in fact["text"]


def test_ldo_thermal_other_statuses_and_a_surface_temperature_above_the_junction():
    ok = facts_for(TPS7A20, vin_v=3.3, vout_v=1.8, iout_a=0.1, ta_c=25.0)["ldo_thermal"]
    assert ok["status"] == "ok" and "권장 접합 온도 125 °C 안입니다" in ok["text"]
    over = facts_for(TPS7A20, vin_v=4.8, vout_v=1.8, iout_a=0.2, ta_c=25.0, tcase_meas_c=200.0)["ldo_thermal"]
    assert over["status"] == "over_recommended" and over["tj_c"] == pytest.approx(137.2, abs=0.1)
    assert "다시 확인해야 합니다" in over["text"]  # 표면이 접합보다 높게 나오면 θJA나 조건을 의심
    assert "ldo_thermal" not in facts_for(TPS7A20, vin_v=5.5, vout_v=1.8, iout_a=0.25)  # 주변 온도를 모르면 점검하지 않는다


def test_offset_for_m2_conditions_is_101_mv_typical_and_404_mv_maximum():
    fact = facts_for(LMV321A, rf_ohm=100e3, rg_ohm=1e3)["offset"]
    assert fact["status"] == "info" and fact["gain"] == pytest.approx(101)
    assert fact["offset_out_typ_mv"] == pytest.approx(101) and fact["offset_out_max_mv"] == pytest.approx(404)
    assert "불량이 아닙니다" in fact["text"]
    assert "offset" not in facts_for(LMV321A, rf_ohm=100e3)  # Rg를 모르면 점검하지 않는다


# ---------------------------------------------------------------- 검수·mock 흐름


def test_missing_causes_uses_the_coverage_table():
    facts = design_facts(intake(**M3, css_f=100e-9), TPS54331, call)
    assert [f["kind"] for f, _ in missing_causes(facts, "출력 커패시터 부족")] == ["soft_start"]  # uncertain·ok는 요구하지 않는다
    assert missing_causes(facts, "SS 핀 커패시터가 커서 소프트 스타트 시간이 길다") == []
    assert missing_causes(None, "") == []
    for (kind, status), (title, words, target, after) in COVERAGE.items():
        assert kind and status and title and words and target and after
        assert all(word == word.lower() for word in words)  # 가설 글을 소문자로 바꿔 비교하므로


def test_qa_sends_back_a_diagnosis_that_misses_the_soft_start_or_the_heat():
    soft = design_facts(intake(**M3, css_f=100e-9), TPS54331, call)
    review = qa_issues(M3, TPS54331, soft, hypothesis_diagnosis("출력 커패시터 용량 부족"))
    assert "design_not_addressed" in review.issue_codes
    assert any("소프트 스타트 커패시터가 너무 커서" in issue for issue in review.issues)
    covered = hypothesis_diagnosis("SS 커패시터가 커서 기동이 느림", "소프트 스타트 40 ms")
    review = qa_issues(M3, TPS54331, soft, covered)
    assert "design_not_addressed" not in review.issue_codes
    assert any(w.startswith("설계 점검:") and "40 ms" in w for w in review.warnings)
    heat = design_facts(intake(**M1), TPS7A20, call)
    assert "design_not_addressed" in qa_issues(M1, TPS7A20, heat, hypothesis_diagnosis("출력 커패시터 ESR 초과")).issue_codes
    heat_cause = hypothesis_diagnosis("LDO 소모전력으로 접합 온도 초과")
    assert "design_not_addressed" not in qa_issues(M1, TPS7A20, heat, heat_cause).issue_codes


def run_mock_case(case_id: str) -> dict:
    from fae_agent.graph import build_graph, new_thread_id, start_run
    from fae_agent.schemas import Complaint

    case = hard_case(case_id)
    return start_run(build_graph(), Complaint(subject=case["subject"], body=case["body"]), new_thread_id())


def test_mock_flow_puts_the_soft_start_cause_in_for_m3_and_keeps_internal_names_out_of_the_reply():
    result = run_mock_case("M-3")
    kinds = {f["kind"]: f["status"] for f in result["design_check"]}
    assert kinds["soft_start"] == "too_slow" and kinds["min_on_time"] == "uncertain" and kinds["current_limit"] == "ok"
    assert any("소프트 스타트" in h["cause"] for h in result["diagnosis"]["hypotheses"])
    assert "design_not_addressed" not in result["qa"]["issue_codes"]
    reply = result["response"]["customer_reply"]
    assert "40 ms" in reply and "12.5 nF" in reply and "113 ns" in reply
    assert ".pdf" not in reply and ".yaml" not in reply and "parts/" not in reply and "부품 카드" not in reply


def test_mock_flow_reports_the_heat_for_m1_and_the_offset_for_m2():
    heat = run_mock_case("M-1")
    assert {f["kind"]: f["status"] for f in heat["design_check"]}["ldo_thermal"] == "shutdown"
    assert any("열 차단" in h["cause"] or "접합 온도" in h["cause"] for h in heat["diagnosis"]["hypotheses"])
    reply = heat["response"]["customer_reply"]
    assert "223 °C" in reply and "접합 온도가 아닙니다" in reply
    offset = run_mock_case("M-2")
    assert [f["kind"] for f in offset["design_check"]] == ["offset"]
    assert "404 mV" in offset["response"]["customer_reply"]
