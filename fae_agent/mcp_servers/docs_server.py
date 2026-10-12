"""MCP 서버: 문서 조회·검사(승인 뒤 만든 문서는 읽기 전용) (서버 이름: docs).

실행: python -m fae_agent.mcp_servers.docs_server
"""

from fae_agent.mcp_servers._compat import build_server, run_stdio
from fae_agent.tools.docs import DOCS_TOOLS

mcp = build_server("docs", DOCS_TOOLS)

if __name__ == "__main__":
    run_stdio(mcp)
