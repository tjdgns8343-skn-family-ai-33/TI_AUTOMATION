"""그래프 실행 서비스: Celery 작업자가 부른다. LangGraph 진행 상태는 Postgres에, 화면용 상태는 Run.state에 저장한다."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from .models import Run

logger = logging.getLogger(__name__)

_MEMORY_SAVER: Any = None


@contextmanager
def checkpointer():
    """진행 상태 저장소. Postgres가 설정돼 있으면 PostgresSaver, 없으면(테스트) 프로세스 메모리."""
    if settings.POSTGRES_URI:
        from langgraph.checkpoint.postgres import PostgresSaver

        with PostgresSaver.from_conn_string(settings.POSTGRES_URI) as saver:
            saver.setup()  # 표가 없으면 만들고, 있으면 그대로 둔다
            yield saver
    else:
        global _MEMORY_SAVER
        if _MEMORY_SAVER is None:
            from langgraph.checkpoint.memory import InMemorySaver

            _MEMORY_SAVER = InMemorySaver()
        yield _MEMORY_SAVER


@contextmanager
def usage_tracking(mode: str, totals: dict[str, int]):
    """llm 모드에서 이번 실행이 쓴 토큰을 totals에 더한다 (실패해도 쓴 만큼은 남긴다)."""
    if mode != "llm":
        yield
        return
    from langchain_core.callbacks import get_usage_metadata_callback

    with get_usage_metadata_callback() as callback:
        try:
            yield
        finally:
            for usage in callback.usage_metadata.values():
                totals["input"] += int(usage.get("input_tokens", 0) or 0)
                totals["output"] += int(usage.get("output_tokens", 0) or 0)


def _jsonable(data: Any) -> Any:
    return json.loads(json.dumps(data, default=str, ensure_ascii=False))


def _save_progress(run: Run, state: dict[str, Any]) -> None:
    run.state = _jsonable(state)
    run.ticket_id = state.get("ticket_id") or run.ticket_id
    run.save(update_fields=["state", "ticket_id", "updated_at"])


def _run_graph(run: Run, resume: dict[str, str] | None, totals: dict[str, int]) -> None:
    from fae_agent.graph import build_graph
    from fae_agent.schemas import Complaint
    from fae_agent.tools.gateway import ToolGateway

    config = {"configurable": {"thread_id": run.thread_id}, "recursion_limit": 100}
    if resume is None:
        complaint = Complaint(customer=run.customer, subject=run.subject, body=run.body)
        inputs: Any = {"complaint": complaint.model_dump(), "log": []}
    else:
        from langgraph.types import Command

        inputs = Command(resume=resume)

    with checkpointer() as saver, usage_tracking(run.mode, totals):
        graph = build_graph(ToolGateway("local"), saver)
        for chunk in graph.stream(inputs, config=config, stream_mode="values"):
            if "__interrupt__" not in chunk:
                _save_progress(run, chunk)  # 단계가 끝날 때마다 저장해 화면이 진행 상황을 볼 수 있게 한다
        snapshot = graph.get_state(config)

    state = _jsonable(snapshot.values)
    interrupts = [item for task in snapshot.tasks for item in task.interrupts]
    if snapshot.next and interrupts:
        run.status = Run.Status.AWAITING_REVIEW
        run.pending_review = _jsonable(interrupts[0].value)
    else:
        run.pending_review = None
        run.status = Run.Status.REJECTED if state.get("status") == "rejected" else Run.Status.COMPLETED
        run.finished_at = timezone.now()
    run.state = state
    run.ticket_id = state.get("ticket_id") or run.ticket_id
    run.save()


def execute(run_id: str, resume: dict[str, str] | None = None) -> None:
    """Run 한 건을 그래프로 처리한다. resume이 있으면 사람 승인 결과로 멈춘 그래프를 이어서 실행한다."""
    run = Run.objects.get(pk=run_id)
    run.status = Run.Status.RUNNING
    run.error = ""
    run.save(update_fields=["status", "error", "updated_at"])
    totals = {"input": 0, "output": 0}
    try:
        _run_graph(run, resume, totals)
    except Exception as exc:  # 실패해도 작업자가 죽지 않고 화면에 이유를 보여 준다
        logger.exception("Run %s 처리 실패", run_id)
        run.refresh_from_db()
        run.status = Run.Status.FAILED
        run.error = f"{type(exc).__name__}: {exc}"[:2000]
        run.finished_at = timezone.now()
        run.save()
    finally:
        Run.objects.filter(pk=run_id).update(
            input_tokens=F("input_tokens") + totals["input"], output_tokens=F("output_tokens") + totals["output"]
        )
