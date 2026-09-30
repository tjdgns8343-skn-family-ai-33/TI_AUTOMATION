from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"

    def ready(self) -> None:
        # 그래프가 부르는 tickets.* 도구를 SQLite 대신 Django 모델(Postgres)로 바꿔 끼운다
        from . import ticket_backend

        ticket_backend.install()
