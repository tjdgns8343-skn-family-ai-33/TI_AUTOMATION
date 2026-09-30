"""MCP 서버: 데이터시트 공식 계산 (서버 이름: calc).

실행: python -m fae_agent.mcp_servers.calc_server
"""

from fae_agent.mcp_servers._compat import build_server, run_stdio
from fae_agent.tools.calc import CALC_TOOLS

mcp = build_server("calc", CALC_TOOLS)

if __name__ == "__main__":
    run_stdio(mcp)
