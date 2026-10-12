"""MCP 서버: 문의 접수 → 사람 승인 → 문서화 워크플로(상태를 가진 서버) (서버 이름: fae_workflow).

실행: python -m fae_agent.mcp_servers.workflow_server
"""

from fae_agent.mcp_servers._compat import build_server, run_stdio
from fae_agent.tools.workflow import WORKFLOW_TOOLS

mcp = build_server("fae_workflow", WORKFLOW_TOOLS)

if __name__ == "__main__":
    run_stdio(mcp)
