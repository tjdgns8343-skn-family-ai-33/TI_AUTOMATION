"""품질 클레임 경로의 정격 점검: 고객이 알려 준 값을 부품 카드 한계와 비교해 답변·8D에 붙인다."""

import pytest

pytest.importorskip("langgraph")

from fae_agent.agents import respond  # noqa: E402
from fae_agent.graph import build_graph, new_thread_id, start_run  # noqa: E402
from fae_agent.schemas import Complaint, Conditions, IntakeResult, ResponseDraft  # noqa: E402


def run(body: str, subject: str = "불량 반품 요청") -> dict:
    return start_run(build_graph(), Complaint(customer="가상전자", subject=subject, body=body), new_thread_id())


def reply_of(result: dict) -> str:
    return result["response"]["customer_reply"]


def test_over_absolute_max_is_reported_in_reply_8d_and_note():
    # EXAMPLE-LDO1 카드: 절대 최대 입력 6.5 V
    result = run("EXAMPLE-LDO1을 입력 9V, 출력 3.3V, 부하 0.2A로 쓰다가 IC가 타 버렸습니다. 불량이라 반품(RMA) 요청합니다.")
    check = result["rating_check"]
    assert result["intake"]["category"] == "quality_claim"
    assert check["found"] and any("입력 전압 9 V" in text and "6.5 V" in text for text in check["abs_over"])
    assert "먼저 확인된 점" in reply_of(result) and "6.5 V" in reply_of(result)
    assert "반품(CPR) 접수" in reply_of(result)  # 반품 절차 안내는 그대로 유지
    assert "절대 최대" in result["cpr"]["eight_d_draft"]["D4 근본 원인"]
    assert "정격 점검" in result["response"]["internal_note"]


def test_real_part_reply_cites_datasheet_name_not_file_path():
    # LM2596 카드: 절대 최대 45 V, 권장 최대 40 V
    result = run("LM2596을 입력 48V, 출력 5V, 부하 1A로 쓰다가 IC가 불량으로 타 버렸습니다. 반품 요청합니다.")
    reply = reply_of(result)
    assert any("45 V(권장 최대 40 V)" in text for text in result["rating_check"]["abs_over"])
    assert "출처: LM2596 데이터시트 SNVS124G" in reply and "parts/" not in reply
    assert result["rating_check"]["source"] == "parts/LM2596.yaml"  # 내부 경로는 상태에만 남는다


def test_values_within_limits_are_not_accused():
    result = run("EXAMPLE-LDO1을 입력 5V, 출력 3.3V, 부하 0.2A로 쓰는데 IC가 불량입니다. 반품 요청합니다.")
    check = result["rating_check"]
    assert check["found"] and check["abs_over"] == [] and "입력 전압" in check["within"]
    assert "먼저 확인된 점" not in reply_of(result)
    assert "절대 최대" not in result["cpr"]["eight_d_draft"]["D4 근본 원인"]


def test_unknown_part_is_flagged_as_not_checked_without_claims():
    result = run("TPS99999를 입력 50V로 썼더니 불량입니다. 반품 요청합니다.")
    check = result["rating_check"]
    assert check["found"] is False and check["abs_over"] == []
    assert "먼저 확인된 점" not in reply_of(result)
    assert "정격 점검을 하지 못했습니다" in result["response"]["internal_note"]


def test_no_part_number_means_no_check():
    result = run("제품이 불량입니다. 입력 50V에서 탔습니다. 반품 요청합니다.")
    assert result["rating_check"]["found"] is False and result["rating_check"]["part_number"] == ""


def test_existing_returns_case_without_conditions_is_unchanged():
    # eval E-05: 조건이 없으므로 점검할 값이 없다
    result = run("EXAMPLE-LDO1 200개 중 12개가 불량입니다. 전원을 넣자 IC가 타버렸습니다. 반품(RMA) 요청합니다.")
    assert result["rating_check"]["abs_over"] == []
    assert "먼저 확인된 점" not in reply_of(result)


def test_technical_path_does_not_run_the_check():
    result = run("EXAMPLE-LDO1 입력 7V, 출력 3.3V, 부하 0.3A로 쓰는데 IC가 너무 뜨겁습니다.", subject="EXAMPLE-LDO1 과열")
    assert result["intake"]["category"] == "technical" and "rating_check" not in result


def test_llm_prompt_receives_the_rating_check(monkeypatch):
    captured = {}

    def fake_structured_call(schema, system, user):
        captured["user"] = user
        return ResponseDraft(customer_reply="초안", internal_note="")

    monkeypatch.setattr(respond, "is_llm_mode", lambda: True)
    monkeypatch.setattr(respond, "structured_call", fake_structured_call)
    complaint = Complaint(customer="가상전자", subject="불량", body="LM2596 48V 연결 후 타 버림")
    intake = IntakeResult(category="quality_claim", route="cpr", conditions=Conditions(vin_v=48.0))
    check = {"part_number": "LM2596", "found": True, "abs_over": ["입력 전압 48 V가 절대 최대 정격 45 V를 넘습니다"]}
    respond.run_respond(
        {"complaint": complaint.model_dump(), "intake": intake.model_dump(), "rating_check": check}
    )
    assert "rating_check" in captured["user"] and "45 V" in captured["user"]
