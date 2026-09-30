"""Django 설정. 값은 환경변수에서 읽는다 (Docker Compose와 AWS에서 같은 이미지를 환경변수만 바꿔 쓴다)."""

import os
import sys
from pathlib import Path
from urllib.parse import quote

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent  # fae_agent, circuits, data가 있는 곳 (Docker 이미지도 같은 배치)
if (PROJECT_ROOT / "fae_agent").is_dir() and str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


def env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# dev: 개발(DEBUG 켜짐, 예비 SECRET_KEY 허용) / production: 배포(SECRET_KEY 필수)
ENVIRONMENT = os.environ.get("DJANGO_ENV", "dev")
DEBUG = env_bool("DJANGO_DEBUG", ENVIRONMENT == "dev")
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or ("dev-only-insecure-key" if ENVIRONMENT == "dev" else "")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_ENV=production에서는 DJANGO_SECRET_KEY가 필요합니다.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver,backend")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

# 데이터베이스: POSTGRES_HOST가 있으면 Postgres, 없으면 SQLite (로컬 개발·테스트)
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "")
if POSTGRES_HOST:
    _pg = {
        "name": os.environ.get("POSTGRES_DB", "fae"),
        "user": os.environ.get("POSTGRES_USER", "fae"),
        "password": os.environ.get("POSTGRES_PASSWORD", ""),
        "port": os.environ.get("POSTGRES_PORT", "5432"),
    }
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _pg["name"],
            "USER": _pg["user"],
            "PASSWORD": _pg["password"],
            "HOST": POSTGRES_HOST,
            "PORT": _pg["port"],
            "CONN_MAX_AGE": 60,
        }
    }
    # LangGraph 진행 상태(체크포인트)도 같은 Postgres에 저장한다
    POSTGRES_URI = f"postgresql://{quote(_pg['user'])}:{quote(_pg['password'])}@{POSTGRES_HOST}:{_pg['port']}/{_pg['name']}"
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
    POSTGRES_URI = ""

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],  # 접근 보호는 앞단(nginx 비밀번호, 보안 그룹)에서 한다
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}
SPECTACULAR_SETTINGS = {
    "TITLE": "FAE Agent API",
    "DESCRIPTION": "반도체 고객 기술 문의를 접수·분석하고 사람 승인까지 처리하는 API",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# Celery: 그래프 실행은 웹 요청 밖의 작업자에서 돈다. CELERY_TASK_ALWAYS_EAGER=1이면 그 자리에서 바로 실행 (테스트)
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_IGNORE_RESULT = True  # 결과는 Run 테이블에 저장한다
CELERY_TASK_TIME_LIMIT = 900
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# 하루에 쓸 수 있는 LLM 토큰 상한 (llm 모드에서만 적용). 넘으면 새 문의를 받지 않는다
FAE_DAILY_TOKEN_BUDGET = int(os.environ.get("FAE_DAILY_TOKEN_BUDGET", "300000"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO")},
}
