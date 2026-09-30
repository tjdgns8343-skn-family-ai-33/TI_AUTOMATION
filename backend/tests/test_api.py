"""API 흐름 테스트: 문의 접수 → 그래프 실행 → 승인 대기 → 승인·수정 요청·반려 (mock 모드, Celery는 즉시 실행)."""

import pytest
from api.models import Review, Run, Ticket
from rest_framework.test import APIClient

from fae_agent.config import get_settings

pytestmark = pytest.mark.django_db

LDO = {
    "customer": "가상전자",
    "subject": "EXAMPLE-LDO1 과열",
    "body": "EXAMPLE-LDO1에 입력 7V, 출력 3.3V, 부하 0.3A로 쓰는데 IC가 너무 뜨겁습니다.",
}
QUALITY = {
    "customer": "가상모터스",
    "subject": "LM2596 손상",
    "body": "LM2596을 입력 48V, 출력 5V, 부하 1A로 쓰다가 IC가 불량으로 타 버렸습니다. 반품 요청합니다.",
}


@pytest.fixture
def client():
    return APIClient()


def create(client, payload=LDO):
    response = client.post("/api/runs/", payload, format="json")
    assert response.status_code == 201, response.content
    return response.json()


def test_health(client):
    response = client.get("/api/health/")
    assert response.status_code == 200 and response.json()["database"] is True


def test_config_reports_mode_and_budget(client):
    body = client.get("/api/config/").json()
    assert body["fae_mode"] == "mock" and body["tool_mode"] == "local"
    assert body["daily_token_budget"] > 0 and body["tokens_used_today"] == 0


def test_samples_do_not_leak_ground_truth(client):
    items = client.get("/api/samples/").json()
    assert len(items) >= 4 and all({"id", "group", "subject", "body"} <= set(i) for i in items)
    assert "ground_truth" not in str(items)


def test_run_reaches_review_with_full_state(client):
    run = create(client)
    detail = client.get(f"/api/runs/{run['id']}/").json()
    assert detail["status"] == "awaiting_review"
    assert detail["category"] == "technical" and detail["ticket_id"].startswith("TCK-")
    state = detail["state"]
    assert {"intake", "research", "diagnosis", "check_report", "qa"} <= set(state)
    assert any("[테스트]" in line for line in state["log"])
    assert detail["pending_review"]["draft"]["customer_reply"]
    assert detail["input_tokens"] == 0 and detail["output_tokens"] == 0  # mock은 토큰을 쓰지 않는다


def test_tickets_are_stored_in_django_models(client):
    run = create(client)
    ticket = Ticket.objects.get(pk=run["ticket_id"])
    assert ticket.category == "technical" and ticket.status == "in_progress"
    assert ticket.events.filter(stage="intake").exists()


def test_approve_completes_and_records_review(client):
    run = create(client)
    response = client.post(f"/api/runs/{run['id']}/review/", {"action": "approve"}, format="json")
    assert response.status_code == 200
    detail = client.get(f"/api/runs/{run['id']}/").json()
    assert detail["status"] == "completed" and detail["finished_at"]
    assert [r["action"] for r in detail["reviews"]] == ["approve"]
    assert Ticket.objects.get(pk=run["ticket_id"]).status == "answered"


def test_revise_then_reject(client):
    run = create(client)
    revised = client.post(f"/api/runs/{run['id']}/review/", {"action": "revise", "comment": "더 짧게"}, format="json")
    assert revised.status_code == 200
    assert client.get(f"/api/runs/{run['id']}/").json()["status"] == "awaiting_review"  # 수정 후 다시 승인 대기
    client.post(f"/api/runs/{run['id']}/review/", {"action": "reject"}, format="json")
    detail = client.get(f"/api/runs/{run['id']}/").json()
    assert detail["status"] == "rejected"
    assert [r["action"] for r in detail["reviews"]] == ["revise", "reject"]


def test_revise_requires_a_comment(client):
    run = create(client)
    response = client.post(f"/api/runs/{run['id']}/review/", {"action": "revise", "comment": " "}, format="json")
    assert response.status_code == 400 and "comment" in response.json()


def test_review_is_rejected_when_not_waiting(client):
    run = create(client)
    client.post(f"/api/runs/{run['id']}/review/", {"action": "approve"}, format="json")
    again = client.post(f"/api/runs/{run['id']}/review/", {"action": "approve"}, format="json")
    assert again.status_code == 409
    assert Review.objects.filter(run_id=run["id"]).count() == 1


def test_quality_claim_runs_rating_check(client):
    run = create(client, QUALITY)
    detail = client.get(f"/api/runs/{run['id']}/").json()
    assert detail["category"] == "quality_claim"
    check = detail["state"]["rating_check"]
    assert any("45 V" in text for text in check["abs_over"])
    assert "먼저 확인된 점" in detail["pending_review"]["draft"]["customer_reply"]


def test_short_body_is_rejected(client):
    assert client.post("/api/runs/", {"body": "짧"}, format="json").status_code == 400


def test_run_list_is_newest_first_without_heavy_fields(client):
    first, second = create(client), create(client, QUALITY)
    rows = client.get("/api/runs/").json()
    assert [r["id"] for r in rows[:2]] == [second["id"], first["id"]]
    assert "state" not in rows[0]


def test_failure_is_recorded_and_visible(client, monkeypatch):
    import fae_agent.graph as graph_module

    def boom(*args, **kwargs):
        raise RuntimeError("그래프를 만들 수 없음")

    monkeypatch.setattr(graph_module, "build_graph", boom)
    run = create(client)
    detail = client.get(f"/api/runs/{run['id']}/").json()
    assert detail["status"] == "failed" and "그래프를 만들 수 없음" in detail["error"]


def test_daily_token_budget_blocks_new_llm_runs(client, monkeypatch, settings):
    monkeypatch.setenv("FAE_MODE", "llm")
    get_settings.cache_clear()
    settings.FAE_DAILY_TOKEN_BUDGET = 1000
    Run.objects.create(body="예전 실행", thread_id="t", mode="llm", input_tokens=900, output_tokens=200)
    response = client.post("/api/runs/", LDO, format="json")
    assert response.status_code == 429 and "토큰" in response.json()["detail"]
    assert Run.objects.count() == 1  # 새 실행은 만들어지지 않는다


def test_mock_mode_ignores_token_budget(client, settings):
    settings.FAE_DAILY_TOKEN_BUDGET = 1
    Run.objects.create(body="예전 실행", thread_id="t", mode="mock", input_tokens=900, output_tokens=200)
    assert client.post("/api/runs/", LDO, format="json").status_code == 201
