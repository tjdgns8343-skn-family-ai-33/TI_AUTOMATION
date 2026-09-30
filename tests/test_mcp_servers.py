"""MCP 서버 모듈이 도구를 등록하며 정상적으로 만들어지는지 확인합니다 (mcp 패키지 필요)."""

import importlib

import pytest

pytest.importorskip("mcp")


@pytest.mark.parametrize("module", ["calc_server", "knowledge_server", "spice_server", "tickets_server"])
def test_server_builds(module):
    server_module = importlib.import_module(f"fae_agent.mcp_servers.{module}")
    assert server_module.mcp is not None
