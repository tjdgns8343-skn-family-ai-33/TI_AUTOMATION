"""LangGraph 없이 에이전트 함수를 직접 이어 붙여 로직을 확인합니다."""

import json

import pytest

from fae_agent.agents.checker import run_checks
from fae_agent.agents.cpr import run_cpr
from fae_agent.agents.diagnose import run_diagnose
from fae_agent.agents.intake import classify_rules
from fae_agent.agents.playbook import build_candidates
from fae_agent.agents.qa import run_qa
from fae_agent.agents.research import run_research
from fae_agent.config import get_settings
from fae_agent.schemas import CheckReport, Complaint, Diagnosis, Solution
from fae_agent.tools.gateway import ToolGateway, available_tools


def load_case(case_id: str) -> dict:
    lines = get_settings().eval_path.read_text(encoding="utf-8").splitlines()
    return next(json.loads(line) for line in lines if line.strip() and json.loads(line)["id"] == case_id)


def technical_pipeline(case_id: str, max_retries: int = 2):
    """접수 → 조사 → (분석 → 테스트 → 검수)를 검수 통과나 재시도 한도까지 반복."""
    case = load_case(case_id)
    complaint = Complaint(subject=case["subject"], body=case["body"])
    intake = classify_rules(complaint)
    gateway = ToolGateway("local")
    research = run_research(complaint, intake, gateway)
    diagnosis, qa, history = None, None, []
    for _ in range(max_retries + 1):
        diagnosis = run_diagnose(complaint, intake, research, gateway, previous=diagnosis, qa=qa)
        solution = Solution.model_validate(diagnosis["solution"]) if diagnosis["solution"] else None
        report = run_checks(solution, gateway)
        previous_codes = (qa or {}).get("issue_codes", [])
        review = run_qa(intake, research, Diagnosis.model_validate(diagnosis), report, previous_codes=previous_codes)
        qa = review.model_dump()
        history.append((diagnosis, report, review))
        if review.passed:
            break
    return intake, history


def test_intake_extracts_conditions():
    case = load_case("E-01")
    intake = classify_rules(Complaint(subject=case["subject"], body=case["body"]))
    known = intake.conditions.known()
    assert (intake.category, intake.route) == ("technical", "fae")
    assert intake.part_numbers == ["EXAMPLE-BUCK1"]
    assert intake.symptoms[0] == "oscillation"
    assert known["vin_v"] == 12 and known["vout_v"] == 3.3 and known["iout_a"] == 2
    assert known["cout_f"] == pytest.approx(22e-6) and known["l_h"] == pytest.approx(4.7e-6)
    assert known["fsw_hz"] == pytest.approx(500e3)


@pytest.mark.parametrize(
    "case_id, category",
    [("E-01", "technical"), ("E-04", "technical"), ("E-05", "quality_claim"), ("E-06", "logistics")],
)
def test_intake_categories(case_id, category):
    case = load_case(case_id)
    assert classify_rules(Complaint(subject=case["subject"], body=case["body"])).category == category


def test_cpr_lists_missing_return_info():
    case = load_case("E-05")
    complaint = Complaint(subject=case["subject"], body=case["body"])
    check = run_cpr(complaint, classify_rules(complaint))
    assert any("A-B-A" in item for item in check.missing)
    assert "D4 근본 원인" in check.eight_d_draft


def test_playbook_tools_exist():
    tools = set(available_tools())
    candidates, _ = build_candidates(
        ["oscillation"], {"vout_v": 3.3, "l_h": 4.7e-6, "cout_f": 22e-6, "iout_a": 2.0}, None
    )
    steps = candidates[0].solution.test_plan
    assert len(steps) == 3 and all(step.tool in tools for step in steps)


def test_thermal_case_passes_first_time():
    _, history = technical_pipeline("E-02")
    diagnosis, report, review = history[-1]
    assert len(history) == 1 and review.passed
    assert [s.status for s in report.steps] == ["pass", "pass"]  # 증상 재현 + 해결책 확인


def test_wrong_output_moves_to_next_hypothesis():
    _, history = technical_pipeline("E-03")
    first_review = history[0][2]
    assert "reproduce_fail" in first_review.issue_codes  # 분압비로는 3.0 V가 설명되지 않음
    final_diagnosis, _, final_review = history[-1]
    assert final_review.passed
    assert "전류 제한" in final_diagnosis["hypotheses"][0]["cause"]


def test_qa_catches_rating_violation():
    _, history = technical_pipeline("E-07")
    assert "rating_not_addressed" in history[0][2].issue_codes
    final_diagnosis, report, final_review = history[-1]
    assert "정격" in final_diagnosis["hypotheses"][0]["cause"]
    assert final_review.passed and report.status == "pass"


def test_i2c_case_proposes_pullup_in_range():
    _, history = technical_pipeline("E-04")
    diagnosis, report, review = history[-1]
    assert review.passed
    assert [s.status for s in report.steps] == ["pass", "pass"]
    assert diagnosis["solution"]["changes"][0]["after"] == "1 kΩ"


def test_oscillation_case_without_ngspice_is_partial(monkeypatch):
    from fae_agent.tools import spice

    monkeypatch.setattr(spice, "find_ngspice", lambda: None)
    _, history = technical_pipeline("E-01")
    _, report, review = history[-1]
    assert report.status == "partial"  # 계산은 통과, 시뮬레이션은 생략
    assert review.passed and any("생략" in w for w in review.warnings)


def test_empty_plan_is_skipped():
    report = run_checks(None, ToolGateway("local"))
    assert report == CheckReport(status="skipped", steps=[])
