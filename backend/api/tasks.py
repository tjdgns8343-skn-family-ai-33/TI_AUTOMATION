from celery import shared_task

from . import services


@shared_task
def process_run(run_id: str) -> None:
    """새 문의를 처음부터 처리한다 (사람 승인 단계에서 멈춘다)."""
    services.execute(run_id)


@shared_task
def resume_run(run_id: str, action: str, comment: str = "") -> None:
    """사람 승인 결과(approve·revise·reject)로 멈춘 그래프를 이어서 실행한다."""
    services.execute(run_id, resume={"action": action, "comment": comment})
