"""영업 문의(견적·샘플, 설계 상담): 분류, 요청 점검, 부품 추천(코드 판단), 승인 뒤 문서화. 정답은 평가 문의 파일에 손으로 유도해 둔 값이다."""

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from fae_agent.agents.intake import classify_rules, extract_conditions
from fae_agent.config import get_settings
from fae_agent.docs_check import check_document
from fae_agent.graph import build_graph, new_thread_id, resume_run, start_run
from fae_agent.schemas import Complaint, IntakeResult

ROOT = Path(__file__).resolve().parent.parent
CASES = {c["id"]: c for c in map(json.loads, (ROOT / "data" / "eval" / "eval_sales.jsonl").read_text(encoding="utf-8").splitlines())}
QUOTES = [cid for cid, c in CASES.items() if c["expected_category"] == "quote_sample"]
DESIGNS = [cid for cid, c in CASES.items() if c["expected_category"] == "design_request"]


def run(case_id: str, decisions: tuple[str, ...] = ()) -> dict:
    case = CASES[case_id]
    graph, thread = build_graph(), new_thread_id()
    result = start_run(graph, Complaint(customer=case["customer"], subject=case["subject"], body=case["body"]), thread)
    for action in decisions:
        result = resume_run(graph, thread, action)
    return result


def test_nine_cases_with_a_rubric_and_hand_derived_expectations():
    assert len(CASES) == 9 and len(QUOTES) == 3 and len(DESIGNS) == 6
    for case in CASES.values():
        assert case["rubric"][0].startswith("분류 ") and case["ground_truth"] and case["verifiable"] is True


@pytest.mark.parametrize("case_id", list(CASES))
def test_rule_based_intake_classifies_every_case_and_routes_to_sales(case_id):
    case = CASES[case_id]
    intake = classify_rules(Complaint(subject=case["subject"], body=case["body"]))
    assert intake.category == case["expected_category"] and intake.route == "sales"


def test_input_ranges_and_request_details_are_read():
    conditions = extract_conditions("입력은 9~15V, 출력은 3.3V, 부하는 0.5A입니다.")
    assert (conditions.vin_min_v, conditions.vin_max_v, conditions.vin_v) == (9.0, 15.0, 15.0)  # vin_v에는 최고값 (정격 비교는 최악 조건)
    assert extract_conditions("입력 12V, 출력 5V").vin_min_v is None
    request = classify_rules(Complaint(body=CASES["S-1"]["body"])).request
    assert (request.kind, request.quantity, request.need_by, request.application, request.stage) == ("sample", 10, "6주 안", "웨어러블 전원", "prototype")
    assert classify_rules(Complaint(body=CASES["S-3"]["body"])).request.quantity == 5000  # 쉼표가 있는 수량
    assert IntakeResult(category="quote_sample", route="sales").request.kind == ""  # LLM 접수 스키마가 새 유형을 받는다


@pytest.mark.parametrize("case_id", QUOTES)
def test_quote_and_sample_requests_collect_what_the_customer_gave_and_ask_for_the_rest(case_id):
    case = CASES[case_id]
    result = run(case_id)
    sales = result["sales"]
    assert sales["collected"] == case["expected_collected"] and sales["missing"] == case["expected_missing"]
    reply = result["response"]["customer_reply"]
    assert "영업 시스템에서 확인" in reply
    for missing in case["expected_missing"]:
        assert missing in reply
    assert "recommendation" not in result and not any(word in reply for word in ("원 ", "달러", "할인", "보증", "리드 타임"))  # 가격·납기를 말하지 않는다


def test_an_unknown_part_is_not_introduced():
    sales = run("S-3")["sales"]
    assert sales["parts"] == [{"part": "TPS99999", "known": False, "description": "", "source": ""}]
    assert "TPS99999:" not in run("S-3")["response"]["customer_reply"]  # 카드가 없으면 사양을 소개하지 않는다
    known = run("S-1")["sales"]["parts"][0]
    assert known["known"] and "동기 벅" in known["description"]


@pytest.mark.parametrize("case_id", DESIGNS)
def test_recommendation_verdicts_match_the_hand_derived_expectations(case_id):
    case = CASES[case_id]
    rec = run(case_id)["recommendation"]
    assert rec["status"] == case["expected_status"] and rec["top"] == case["expected_top"]
    assert {c["part"]: c["verdict"] for c in rec["candidates"]} == case["expected_verdicts"]
    assert rec["missing"] == case.get("expected_missing", [])


def test_recommendation_numbers_for_the_ldo_buck_and_controller_cases():
    s4 = {c["part"]: c for c in run("S-4")["recommendation"]["candidates"]}
    assert "0.22 W" in s4["TPS7A02"]["facts"][0] and "65" in s4["TPS7A02"]["facts"][0] and "66" in s4["TPS7A20"]["facts"][0]
    assert any("권장 최소 4.5 V" in f for f in s4["LM2596"]["fails"]) and any("권장 최소 5.5 V" in f for f in s4["LM5146"]["fails"])
    s5 = {c["part"]: c for c in run("S-5")["recommendation"]["candidates"]}
    assert any("5.85 W" in f for f in s5["TPS7A02"]["fails"]) and any("절대 최대 6.5 V" in f for f in s5["TPS7A02"]["fails"])
    assert any("카드에 최소 온타임·최대 듀티가 없어" in w for w in s5["LM2596"]["warnings"])
    s6 = {c["part"]: c for c in run("S-6")["recommendation"]["candidates"]}
    assert any("3.64 V" in f for f in s6["TPS62130A"]["fails"]) and any("강압" in f for f in s6["TPS7A02"]["fails"])
    s7 = {c["part"]: c for c in run("S-7")["recommendation"]["candidates"]}
    assert any("100 kHz 이상 약 1 MHz 이하" in f for f in s7["LM5146"]["facts"]) and any("절대 최대 45 V" in f for f in s7["LM2596"]["fails"])


def test_a_preference_changes_the_order_but_not_the_verdicts():
    efficient = run("S-4")["recommendation"]
    assert efficient["prefer"] == "ldo" and [c["part"] for c in efficient["candidates"]][:2] == ["TPS7A02", "TPS7A20"]
    buck_first = run("S-5")["recommendation"]
    assert buck_first["prefer"] == "buck" and buck_first["candidates"][0]["type"] == "buck"


def test_unsupported_and_incomplete_requests_do_not_get_a_recommendation():
    assert run("S-8")["recommendation"]["candidates"] == [] and "출력 전압" in run("S-8")["response"]["customer_reply"]
    reply = run("S-9")["response"]["customer_reply"]
    assert "전원(LDO·벅) 추천만" in reply and "TPS" not in reply and "LM" not in reply
    none = run("S-6")["response"]["customer_reply"]
    assert "요구사항을 만족하는 부품이 없습니다" in none and "■ 후보" not in none


def test_the_reply_lists_candidates_rejections_assumptions_and_the_scope():
    reply = run("S-6")["response"]["customer_reply"]
    for expected in ("■ 알려 주신 요구사항", "입력 3~4.2 V", "■ 제외한 부품과 이유", "■ 계산에 쓴 가정", "인덕터 DCR", "6종 부품 안에서만 비교"):
        assert expected in reply


@pytest.mark.parametrize("case_id", ["S-1", "S-2", "S-4", "S-6", "S-7", "S-8", "S-9"])
def test_approved_sales_inquiries_get_documents_that_pass_the_check(case_id):
    result = run(case_id, ("approve",))
    assert result["documents"]["status"] == "generated", result["documents"]
    folder = get_settings().outbox_dir / result["ticket_id"]
    report = (folder / "internal_report.md").read_text(encoding="utf-8")
    assert ("## 영업 요청 점검" in report) == (case_id in QUOTES) and ("## 부품 추천" in report) == (case_id in DESIGNS)
    summary = json.loads((folder / "ticket_summary.json").read_text(encoding="utf-8"))
    assert summary["category"] == CASES[case_id]["expected_category"] and summary["route"] == "sales"


def test_a_promise_added_to_a_sales_document_is_still_caught():
    result = run("S-1")
    source = result["response"]["customer_reply"]
    assert check_document(source + "\n납기는 3주, 단가는 $4입니다.", source)
    assert check_document(source, source) == []
