import pytest


@pytest.fixture(autouse=True)
def fae_env(monkeypatch):
    """테스트는 항상 mock·local 모드(API 키 없음, 요금 없음)로 돌린다 (.env보다 환경변수가 우선)."""
    for key, value in {
        "FAE_MODE": "mock",
        "TOOL_MODE": "local",
        "AUTO_APPROVE": "false",
        "MAX_QA_RETRIES": "2",
    }.items():
        monkeypatch.setenv(key, value)
    from fae_agent.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
