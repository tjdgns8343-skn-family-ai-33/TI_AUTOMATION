"""MCP Python SDK v2(MCPServer)와 v1(FastMCP)을 모두 지원하는 서버 생성기.

pip install mcp 는 2026년 기준 v2를 설치합니다 (v1의 FastMCP가 v2에서 MCPServer로 바뀜).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

try:  # v2 (2026-07-28 스펙)
    from mcp.server import MCPServer
except ImportError:  # v1.x
    from mcp.server.fastmcp import FastMCP as MCPServer


def build_server(name: str, tools: dict[str, Callable[..., Any]]) -> Any:
    """함수 목록을 MCP 도구로 등록한 서버를 만듭니다. 도구 이름 = 함수 이름, 설명 = docstring."""
    server = MCPServer(name)
    for function in tools.values():
        server.tool()(function)
    return server


def run_stdio(server: Any) -> None:
    """stdio로 실행합니다. (stdout은 MCP 통신에 쓰이므로 print를 쓰지 마세요)"""
    server.run()
