"""MCP 서버: 영업 도구(분류·견적 요청 점검·부품 추천) (서버 이름: sales).

실행: python -m fae_agent.mcp_servers.sales_server
"""

from fae_agent.mcp_servers._compat import build_server, run_stdio
from fae_agent.tools.sales import SALES_TOOLS

mcp = build_server("sales", SALES_TOOLS)

if __name__ == "__main__":
    run_stdio(mcp)
