"""MCP 서버: 문서·사례·부품 카드 검색 (서버 이름: kb).

실행: python -m fae_agent.mcp_servers.knowledge_server
"""

from fae_agent.mcp_servers._compat import build_server, run_stdio
from fae_agent.tools.knowledge import KNOWLEDGE_TOOLS

mcp = build_server("kb", KNOWLEDGE_TOOLS)

if __name__ == "__main__":
    run_stdio(mcp)
