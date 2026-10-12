"""영업 문의 새 평가 9건(eval_sales_heldout.jsonl, V-1~V-9): 답변 검사 규칙을 만들 때 보지 않은 문의.

정답은 부품 카드 값으로 손으로 유도해 측정 전에 확정했다. 처음 코드(규칙 기반 접수, 추천)는 7곳에서 정답과 달랐고(xfail로 기록),
정답을 바꾸지 않고 코드를 고쳐 모두 맞췄다 (docs/sales_measurement_2026-10-12.md). 이 9건은 고치는 동안 본 문의라 더는 '보지 않은' 문의가 아니다.
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from test_sales import run_case

from fae_agent.agents.recommend import run_recommend
from fae_agent.schemas import Conditions, IntakeResult
from fae_agent.tools.gateway import ToolGateway

ROOT = Path(__file__).resolve().parent.parent
CASES = {c["id"]: c for c in map(json.loads, (ROOT / "data" / "eval" / "eval_sales_heldout.jsonl").read_text(encoding="utf-8").splitlines())}

def test_nine_held_out_cases_with_a_rubric_and_hand_derived_truth():
    assert len(CASES) == 9
    for case in CASES.values():
        assert case["rubric"][0].startswith("분류 ") and case["ground_truth"] and case["verifiable"] is True


@pytest.mark.parametrize("case_id", list(CASES))
def test_the_pipeline_matches_the_hand_derived_truth(case_id):
    case = CASES[case_id]
    result = run_case(case)
    assert result["intake"]["category"] == case["expected_category"]
    if case["expected_category"] == "quote_sample":
        collected = result["sales"]["collected"]
        for label, parts in case["expected_collected_contains"].items():
            assert label in collected and all(p in collected[label] for p in parts), (label, collected)
        assert sorted(result["sales"]["missing"]) == sorted(case["expected_missing"])
    else:
        rec = result["recommendation"]
        assert rec["status"] == case["expected_status"]
        assert {c["part"]: c["verdict"] for c in rec["candidates"]} == case["expected_verdicts"]
        assert sorted(rec["missing"]) == sorted(case["expected_missing"])


# 접수가 맞다고 가정하고 올바른 조건을 직접 넣어, 추천 판정만 정답과 맞는지 본다
CORRECT_CONDITIONS = {
    "V-4": {"vin_v": 5, "vout_v": 3.3, "iout_a": 0.3},
    "V-5": {"vin_min_v": 2.7, "vin_max_v": 5.5, "vin_v": 5.5, "vout_v": 1.8, "iout_a": 1},
    "V-6": {"vin_v": 12, "vout_v": 9, "iout_a": 1},
    "V-7": {"vin_v": 12, "vout_v": 3.3},
}


@pytest.mark.parametrize("case_id", list(CORRECT_CONDITIONS))
def test_recommendation_matches_the_truth_when_the_conditions_are_right(case_id):
    case = CASES[case_id]
    intake = IntakeResult(category="design_request", route="sales", conditions=Conditions(**CORRECT_CONDITIONS[case_id]))
    rec = run_recommend(intake, case["body"], ToolGateway("local"))
    assert rec["status"] == case["expected_status"]
    assert {c["part"]: c["verdict"] for c in rec["candidates"]} == case["expected_verdicts"]
    assert rec["top"] in case["expected_top_in"] or case["expected_top_in"] == [""]


# ---- 고친 규칙의 일반화 확인 (V 문항 밖의 표현)
from fae_agent.agents.intake import classify_rules, extract_request
from fae_agent.schemas import Complaint


@pytest.mark.parametrize(
    ("text", "field", "expected"),
    [
        ("적용 제품은 전동 킥보드입니다.", "application", "전동 킥보드"),
        ("사용처는 스마트 도어락이에요", "application", "스마트 도어락"),
        ("이번 주까지 필요합니다", "need_by", "이번 주까지"),
        ("내년 초까지 받고 싶어요", "need_by", "내년 초까지"),
        ("다음 분기 중에 필요", "need_by", "다음 분기 중"),
        ("양산 전 평가용으로 쓸 예정", "stage", "prototype"),
        ("양산 이전에 시제품을 만듭니다", "stage", "prototype"),
        ("양산 물량 500개", "stage", "production"),  # 기존 동작 유지
    ],
)
def test_request_extraction_rules_generalize(text, field, expected):
    assert getattr(extract_request(text, True, False), field) == expected


@pytest.mark.parametrize(
    ("text", "vin", "vout", "iout"),
    [
        ("5V에서 1.8V 0.5A 만들 부품 추천", 5.0, 1.8, 0.5),
        ("24V 입력으로 5V를 만들고 싶은데 부품을 추천해 주세요", 24.0, None, None),
        ("입력 9~15V, 출력 3.3V, 부하 0.5A 추천", 15.0, 3.3, 0.5),  # 이미 읽은 값은 건드리지 않는다
    ],
)
def test_design_conditions_fill_only_what_is_missing(text, vin, vout, iout):
    c = classify_rules(Complaint(body=text)).conditions
    assert (c.vin_v, c.vout_v, c.iout_a) == (vin, vout, iout)


def test_technical_inquiries_are_not_touched_by_the_design_rules():
    c = classify_rules(Complaint(body="TPS54331 출력이 발진합니다. 12V에서 5V 2A로 씁니다.")).conditions
    assert c.iout_a is None  # 설계 상담 경로에서만 'N A'를 부하 전류로 읽는다


def test_a_charger_request_is_unsupported_even_when_a_power_condition_is_given():
    from fae_agent.agents.recommend import run_recommend

    intake = classify_rules(Complaint(body="5V 입력으로 3.3V 1A, 리튬이온 충전 IC를 추천해 주세요"))
    assert run_recommend(intake, "5V 입력으로 3.3V 1A, 리튬이온 충전 IC를 추천해 주세요", ToolGateway("local"))["status"] == "unsupported"
