"""Celery 앱: 그래프 실행(수십 초 + 사람 승인 대기)을 웹 요청 밖의 작업자에서 돌린다."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("fae")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
