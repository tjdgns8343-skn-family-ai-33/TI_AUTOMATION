"""영업·기술지원 워크플로 도구: 문의 접수 → 초안 → 사람 승인 → (승인되면) 문서화를 외부 MCP 클라이언트가 단계별로 부른다 (서버 이름: fae_workflow).

- **상태를 가진다.** 그래프와 승인 대기 중인 건은 서버 프로세스 메모리에 있다. 서버를 끄면 대기 중인 건은 사라진다
  (티켓 기록과 이미 만든 문서는 남는다). 그래서 ToolGateway(호출마다 서버를 새로 띄움)에는 등록하지 않고, Claude Desktop 같은 클라이언트가 직접 연결해 쓴다.
- **승인 게이트는 그래프가 강제한다.** `submit_inquiry`는 승인 단계에서 멈추고, 문서는 `review(approve)` 뒤에만 만들어진다. 문서를 직접 만드는 도구는 없다.
- 한계: MCP 서버는 호출하는 쪽이 사람인지 모델인지 구별하지 못한다. 클라이언트가 도구 호출마다 사람에게 확인을 받는 설정이어야 게이트가 의미가 있고,
  `review`는 검토자 이름(reviewer)을 받아 승인 기록에 남긴다.
- FAE_MODE=llm이면 접수가 OpenAI 요금을 쓴다. 서버를 띄운 환경에 FAE_ALLOW_LLM=1이 없으면 거부한다 (CLI의 --allow-llm과 같은 안전장치).
- AUTO_APPROVE=true면 사람 승인을 건너뛰므로 거부한다.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import Any

from ..config import get_settings

DECISIONS = ("approve", "revise", "reject")
_LOCK = threading.RLock()
_STATE: dict[str, Any] = {"graph": None, "runs": {}}


def _refuse() -> dict[str, Any] | None:
    settings = get_settings()
    if settings.auto_approve:
        return {"error": "AUTO_APPROVE=true라서 사람 승인을 건너뜁니다. 이 서버는 사람 승인이 필요하므로 끄고 다시 시작하세요."}
    if settings.fae_mode == "llm" and os.environ.get("FAE_ALLOW_LLM") != "1":
        return {"error": "FAE_MODE=llm이면 문의 접수가 요금이 나가는 LLM을 씁니다. 요금을 감수하면 서버 환경에 FAE_ALLOW_LLM=1을 설정하고 다시 시작하세요."}
    return None


def _graph():
    from ..graph import build_graph

    if _STATE["graph"] is None:
        _STATE["graph"] = build_graph()
    return _STATE["graph"]


def _snapshot(thread_id: str, result: dict[str, Any]) -> dict[str, Any]:
    from ..graph import pending_review

    pending = pending_review(result)
    status = "awaiting_review" if pending else result.get("status", "unknown")
    intake = result.get("intake") or {}
    response = result.get("response") or {}
    snap: dict[str, Any] = {
        "thread_id": thread_id,
        "ticket_id": result.get("ticket_id", ""),
        "status": status,
        "category": intake.get("category", ""),
        "route": intake.get("route", ""),
        "draft": {"customer_reply": response.get("customer_reply", ""), "internal_note": response.get("internal_note", "")},
    }
    if result.get("sales"):
        snap["sales"] = result["sales"]
    if result.get("recommendation"):
        snap["recommendation"] = result["recommendation"]
    if result.get("diagnosis"):
        snap["causes"] = [h.get("cause", "") for h in (result["diagnosis"].get("hypotheses") or [])]
    if result.get("qa"):
        snap["qa"] = {"passed": result["qa"].get("passed"), "issues": result["qa"].get("issues", []), "warnings": result["qa"].get("warnings", [])}
    if result.get("cpr"):
        snap["cpr_missing"] = [item for item, ok in (result["cpr"].get("checklist") or {}).items() if not ok]
    if pending:
        snap["options"] = list(DECISIONS)
    if result.get("review"):
        snap["review"] = result["review"]
    if result.get("documents"):
        snap["documents"] = result["documents"]
    return snap


def submit_inquiry(customer: str, subject: str, body: str) -> dict[str, Any]:
    """고객 문의를 접수해 분류·분석·답변 초안까지 만들고 사람 승인 단계에서 멈춘다. thread_id와 초안을 돌려준다. 이 단계에서는 문서가 만들어지지 않는다."""
    if error := _refuse():
        return error
    if not (body or "").strip():
        return {"error": "문의 본문(body)이 비어 있습니다."}
    from ..graph import new_thread_id, start_run
    from ..schemas import Complaint

    with _LOCK:
        thread_id = new_thread_id()
        result = start_run(_graph(), Complaint(customer=customer, subject=subject, body=body), thread_id)
        snap = _snapshot(thread_id, result)
        _STATE["runs"][thread_id] = {"snapshot": snap, "subject": subject, "submitted_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    return snap


def get_draft(thread_id: str) -> dict[str, Any]:
    """접수한 건의 현재 상태와 최신 초안을 다시 본다."""
    run = _STATE["runs"].get(thread_id)
    return run["snapshot"] if run else {"error": f"알 수 없는 thread_id: {thread_id} (서버를 다시 시작하면 승인 대기 건은 사라집니다)"}


def list_pending() -> dict[str, Any]:
    """사람 승인을 기다리는 건 목록."""
    pending = [
        {"thread_id": tid, "ticket_id": run["snapshot"]["ticket_id"], "category": run["snapshot"]["category"], "subject": run["subject"], "submitted_at": run["submitted_at"]}
        for tid, run in _STATE["runs"].items()
        if run["snapshot"]["status"] == "awaiting_review"
    ]
    return {"pending": pending}


def review(thread_id: str, decision: str, reviewer: str, comment: str = "") -> dict[str, Any]:
    """승인 대기 건에 사람의 결정을 전달한다. decision: approve(승인, 문서화 진행), revise(수정 요청, comment대로 답변을 다시 쓰고 다시 승인 대기), reject(반려, 문서 없음). 초안을 읽은 사람이 reviewer 이름과 함께 호출해야 한다."""
    if error := _refuse():
        return error
    if decision not in DECISIONS:
        return {"error": f"decision은 {', '.join(DECISIONS)} 중 하나여야 합니다 (받은 값: {decision!r})."}
    if not (reviewer or "").strip():
        return {"error": "검토자 이름(reviewer)이 필요합니다. 승인 기록에 남습니다."}
    from ..graph import resume_run

    with _LOCK:
        run = _STATE["runs"].get(thread_id)
        if run is None:
            return {"error": f"알 수 없는 thread_id: {thread_id}"}
        if run["snapshot"]["status"] != "awaiting_review":
            return {"error": f"승인 대기 건이 아닙니다 (현재 상태: {run['snapshot']['status']}). 이미 결정된 건은 다시 결정할 수 없습니다."}
        result = resume_run(_graph(), thread_id, decision, f"[{reviewer.strip()}] {comment}".strip())
        run["snapshot"] = _snapshot(thread_id, result)
        return run["snapshot"]


WORKFLOW_TOOLS = {fn.__name__: fn for fn in (submit_inquiry, get_draft, list_pending, review)}
