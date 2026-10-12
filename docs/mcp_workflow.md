# 영업 워크플로를 MCP로 쓰기 (Claude Desktop 등)

문의 접수 → 분류·분석 → 답변 초안 → **사람 승인** → (승인되면) 고객 메일 초안·내부 보고서·티켓 요약, 이 흐름을 MCP 클라이언트가 단계별로 부를 수 있다.
서버 세 개를 따로 연결한다.

| 서버 (실행 모듈) | 도구 | 하는 일 |
| --- | --- | --- |
| `fae_workflow` (`fae_agent.mcp_servers.workflow_server`) | `submit_inquiry`, `get_draft`, `list_pending`, `review` | 접수부터 승인·문서화까지. **상태를 가진 서버**(대기 건은 서버 메모리) |
| `sales` (`...sales_server`) | `classify_inquiry`, `check_request`, `recommend_parts` | 분류, 견적·샘플 요청 점검, 부품 추천. 상태 없음, 규칙 기반이라 토큰 0 |
| `docs` (`...docs_server`) | `list_documents`, `read_document`, `check_document_text` | 승인 뒤 만든 문서 조회(해시 검증), 임의 문서 대조 검사. 읽기 전용 |

## 승인 게이트

- `submit_inquiry`는 승인 단계에서 멈춘다. 이 시점에는 문서가 없다.
- 문서는 `review(decision="approve")` 뒤에 그래프의 문서화 노드만 만든다. **문서를 만드는 도구는 없다** (우회 경로 없음).
- `revise`는 의견대로 답변을 다시 쓰고 다시 승인 대기로 돌아간다. `reject`는 문서 없이 끝난다. 이미 결정한 건은 다시 결정할 수 없다. 모르는 결정값은 승인으로 처리하지 않고 거부한다.
- 승인 기록에 검토자 이름(`reviewer`)과 의견이 남고, `list_documents`가 승인 뒤 문서가 바뀌지 않았는지 해시로 확인한다.
- 서버는 `AUTO_APPROVE=true`(사람 승인 건너뛰기)면 접수를 거부한다. `FAE_MODE=llm`이면 요금이 나가므로 서버 환경에 `FAE_ALLOW_LLM=1`이 없으면 거부한다.

**한계:** MCP 서버는 호출하는 쪽이 사람인지 모델인지 구별하지 못한다. 클라이언트가 도구 호출마다 사람에게 확인을 받도록 설정해야 승인 게이트가 의미가 있다 (Claude Desktop은 기본으로 도구 호출을 허용할지 묻는다. "항상 허용"으로 바꾸지 않는다).
서버를 끄면 승인 대기 건은 사라진다. 티켓 기록(`data/tickets.db`)과 이미 만든 문서(`data/outbox/`)는 남는다.

## Claude Desktop 연결 예

`claude_desktop_config.json`의 `mcpServers`에 추가한다. 경로는 본인 PC 값으로 바꾼다.

```json
{
  "mcpServers": {
    "fae_workflow": {
      "command": "<ti_project 환경의 python.exe 전체 경로>",
      "args": ["-m", "fae_agent.mcp_servers.workflow_server"],
      "env": { "PYTHONPATH": "<이 저장소 경로>", "FAE_MODE": "mock", "PYTHONUTF8": "1" }
    },
    "sales": {
      "command": "<ti_project 환경의 python.exe 전체 경로>",
      "args": ["-m", "fae_agent.mcp_servers.sales_server"],
      "env": { "PYTHONPATH": "<이 저장소 경로>", "FAE_MODE": "mock", "PYTHONUTF8": "1" }
    },
    "docs": {
      "command": "<ti_project 환경의 python.exe 전체 경로>",
      "args": ["-m", "fae_agent.mcp_servers.docs_server"],
      "env": { "PYTHONPATH": "<이 저장소 경로>", "PYTHONUTF8": "1" }
    }
  }
}
```

- `FAE_MODE`를 `mock`으로 적어 두면 `.env`의 llm 설정이 있어도 요금이 나가지 않는다 (환경 변수가 `.env`보다 우선).
- ngspice를 쓰는 기술 문의까지 돌리려면 `env`에 `NGSPICE_PATH`도 적는다. 영업 문의(견적·샘플, 설계 상담)는 ngspice가 필요 없다.
- 서버 코드는 `print`를 쓰지 않는다 (stdout이 MCP 통신에 쓰임).

## 사용 순서 예

1. `submit_inquiry(customer, subject, body)` → `thread_id`, 분류, 답변 초안, 빠진 정보 또는 추천 결과.
2. 사람이 초안을 읽는다. (`get_draft`, `list_pending`으로 다시 볼 수 있다.)
3. `review(thread_id, "approve", reviewer="이름", comment="...")` → 문서 생성 결과.
4. `list_documents(ticket_id)`, `read_document(ticket_id, "customer_email.md")`. 메일은 **초안**이며 발송은 하지 않는다.

## 확인한 것

- 함수 단위: `tests/test_workflow_tools.py` (게이트, 안전장치, 문서 해시, 경로 이동 차단).
- MCP 프로토콜: `tests/test_mcp_workflow_smoke.py`가 서버를 실제로 띄워(stdio) 접수 → 승인 → 문서 조회를 한 세션에서 호출하고, `TOOL_MODE=mcp`로 `sales`·`docs` 도구도 부른다.
- 아직 안 한 것: Claude Desktop 화면에서 직접 연결해 보는 것(사용자 PC 확인 필요).
