"""영업 문의 답변 검사: 코드 결과를 빼먹거나 어기는 LLM 답변을 잡고, 걸리면 코드 템플릿으로 바꾼다 (LLM은 부르지 않는다).

저장된 LLM 답변(docs/feasibility/results/sales_llm.jsonl)은 2026-10-12 측정의 실제 출력이다.
검사 규칙은 이 출력의 실패를 보고 만들었으므로 이 테스트는 '측정에서 본 실패를 다시 잡는다'는 회귀 확인이지 독립 검증이 아니다.
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from test_sales import CASES, run

from fae_agent.agents import respond as respond_module
from fae_agent.agents.respond import run_respond
from fae_agent.sales_reply_check import check_sales_reply
from fae_agent.schemas import ResponseDraft

ROOT = Path(__file__).resolve().parent.parent
STORED = ROOT / "docs" / "feasibility" / "results" / "sales_llm.jsonl"


def codes(issues: list[str]) -> set[str]:
    return {issue.split(":")[0] for issue in issues}


def check(case_id: str, reply: str) -> list[str]:
    result = run(case_id)
    return check_sales_reply(reply, result["intake"]["category"], result)


@pytest.mark.parametrize("case_id", list(CASES))
def test_the_code_templates_pass_their_own_check(case_id):
    result = run(case_id)
    assert check_sales_reply(result["response"]["customer_reply"], result["intake"]["category"], result) == []


@pytest.mark.skipif(not STORED.exists(), reason="저장된 측정 결과 없음")
def test_failures_seen_in_the_2026_10_12_measurement_are_caught():
    stored = {r["id"]: r["response"]["customer_reply"] for r in map(json.loads, STORED.read_text(encoding="utf-8").splitlines())}
    expected = {
        "S-1": {"extra_questions"},
        "S-3": {"extra_questions", "stage_as_application"},
        "S-4": {"candidate_missing"},
        "S-5": {"candidate_missing"},
        "S-6": {"candidate_missing", "none_not_stated", "handoff_missing", "scope_missing", "assumption_missing"},
        "S-7": {"candidate_missing"},
        "S-9": {"placeholder", "handoff_missing"},
    }
    for case_id, want in expected.items():
        assert want <= codes(check(case_id, stored[case_id])), case_id
    assert check("S-2", stored["S-2"]) == [] and check("S-8", stored["S-8"]) == []  # 잘 쓴 답변은 건드리지 않는다


def test_rules_on_small_replies():
    quote = "안녕하세요. 수량 10개 확인했습니다. 용도, 시점을 알려 주세요. 시제품인지 양산인지도요. 부품 번호도 부탁드립니다."
    assert "missing_not_asked" not in codes(check("S-2", quote))  # S-2에서 빠진 항목은 수량·용도·시점·시제품/양산
    assert "missing_not_asked" in codes(check("S-2", "안녕하세요. 영업 시스템에서 확인 후 회신드리겠습니다."))
    assert "price_or_lead_time" in codes(check("S-1", "수량 10개, 단가는 1,200원이고 납기 3주입니다."))
    assert "price_or_lead_time" not in codes(check("S-1", "수량 10개, 6주 안에 필요하시다고 확인했습니다."))  # 고객이 쓴 시점은 허용
    assert "collected_dropped" in codes(check("S-1", "안녕하세요. 영업 시스템에서 확인 후 회신드리겠습니다."))
    assert "recommended_without_info" in codes(check("S-8", "12 V 입력이면 TPS54331을 추천합니다. 출력 전압과 전류를 알려 주세요."))
    assert "unsupported_with_parts" in codes(check("S-9", "OPA1612를 추천합니다. 담당자가 검토합니다. 전원 추천만 지원합니다."))


def llm_draft(monkeypatch, reply: str) -> None:
    monkeypatch.setattr(respond_module, "is_llm_mode", lambda: True)
    monkeypatch.setattr(respond_module, "structured_call", lambda *a, **k: ResponseDraft(customer_reply=reply, internal_note="모델 메모"))


def test_a_bad_llm_draft_is_replaced_by_the_code_template(monkeypatch):
    state = run("S-6")
    llm_draft(monkeypatch, "부하 전류를 알려 주세요. [source]")
    draft = run_respond(state)
    assert draft.customer_reply == state["response"]["customer_reply"]  # mock이 만든 템플릿과 같다
    assert "템플릿 답변으로 바꿨습니다" in draft.internal_note and "none_not_stated" in draft.internal_note and "모델 메모" in draft.internal_note


def test_a_good_llm_draft_is_kept(monkeypatch):
    state = run("S-6")
    good = state["response"]["customer_reply"] + "\n\n(모델이 다듬은 문장)"
    llm_draft(monkeypatch, good)
    draft = run_respond(state)
    assert draft.customer_reply == good and "템플릿 답변으로" not in draft.internal_note


def test_a_requested_revision_is_not_overwritten(monkeypatch):
    state = {**run("S-6"), "review": {"action": "revise", "comment": "더 짧게"}}
    llm_draft(monkeypatch, "짧은 답변입니다.")
    draft = run_respond(state)
    assert draft.customer_reply == "짧은 답변입니다." and "수정 요청 중이라 그대로 두었습니다" in draft.internal_note


def test_technical_replies_are_not_checked(monkeypatch):
    state = run("S-6")
    state["intake"] = {**state["intake"], "category": "technical", "route": "fae"}
    llm_draft(monkeypatch, "아무 답변")
    assert run_respond(state).customer_reply == "아무 답변"
