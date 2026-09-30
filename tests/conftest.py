import shutil

import pytest

from fae_agent.config import PROJECT_ROOT, get_settings
from fae_agent.tools import knowledge


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """테스트마다 임시 data 폴더를 쓰고, mock·local 모드로 고정합니다 (.env 설정보다 우선)."""
    data = tmp_path / "data"
    for sub in ("samples", "parts", "cases", "eval"):
        shutil.copytree(PROJECT_ROOT / "data" / sub, data / sub)
    for key, value in {
        "DATA_DIR": str(data),
        "FAE_MODE": "mock",
        "TOOL_MODE": "local",
        "AUTO_APPROVE": "false",
        "MAX_QA_RETRIES": "2",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    knowledge.clear_cache()
    yield data
    get_settings.cache_clear()
    knowledge.clear_cache()
