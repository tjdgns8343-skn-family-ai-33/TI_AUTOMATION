"""MCP 서버: 문의 건(티켓) 저장소 (서버 이름: tickets).

실행: python -m fae_agent.mcp_servers.tickets_server
"""

from fae_agent.mcp_servers._compat import build_server, run_stdio
from fae_agent.tools.tickets import TICKET_TOOLS

mcp = build_server("tickets", TICKET_TOOLS)

if __name__ == "__main__":
    run_stdio(mcp)
