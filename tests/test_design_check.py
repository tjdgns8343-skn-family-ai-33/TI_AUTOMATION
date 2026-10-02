"""설계 점검(design_check): 전류 제한 여유와 잰 리플 대 계산 비교를 코드가 직접 한다."""

import json
from pathlib import Path

import pytest

from fae_agent.agents.qa import run_qa
from fae_agent.design_check import design_facts, format_facts
from fae_agent.schemas import (
    CheckReport,
    Conditions,
    Diagnosis,
    Evidence,
    Hypothesis,
    IntakeResult,
    Solution,
)
from fae_agent.tools import calc, spice
from fae_agent.tools.calc import CALC_TOOLS

EVAL = "data/eval/eval_tps62130a.jsonl"

# 평가 문의 D-1의 TPS62130A 카드 중 설계 점검이 쓰는 값 (data/parts/TPS62130A.yaml과 같은 값)
TPS62130A = {
    "type": "buck",
    "typical": {"current_limit_a_min": 3.6, "current_limit_a": 4.2, "fsw_hz": 2.5e6},
}
TPS54331 = {"type": "buck", "typical": {"current_limit_a_min": 3.5, "current_limit_a": 5.8, "fsw_hz": 570000}}
D1 = {"vin_v": 12.0, "vout_v": 3.3, "iout_a": 2.8, "l_h": 1e-6, "fsw_hz": 1.25e6, "cout_f": 22e-6}


def call(tool: str, **kwargs):
    return CALC_TOOLS[tool.split(".", 1)[1]](**kwargs)


def intake(**conditions) -> IntakeResult:
    return IntakeResult(category="technical", route="fae", conditions=Conditions(**conditions))


def facts_for(card=TPS62130A, **conditions):
    return {f["kind"]: f for f in design_facts(intake(**conditions), card, call)}


def test_il_max_is_load_plus_half_the_ripple_in_ccm():
    r = calc.buck_ripple_estimate(12, 3.3, 1e-6, 1.25e6, 22e-6, 0.0, 2.8)
    assert r["il_max_a"] == pytest.approx(2.8 + r["delta_il_a"] / 2)
    assert r["il_max_a"] == pytest.approx(3.757, abs=0.001) and r["delta_il_a"] == pytest.approx(1.914, abs=0.001)


@pytest.mark.skipif(spice.find_ngspice() is None, reason="ngspice 미설치")
def test_il_max_in_dcm_matches_the_simulation_peak():
    est = calc.buck_ripple_estimate(24, 5, 33e-6, 150e3, 220e-6, 0.1, 0.15)  # 불연속 경계 아래 (평가 문의 B-L1)
    sim = spice.run_buck_switch(
        {"vin_v": 24, "vout_v": 5, "iout_a": 0.15, "l_h": 33e-6, "fsw_hz": 150e3, "cout_f": 220e-6, "esr_ohm": 0.1}
    )
    assert est["dcm"] == 1 and sim["dcm"] == 1
    assert est["il_max_a"] == pytest.approx(0.49, abs=0.005)  # 해석식 (다이오드 강하 없음)
    assert est["il_max_a"] == pytest.approx(sim["il_max_a"], rel=0.05)  # 시뮬레이션 0.504 A와 5 % 안


def test_current_limit_exceeds_the_minimum_for_d1_conditions():
    fact = facts_for(**D1)["current_limit"]
    assert fact["status"] == "exceeds_min"
    assert fact["il_max_a"] == pytest.approx(3.757, abs=0.001) and fact["limit_min_a"] == 3.6
    assert fact["onset_load_a"] == pytest.approx(2.643, abs=0.001)  # 이 부하부터 최소 제한 부품이 걸린다
    for expected in ("3.76 A", "3.6 A", "2.64 A", "1.91 A", "4.2 A"):
        assert expected in fact["text"]


def test_current_limit_has_room_with_a_larger_inductor():
    fact = facts_for(**{**D1, "l_h": 2.2e-6})["current_limit"]
    assert fact["status"] == "ok" and fact["il_max_a"] == pytest.approx(3.235, abs=0.001)
    assert "여유가 있습니다" in fact["text"]


def test_current_limit_uses_the_card_frequency_when_the_customer_gave_none():
    conditions = {k: v for k, v in D1.items() if k != "fsw_hz"}
    fact = facts_for(TPS54331, **{**conditions, "vin_v": 24, "vout_v": 5, "iout_a": 2.0, "l_h": 4.7e-6})["current_limit"]
    assert "카드의 전형값" in fact["text"]


def test_current_limit_in_discontinuous_conduction_uses_the_peak():
    fact = facts_for(TPS54331, vin_v=24, vout_v=5, iout_a=0.02, l_h=22e-6, fsw_hz=570e3)["current_limit"]
    assert fact["dcm"] == 1 and fact["status"] == "ok" and fact["il_max_a"] == pytest.approx(0.112, abs=0.002)


def test_nothing_is_checked_without_the_needed_values_or_for_other_part_types():
    assert design_facts(intake(vin_v=12, vout_v=3.3), TPS62130A, call) == []  # 인덕터·부하 없음
    assert design_facts(intake(**D1), {"type": "ldo", "typical": {}}, call) == []
    assert design_facts(intake(**D1), None, call) == []
    no_min = {"type": "buck", "typical": {"current_limit_a": 4.5}}  # 카드에 최소 전류 제한이 없으면 비교하지 않는다
    assert design_facts(intake(**D1), no_min, call) == []
    assert design_facts(intake(**{**D1, "vout_v": 13.0}), TPS62130A, call) == []  # Vout ≥ Vin은 점검하지 않는다


def test_measured_ripple_without_esr_reports_the_esr_needed_to_explain_it():
    fact = facts_for(**D1, ripple_meas_v=0.07)["measured_ripple"]
    assert fact["status"] == "needs_esr"
    assert fact["implied_esr_ohm"] == pytest.approx(0.032, abs=0.001)  # (70 − 8.7 mV) / 1.91 A
    assert "ESR이 약 32 mΩ" in fact["text"] and "단정하지 말고" in fact["text"]


def test_measured_ripple_with_esr_is_compared_with_the_formula():
    base = {"vin_v": 12, "vout_v": 5, "iout_a": 1.0, "l_h": 33e-6, "fsw_hz": 150e3, "cout_f": 100e-6, "esr_ohm": 0.3}
    consistent = facts_for(TPS54331, **base, ripple_meas_v=0.18)["measured_ripple"]  # 평가 문의 L-02: 계산 182 mV
    assert consistent["status"] == "consistent" and "일치" in consistent["text"]
    base_low = {**base, "cout_f": 470e-6, "esr_ohm": 0.02}
    unexplained = facts_for(TPS54331, **base_low, ripple_meas_v=0.12)["measured_ripple"]  # H-L1: 계산 12.8 mV
    assert unexplained["status"] == "unexplained" and unexplained["ratio"] > 9
    assert "계산으로는 설명되지 않으니" in unexplained["text"]


def test_measured_ripple_is_skipped_in_discontinuous_conduction():
    fact = facts_for(TPS54331, vin_v=24, vout_v=5, iout_a=0.15, l_h=33e-6, fsw_hz=150e3, cout_f=220e-6,
                     ripple_meas_v=0.05)["measured_ripple"]  # fmt: skip
    assert fact["status"] == "skipped" and "불연속" in fact["text"]


def hypothesis_diagnosis(cause: str, mechanism: str = "") -> Diagnosis:
    h = Hypothesis(cause=cause, mechanism=mechanism, evidence=[Evidence(source="parts/TPS62130A.yaml")])
    return Diagnosis(hypotheses=[h], solution=Solution(summary="해결"))


def qa_for(design, diagnosis):
    return run_qa(intake(**D1), {"part_card": TPS62130A}, diagnosis, CheckReport(status="skipped"), design=design)


def test_qa_sends_back_a_diagnosis_that_misses_the_current_limit():
    design = design_facts(intake(**D1), TPS62130A, call)
    review = qa_for(design, hypothesis_diagnosis("출력 커패시터 용량 부족", "유효 용량이 줄어 안정성 마진이 작다"))
    assert "design_not_addressed" in review.issue_codes and not review.passed
    assert any("3.76 A" in issue and "전류 제한이 없습니다" in issue for issue in review.issues)


def test_qa_accepts_a_diagnosis_that_covers_the_current_limit_and_keeps_the_fact_as_a_warning():
    design = design_facts(intake(**D1), TPS62130A, call)
    review = qa_for(design, hypothesis_diagnosis("인덕터 1 µH가 작아 전류 제한에 걸림", "최대 전류 3.76 A"))
    assert "design_not_addressed" not in review.issue_codes
    assert any(w.startswith("설계 점검:") and "3.76 A" in w for w in review.warnings)


def test_qa_lists_measured_ripple_facts_as_warnings_only():
    design = design_facts(intake(**D1, ripple_meas_v=0.07), TPS62130A, call)
    review = qa_for(design, hypothesis_diagnosis("전류 제한", "피크 전류가 제한에 걸림"))
    assert any("ESR이 약 32 mΩ" in w for w in review.warnings)
    assert "design_not_addressed" not in review.issue_codes


def test_format_facts_lists_kind_and_status():
    text = format_facts(design_facts(intake(**D1, ripple_meas_v=0.07), TPS62130A, call))
    assert "[current_limit / exceeds_min]" in text and "[measured_ripple / needs_esr]" in text
    assert format_facts(None) == ""


def test_prompt_example_of_the_inductor_back_calculation_holds():
    vin, vout, fsw, iout, limit = 15.0, 5.0, 1e6, 2.0, 3.0
    max_ripple = 2 * (0.9 * limit - iout)
    assert max_ripple == pytest.approx(1.4)
    l_min = vout * (1 - vout / vin) / (fsw * max_ripple)
    assert l_min == pytest.approx(2.4e-6, rel=0.01)
    r = calc.buck_ripple_estimate(vin, vout, l_min, fsw, 22e-6, 0.0, iout)
    assert r["il_max_a"] == pytest.approx(0.9 * limit)  # 이 L이면 최대 전류가 제한의 90 %가 된다


def test_d1_case_numbers_match_the_design_check():
    """평가 문의 D-1의 정답 숫자(1.91 A, 3.76 A, 2.64 A)가 설계 점검과 같은 값이다."""
    case = json.loads((Path(__file__).parent.parent / EVAL).read_text(encoding="utf-8").splitlines()[0])
    assert "3.76 A" in case["ground_truth"] and "2.64 A" in case["ground_truth"]
    fact = facts_for(**D1)["current_limit"]
    assert round(fact["il_max_a"], 2) == 3.76 and round(fact["delta_il_a"], 2) == 1.91 and round(fact["onset_load_a"], 2) == 2.64


def test_mock_diagnosis_adds_the_current_limit_hypothesis_so_the_flow_does_not_loop():
    """mock 규칙은 전류 제한을 모르므로 설계 점검 결과를 가설로 넣는다 (넣지 않으면 검수가 헛재분석을 반복한다)."""
    from fae_agent.graph import build_graph, new_thread_id, start_run
    from fae_agent.schemas import Complaint

    text = (
        "TPS62130A를 입력 12V, 출력 3.3V, 부하 2.8A로 쓰는데 인덕터 1uH, 스위칭 주파수 1250kHz, 출력 커패시터 22uF입니다. "
        "출력 리플이 약 70mV이고 2.5A 넘으면 출력이 떨어집니다."
    )
    result = start_run(build_graph(), Complaint(subject="TPS62130A 점검", body=text), new_thread_id())
    assert [f["status"] for f in result["design_check"]] == ["exceeds_min", "needs_esr"]
    assert "전류 제한" in result["diagnosis"]["hypotheses"][0]["cause"]
    assert "3.76 A" in result["diagnosis"]["hypotheses"][0]["mechanism"]
    assert "design_not_addressed" not in result["qa"]["issue_codes"]
    assert any("설계 점검" in line for line in result["response"]["internal_note"].splitlines())
    assert "■ 설계 점검 (코드 계산)" in result["response"]["customer_reply"]
