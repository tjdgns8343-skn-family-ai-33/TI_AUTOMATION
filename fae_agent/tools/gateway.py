"""도구 호출 창구 (ToolGateway).

에이전트는 도구를 항상 `gateway.call("서버.도구", **인자)`로 부릅니다.
- TOOL_MODE=local : 같은 프로세스에서 함수를 바로 호출 (기본값, 빠르고 디버깅이 쉬움)
- TOOL_MODE=mcp   : 해당 MCP 서버를 stdio로 띄워 MCP 프로토콜로 호출
                    (langchain[mcp]의 MCPAdapter 사용, 2026년 9월 기준 베타 API)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Callable
from typing import Any

from ..config import PROJECT_ROOT, get_settings
from .calc import CALC_TOOLS
from .docs import DOCS_TOOLS
from .knowledge import KNOWLEDGE_TOOLS
from .sales import SALES_TOOLS
from .spice import SPICE_TOOLS
from .tickets import TICKET_TOOLS

# 서버 이름 → (로컬 함수 목록, MCP 서버 모듈)
SERVERS: dict[str, tuple[dict[str, Callable[..., dict[str, Any]]], str]] = {
    "calc": (CALC_TOOLS, "fae_agent.mcp_servers.calc_server"),
    "kb": (KNOWLEDGE_TOOLS, "fae_agent.mcp_servers.knowledge_server"),
    "spice": (SPICE_TOOLS, "fae_agent.mcp_servers.spice_server"),
    "tickets": (TICKET_TOOLS, "fae_agent.mcp_servers.tickets_server"),
    "sales": (SALES_TOOLS, "fae_agent.mcp_servers.sales_server"),
    "docs": (DOCS_TOOLS, "fae_agent.mcp_servers.docs_server"),
    # fae_workflow(문의 접수 → 승인 → 문서화)는 상태를 가진 서버라 호출마다 서버를 띄우는 게이트웨이에는 넣지 않는다 (tools/workflow.py)
}


def available_tools() -> list[str]:
    return [f"{server}.{name}" for server, (tools, _) in SERVERS.items() for name in tools]


class ToolGateway:
    def __init__(self, mode: str | None = None):
        self.mode = mode or get_settings().tool_mode

    def call(self, name: str, **kwargs: Any) -> dict[str, Any]:
        server, _, tool = name.partition(".")
        if server not in SERVERS or tool not in SERVERS[server][0]:
            raise KeyError(f"알 수 없는 도구: {name} (사용 가능: {', '.join(available_tools())})")
        if self.mode == "mcp":
            return asyncio.run(_call_via_mcp(server, tool, kwargs))
        return SERVERS[server][0][tool](**kwargs)


async def _call_via_mcp(server: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """해당 서버 하나만 stdio로 띄워 도구를 호출합니다 (에이전트별 권한 분리에도 유리)."""
    try:
        from langchain.mcp import MCPAdapter
    except ImportError as exc:
        raise RuntimeError('MCP 모드에는 langchain[mcp]가 필요합니다: pip install "langchain[mcp]"') from exc

    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    config = {"mcpServers": {server: {"command": sys.executable, "args": ["-m", SERVERS[server][1]], "env": env}}}
    async with MCPAdapter(config) as adapter:
        tools = await adapter.list_tools()
        matched = [t for t in tools if t.name == tool or t.name.endswith(("_" + tool, "." + tool, "/" + tool))]
        if not matched:
            raise KeyError(f"MCP 서버 '{server}'에 '{tool}' 도구가 없습니다: {[t.name for t in tools]}")
        raw = await matched[0].ainvoke(args)
    return _to_dict(raw)


def _to_dict(raw: Any) -> dict[str, Any]:
    """MCP 도구 결과(문자열·content 블록·메시지 등)를 dict로 바꿉니다."""
    if isinstance(raw, dict):
        if raw.get("type") == "text" and "text" in raw:
            return _to_dict(raw["text"])
        return raw
    if isinstance(raw, tuple) and raw:  # (content, artifact) 형식
        return _to_dict(raw[0])
    if hasattr(raw, "content"):  # ToolMessage 등
        return _to_dict(raw.content)
    if isinstance(raw, list):
        texts = [b.get("text", "") if isinstance(b, dict) else getattr(b, "text", str(b)) for b in raw]
        return _to_dict("".join(texts))
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {"text": raw}
        return value if isinstance(value, dict) else {"value": value}
    return {"value": raw}
