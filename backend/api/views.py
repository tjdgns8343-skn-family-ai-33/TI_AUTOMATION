import uuid

from django.conf import settings
from django.db import connection, transaction
from django.db.models import F, Sum
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from fae_agent.config import get_settings as fae_settings

from .models import Review, Run
from .samples import list_samples
from .serializers import (
    ReviewRequestSerializer,
    RunCreateSerializer,
    RunDetailSerializer,
    RunListSerializer,
)
from .tasks import process_run, resume_run


def tokens_used_today() -> int:
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    total = Run.objects.filter(created_at__gte=start).aggregate(total=Sum(F("input_tokens") + F("output_tokens")))
    return total["total"] or 0


class HealthView(APIView):
    @extend_schema(responses={200: dict})
    def get(self, request):
        try:
            connection.ensure_connection()
            database = True
        except Exception:
            database = False
        body = {"status": "ok" if database else "degraded", "database": database, "time": timezone.now()}
        return Response(body, status=200 if database else 503)


class ConfigView(APIView):
    @extend_schema(responses={200: dict})
    def get(self, request):
        conf = fae_settings()
        return Response(
            {
                "fae_mode": conf.fae_mode,
                "llm_model": conf.llm_model if conf.fae_mode == "llm" else "",
                "tool_mode": conf.tool_mode,
                "daily_token_budget": settings.FAE_DAILY_TOKEN_BUDGET,
                "tokens_used_today": tokens_used_today(),
            }
        )


class SamplesView(APIView):
    @extend_schema(responses={200: dict(type="array")})
    def get(self, request):
        return Response(list_samples())


class RunViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Run.objects.all()

    def get_serializer_class(self):
        if self.action == "create":
            return RunCreateSerializer
        if self.action == "list":
            return RunListSerializer
        if self.action == "review":
            return ReviewRequestSerializer
        return RunDetailSerializer

    def list(self, request, *args, **kwargs):
        return Response(RunListSerializer(self.get_queryset()[:50], many=True).data)

    @extend_schema(request=RunCreateSerializer, responses={201: RunDetailSerializer})
    def create(self, request, *args, **kwargs):
        serializer = RunCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mode = fae_settings().fae_mode
        if mode == "llm" and tokens_used_today() >= settings.FAE_DAILY_TOKEN_BUDGET:
            return Response(
                {"detail": f"오늘 쓸 수 있는 토큰({settings.FAE_DAILY_TOKEN_BUDGET:,})을 모두 썼습니다. 내일 다시 시도해 주세요."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        run = Run.objects.create(**serializer.validated_data, mode=mode, thread_id=uuid.uuid4().hex)
        process_run.delay(str(run.id))
        run.refresh_from_db()
        return Response(RunDetailSerializer(run).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ReviewRequestSerializer, responses={200: RunDetailSerializer})
    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        serializer = ReviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            run = Run.objects.select_for_update().get(pk=pk)
            if run.status != Run.Status.AWAITING_REVIEW:
                return Response(
                    {"detail": f"승인 대기 상태가 아닙니다 (현재: {run.get_status_display()})."},
                    status=status.HTTP_409_CONFLICT,
                )
            Review.objects.create(run=run, action=data["action"], comment=data["comment"])
            run.status = Run.Status.RUNNING
            run.save(update_fields=["status", "updated_at"])
        resume_run.delay(str(run.id), data["action"], data["comment"])  # 커밋된 뒤에 작업을 보낸다
        run.refresh_from_db()
        return Response(RunDetailSerializer(run).data)
