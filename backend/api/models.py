import uuid

from django.db import models


class Ticket(models.Model):
    """문의 건. fae_agent의 tickets 도구가 만들고 갱신한다 (예전 SQLite tickets.db를 대신함)."""

    id = models.CharField(primary_key=True, max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)
    customer = models.CharField(max_length=200, blank=True, default="")
    subject = models.CharField(max_length=300, blank=True, default="")
    body = models.TextField(blank=True, default="")
    category = models.CharField(max_length=40, blank=True, default="")
    route = models.CharField(max_length=40, blank=True, default="")
    status = models.CharField(max_length=40, default="received")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.id} {self.subject}"


class TicketEvent(models.Model):
    """티켓의 처리 단계 기록."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="events")
    ts = models.DateTimeField(auto_now_add=True)
    stage = models.CharField(max_length=40)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["id"]


class Run(models.Model):
    """문의 한 건의 처리 실행. 그래프가 돌아가는 동안 state가 계속 갱신되어 화면이 진행 상황을 보여 준다."""

    class Status(models.TextChoices):
        QUEUED = "queued", "대기 중"
        RUNNING = "running", "처리 중"
        AWAITING_REVIEW = "awaiting_review", "승인 대기"
        COMPLETED = "completed", "완료"
        REJECTED = "rejected", "반려"
        FAILED = "failed", "실패"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.CharField(max_length=200, blank=True, default="")
    subject = models.CharField(max_length=300, blank=True, default="")
    body = models.TextField()
    mode = models.CharField(max_length=10, default="mock")  # mock | llm (접수 시점의 서버 설정)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    thread_id = models.CharField(max_length=64)  # LangGraph 진행 상태를 찾는 키
    ticket_id = models.CharField(max_length=40, blank=True, default="")
    state = models.JSONField(default=dict, blank=True)  # LangGraph 상태 (접수·조사·분석·테스트·검수·응답 …)
    pending_review = models.JSONField(null=True, blank=True)  # 사람 승인을 기다리는 초안과 검수 결과
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.id} [{self.status}] {self.subject}"


class Review(models.Model):
    """사람 승인 기록 (누가 언제 무엇을 결정했는지 남긴다)."""

    class Action(models.TextChoices):
        APPROVE = "approve", "승인"
        REVISE = "revise", "수정 요청"
        REJECT = "reject", "반려"

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="reviews")
    action = models.CharField(max_length=10, choices=Action.choices)
    comment = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
