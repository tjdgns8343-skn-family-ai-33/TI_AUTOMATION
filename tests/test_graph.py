"""LangGraph로 연결한 전체 흐름 테스트 (langgraph가 설치돼 있어야 실행됩니다)."""

import json

import pytest

pytest.importorskip("langgraph")

from fae_agent.config import get_settings  # noqa: E402
from fae_agent.graph import build_graph, new_thread_id, pending_review, resume_run, start_run  # noqa: E402
from fae_agent.schemas import Complaint  # noqa: E402


def cases() -> list[dict]:
    lines = get_settings().eval_path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def complaint_for(case_id: str) -> Complaint:
    case = next(c for c in cases() if c["id"] == case_id)
    return Complaint(customer=case["customer"], subject=case["subject"], body=case["body"])


def test_waits_for_human_then_approves():
    graph, thread_id = build_graph(), new_thread_id()
    result = start_run(graph, complaint_for("E-02"), thread_id)
    review = pending_review(result)
    assert review is not None and review["draft"]["customer_reply"]
    final = resume_run(graph, thread_id, "approve")
    assert pending_review(final) is None
    assert final["status"] == "answered"


def test_revise_then_reject():
    graph, thread_id = build_graph(), new_thread_id()
    start_run(graph, complaint_for("E-05"), thread_id)
    revised = resume_run(graph, thread_id, "revise", "사과 문장을 더 짧게")
    assert pending_review(revised) is not None  # 수정 후 다시 승인 대기
    final = resume_run(graph, thread_id, "reject")
    assert final["status"] == "rejected"


def test_dropping_tests_after_a_test_error_is_sent_back(monkeypatch):
    """H-A1 사례: 테스트 오류 뒤 재분석이 테스트를 모두 빼면 검수가 되돌리고, 테스트를 살린 시도만 통과한다."""
    import fae_agent.graph as graph_module
    from fae_agent.schemas import Bound, CheckStep

    def plan(*steps: CheckStep) -> dict:
        hypothesis = {"cause": "폐루프 대역폭 한계", "evidence": [{"source": "LMV321A 데이터시트"}]}
        return {
            "hypotheses": [hypothesis],
            "solution": {"summary": "주파수를 낮춘다", "test_plan": [s.model_dump() for s in steps]},
            "meta": {"candidate_index": 0, "strength": 1, "rating": False},
        }

    args = '{"rf_ohm": 9000, "rg_ohm": 1000, "gbw_hz": 1e6, "freq_hz": 200000}'
    wrong_metric = CheckStep(
        label="이득 재현", purpose="reproduce", tool="calc.opamp_closed_loop_gain", args_json=args,
        expect=[Bound(metric="없는_지표", max=0.9)],
    )  # fmt: skip
    fixed = wrong_metric.model_copy(update={"expect": [Bound(metric="gain_ratio", max=0.9)]})
    attempts = iter([plan(wrong_metric), plan(), plan(fixed)])
    seen_feedback: list = []

    def fake_diagnose(complaint, intake, research, gateway, previous=None, qa=None, design=None):
        seen_feedback.append((qa or {}).get("issue_codes", []))
        return next(attempts)

    monkeypatch.setattr(graph_module, "run_diagnose", fake_diagnose)
    complaint = Complaint(subject="LMV321A", body="LMV321A로 비반전 게인 10 증폭기를 만들었는데 200kHz에서 출력 전압이 이상합니다.")
    result = start_run(build_graph(), complaint, new_thread_id())

    verdicts = [line for line in result["log"] if line.startswith("[검수]")]
    assert verdicts == ["[검수] 미통과 → test_error", "[검수] 미통과 → tests_dropped", "[검수] 통과"]
    assert seen_feedback == [[], ["test_error"], ["tests_dropped"]]  # 지적 내용이 다음 분석에 전달된다
    assert result["check_report"]["steps"][0]["status"] == "pass"


def test_all_eval_cases_with_auto_approve(monkeypatch):
    monkeypatch.setenv("AUTO_APPROVE", "true")
    get_settings.cache_clear()
    graph = build_graph()
    for case in cases():
        complaint = Complaint(customer=case["customer"], subject=case["subject"], body=case["body"])
        result = start_run(graph, complaint, new_thread_id())
        assert result["status"] == "answered", case["id"]
        assert result["intake"]["category"] == case["expected_category"], case["id"]
        causes = " ".join(h["cause"] for h in (result.get("diagnosis") or {}).get("hypotheses", []))
        if case["expected_cause_keywords"]:
            assert any(k in causes for k in case["expected_cause_keywords"]), case["id"]
