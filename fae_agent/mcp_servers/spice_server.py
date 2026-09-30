"""MCP 서버: ngspice 회로 시뮬레이션 (서버 이름: spice).

실행: python -m fae_agent.mcp_servers.spice_server
"""

from fae_agent.mcp_servers._compat import build_server, run_stdio
from fae_agent.tools.spice import SPICE_TOOLS

mcp = build_server("spice", SPICE_TOOLS)

if __name__ == "__main__":
    run_stdio(mcp)
