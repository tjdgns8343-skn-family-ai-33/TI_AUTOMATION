"""테스트 전용 설정: Celery 작업을 Redis 없이 그 자리에서 실행하고, 항상 SQLite와 메모리 체크포인터를 쓴다."""

from .settings import *

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
POSTGRES_URI = ""
