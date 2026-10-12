"""MCP 프로토콜로 끝까지 확인하는 스모크 테스트: 서버를 실제로 띄워(stdio) 접수 → 승인 → 문서 조회를 한 세션에서 호출한다.

도구 함수 단위 테스트(test_workflow_tools.py)와 달리 서버 등록, JSON 직렬화, 프로세스 사이 상태 유지를 함께 본다.
"""

import asyncio
import os
import sys
import warnings

import pytest

pytest.importorskip("mcp")
pytest.importorskip("langgraph")
warnings.filterwarnings("ignore")
mcp_module = pytest.importorskip("langchain.mcp")

from fae_agent.config import PROJECT_ROOT
from fae_agent.tools.gateway import _to_dict

BODY = "TPS62130A 샘플 10개가 필요합니다. 용도는 웨어러블 전원이고 시제품 단계이며 6주 안에 받고 싶습니다."


def _config() -> dict:
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}  # DATA_DIR 등 테스트 환경(임시 data 폴더)을 서버가 그대로 물려받는다
    server = lambda module: {"command": sys.executable, "args": ["-m", f"fae_agent.mcp_servers.{module}"], "env": env}
    return {"mcpServers": {"fae_workflow": server("workflow_server"), "docs": server("docs_server")}}


async def _flow() -> dict:
    async with mcp_module.MCPAdapter(_config()) as adapter:
        tools = {t.name: t for t in await adapter.list_tools()}

        async def call(tool_name: str, /, **args) -> dict:  # 위치 전용: 도구 인자 이름(name 등)과 겹치지 않게
            tool = next(v for k, v in tools.items() if k == tool_name or k.endswith(("_" + tool_name, "." + tool_name, "/" + tool_name)))
            return _to_dict(await tool.ainvoke(args))

        out = {"tool_names": sorted(tools)}
        out["submit"] = await call("submit_inquiry", customer="테스트", subject="샘플 요청", body=BODY)
        thread = out["submit"]["thread_id"]
        out["docs_before"] = await call("list_documents")
        out["pending"] = await call("list_pending")
        out["bad"] = await call("review", thread_id=thread, decision="maybe", reviewer="kim")
        out["approved"] = await call("review", thread_id=thread, decision="approve", reviewer="kim", comment="확인함")
        out["again"] = await call("review", thread_id=thread, decision="approve", reviewer="kim")
        out["listing"] = await call("list_documents", ticket_id=out["approved"]["ticket_id"])
        out["report"] = await call("read_document", ticket_id=out["approved"]["ticket_id"], name="internal_report.md")
        return out


def test_submit_review_and_read_documents_over_the_mcp_protocol():
    out = asyncio.run(_flow())
    expected = ("submit_inquiry", "get_draft", "review", "list_pending", "list_documents", "read_document", "check_document_text")
    assert all(any(name.endswith(want) for name in out["tool_names"]) for want in expected), out["tool_names"]
    assert out["submit"]["status"] == "awaiting_review" and "documents" not in out["submit"]
    assert out["docs_before"] == {"tickets": []}  # 승인 전에는 문서가 없다
    assert len(out["pending"]["pending"]) == 1
    assert "error" in out["bad"] and "error" in out["again"]
    assert out["approved"]["status"] == "answered" and out["approved"]["documents"]["status"] == "generated"
    assert out["listing"]["verified"] is True and out["listing"]["approval"]["action"] == "approve"
    assert "[kim]" in out["listing"]["approval"]["comment"]
    assert out["report"]["found"] and "영업 요청 점검" in out["report"]["content"]


def test_sales_and_docs_tools_work_through_the_gateway_in_mcp_mode():
    from fae_agent.tools.gateway import ToolGateway

    gateway = ToolGateway("mcp")
    assert gateway.call("sales.classify_inquiry", subject="샘플 요청", body=BODY)["category"] == "quote_sample"
    checked = gateway.call("sales.check_request", subject="샘플 요청", body=BODY)
    assert checked["applicable"] and checked["missing"] == [] and checked["parts"][0]["part"] == "TPS62130A"
    assert gateway.call("docs.check_document_text", text="가격 $4", source="")["ok"] is False
