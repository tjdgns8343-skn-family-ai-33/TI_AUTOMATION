"""LangGraph로 에이전트를 연결합니다.

흐름
  접수 ─┬─ 기술 문의 → 기술 조사 → 원인 분석 → 테스트 → 검수 ─┬─ 통과 → 응답
        │                          ↑______ 재분석(최대 N회) ____│
        ├─ 품질 클레임 → 정격 점검 → CPR 점검 → 응답
        └─ 배송·기타 → 응답
  응답 → 사람 승인(interrupt) ─┬─ 승인·반려 → 마무리
                               └─ 수정 요청 → 응답
"""

from __future__ import annotations

import uuid
from typing import Any

from .agents.checker import run_checks
from .agents.cpr import run_cpr
from .agents.diagnose import run_diagnose
from .agents.intake import run_intake
from .agents.qa import run_qa
from .agents.research import run_research
from .agents.respond import run_respond
from .config import get_settings
from .ratings import rating_facts
from .schemas import CheckReport, Complaint, Diagnosis, IntakeResult, Solution
from .state import FAEState
from .tools.gateway import ToolGateway

# 노드 이름은 상태 키와 겹치면 안 됩니다 (LangGraph 규칙)
INTAKE, RESEARCH, DIAGNOSE, CHECK, QA = "intake_agent", "research_agent", "diagnose_agent", "check_agent", "qa_agent"
CPR, RESPOND, REVIEW, FINALIZE = "cpr_agent", "respond_agent", "human_review", "finalize"
RATING = "rating_agent"


def _intake(state: FAEState) -> IntakeResult:
    return IntakeResult.model_validate(state["intake"])


def _complaint(state: FAEState) -> Complaint:
    return Complaint.model_validate(state["complaint"])


def build_graph(gateway: ToolGateway | None = None, checkpointer: Any = None):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    gw = gateway or ToolGateway()
    settings = get_settings()

    def intake_node(state: FAEState) -> dict[str, Any]:
        complaint = _complaint(state)
        ticket_id = (
            state.get("ticket_id")
            or gw.call(
                "tickets.create_ticket", customer=complaint.customer, subject=complaint.subject, body=complaint.body
            )["ticket_id"]
        )
        result = run_intake(complaint)
        gw.call(
            "tickets.update_ticket",
            ticket_id=ticket_id,
            category=result.category,
            route=result.route,
            status="in_progress",
        )
        gw.call("tickets.add_event", ticket_id=ticket_id, stage="intake", payload=result.model_dump())
        return {
            "ticket_id": ticket_id,
            "intake": result.model_dump(),
            "qa_retries": 0,
            "log": [f"[접수] {result.summary}"],
        }

    def research_node(state: FAEState) -> dict[str, Any]:
        research = run_research(_complaint(state), _intake(state), gw)
        return {
            "research": research,
            "log": [f"[조사] 문서 {len(research['evidence'])}건, 사례 {len(research['cases'])}건"],
        }

    def diagnose_node(state: FAEState) -> dict[str, Any]:
        diagnosis = run_diagnose(
            _complaint(state),
            _intake(state),
            state["research"],
            gw,
            previous=state.get("diagnosis"),
            qa=state.get("qa"),
        )
        top = diagnosis["hypotheses"][0]["cause"] if diagnosis["hypotheses"] else "없음"
        return {"diagnosis": diagnosis, "log": [f"[분석] 1순위 원인: {top}"]}

    def check_node(state: FAEState) -> dict[str, Any]:
        solution_data = state["diagnosis"].get("solution")
        solution = Solution.model_validate(solution_data) if solution_data else None
        report = run_checks(solution, gw)
        return {"check_report": report.model_dump(), "log": [f"[테스트] {report.status} ({len(report.steps)}단계)"]}

    def qa_node(state: FAEState) -> dict[str, Any]:
        review = run_qa(
            _intake(state),
            state["research"],
            Diagnosis.model_validate(state["diagnosis"]),
            CheckReport.model_validate(state["check_report"]),
            previous_codes=(state.get("qa") or {}).get("issue_codes", []),  # 재분석이면 바로 앞 시도의 지적
        )
        retries = state.get("qa_retries", 0) + (0 if review.passed else 1)
        verdict = "통과" if review.passed else f"미통과 → {', '.join(review.issue_codes)}"
        return {"qa": review.model_dump(), "qa_retries": retries, "log": [f"[검수] {verdict}"]}

    def rating_node(state: FAEState) -> dict[str, Any]:
        """품질 클레임도 고객이 알려 준 조건을 부품 카드의 한계와 먼저 비교한다 (사용 조건 문제를 불량으로 착각하지 않도록).

        경로는 바꾸지 않고 결과만 붙인다. 불량 여부는 사람이 판단한다.
        """
        intake = _intake(state)
        part = intake.part_numbers[0] if intake.part_numbers else ""
        card = gw.call("kb.get_part_card", part_number=part)["card"] if part else None
        facts = rating_facts(intake, card or {})
        check = {
            "part_number": part,
            "found": card is not None,
            "source": (card or {}).get("_source"),  # 내부 파일 경로 (고객용 답변에는 쓰지 않는다)
            "source_text": (card or {}).get("source"),  # 데이터시트 이름·페이지 (고객용 답변에 인용)
            **facts,
        }
        log = f"[정격 점검] 절대 최대 초과 {len(facts['abs_over'])}건" if card else "[정격 점검] 부품 카드 없음"
        return {"rating_check": check, "log": [log]}

    def cpr_node(state: FAEState) -> dict[str, Any]:
        check = run_cpr(_complaint(state), _intake(state), state.get("rating_check"))
        return {"cpr": check.model_dump(), "log": [f"[품질 클레임] 부족한 자료 {len(check.missing)}건"]}

    def respond_node(state: FAEState) -> dict[str, Any]:
        draft = run_respond(dict(state))
        return {"response": draft.model_dump(), "log": ["[응답] 고객 답변 초안 작성"]}

    def review_node(state: FAEState) -> dict[str, Any]:
        # 재개(resume)하면 이 노드가 처음부터 다시 실행되므로 interrupt 앞에 부작용을 두지 않습니다.
        if settings.auto_approve:
            decision: Any = {"action": "approve", "comment": "AUTO_APPROVE"}
        else:
            decision = interrupt(
                {
                    "ticket_id": state.get("ticket_id"),
                    "draft": state.get("response"),
                    "qa": state.get("qa"),
                    "options": ["approve", "revise", "reject"],
                }
            )
        if isinstance(decision, str):
            decision = {"action": decision, "comment": ""}
        action = decision.get("action", "approve")
        if action not in ("approve", "revise", "reject"):
            action = "approve"
        review = {"action": action, "comment": decision.get("comment", "")}
        return {"review": review, "log": [f"[사람 검토] {action}"]}

    def finalize_node(state: FAEState) -> dict[str, Any]:
        action = (state.get("review") or {}).get("action", "approve")
        status = "answered" if action == "approve" else "rejected"
        ticket_id = state["ticket_id"]
        gw.call("tickets.update_ticket", ticket_id=ticket_id, status=status)
        gw.call(
            "tickets.add_event",
            ticket_id=ticket_id,
            stage="final",
            payload={"review": state.get("review"), "response": state.get("response")},
        )
        return {"status": status, "log": [f"[완료] {status}"]}

    def route_after_intake(state: FAEState) -> str:
        category = state["intake"]["category"]
        return {"technical": RESEARCH, "quality_claim": RATING}.get(category, RESPOND)

    def route_after_qa(state: FAEState) -> str:
        if state["qa"]["passed"] or state.get("qa_retries", 0) > settings.max_qa_retries:
            return RESPOND
        return DIAGNOSE

    def route_after_review(state: FAEState) -> str:
        return RESPOND if state["review"]["action"] == "revise" else FINALIZE

    builder = StateGraph(FAEState)
    for name, node in (
        (INTAKE, intake_node),
        (RESEARCH, research_node),
        (DIAGNOSE, diagnose_node),
        (CHECK, check_node),
        (QA, qa_node),
        (RATING, rating_node),
        (CPR, cpr_node),
        (RESPOND, respond_node),
        (REVIEW, review_node),
        (FINALIZE, finalize_node),
    ):
        builder.add_node(name, node)
    builder.add_edge(START, INTAKE)
    builder.add_conditional_edges(INTAKE, route_after_intake, {RESEARCH: RESEARCH, RATING: RATING, RESPOND: RESPOND})
    builder.add_edge(RATING, CPR)
    builder.add_edge(RESEARCH, DIAGNOSE)
    builder.add_edge(DIAGNOSE, CHECK)
    builder.add_edge(CHECK, QA)
    builder.add_conditional_edges(QA, route_after_qa, {DIAGNOSE: DIAGNOSE, RESPOND: RESPOND})
    builder.add_edge(CPR, RESPOND)
    builder.add_edge(RESPOND, REVIEW)
    builder.add_conditional_edges(REVIEW, route_after_review, {RESPOND: RESPOND, FINALIZE: FINALIZE})
    builder.add_edge(FINALIZE, END)
    return builder.compile(checkpointer=checkpointer or InMemorySaver())


# ---------------------------------------------------------------- 실행 도우미 (CLI·Streamlit 공용)


def new_thread_id() -> str:
    return uuid.uuid4().hex


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}


def start_run(graph: Any, complaint: Complaint, thread_id: str) -> dict[str, Any]:
    """새 문의를 처리합니다. 사람 승인 단계에서 멈추면 결과에 '__interrupt__'가 들어 있습니다."""
    return graph.invoke({"complaint": complaint.model_dump(), "log": []}, config=_config(thread_id))


def resume_run(graph: Any, thread_id: str, action: str, comment: str = "") -> dict[str, Any]:
    """사람 검토 결과(approve·revise·reject)로 멈춘 그래프를 이어서 실행합니다."""
    from langgraph.types import Command

    return graph.invoke(Command(resume={"action": action, "comment": comment}), config=_config(thread_id))


def pending_review(result: dict[str, Any]) -> dict[str, Any] | None:
    """사람 승인을 기다리는 중이면 그 내용(초안 등)을, 아니면 None을 돌려줍니다."""
    items = result.get("__interrupt__")
    if not items:
        return None
    first = items[0]
    return getattr(first, "value", first)
