# 아키텍처

## 구성

- **에이전트 계층 (LangGraph)**: `fae_agent/graph.py`가 에이전트(노드)를 연결합니다. 상태는 `fae_agent/state.py`.
- **도구 계층 (MCP)**: 에이전트는 도구를 항상 `ToolGateway.call("서버.도구", ...)`로 부릅니다.
  `TOOL_MODE=local`이면 함수를 직접, `TOOL_MODE=mcp`면 `fae_agent/mcp_servers`의 서버를 stdio로 띄워 호출합니다.
- **데이터 계층**: 문서 조각(`data/processed/chunks.jsonl`), 부품 카드(`data/parts`), 사례 카드(`data/cases`),
  티켓 DB(`data/tickets.db`).

## 웹 앱 구성 (Django + React + Docker)

```text
브라우저 → frontend(nginx, React) ─ /api ─→ backend(Django, gunicorn) ─→ db(Postgres: 티켓·실행·그래프 진행 상태)
                                                      └─ 작업 전달 ─→ redis ─→ worker(Celery: LangGraph 실행, ngspice, OpenAI)
```

- 그래프 실행(수십 초 + 사람 승인 대기)은 요청 안에서 돌리지 않고 **Celery 작업자**가 한다. 단계가 끝날 때마다 `Run.state`를 저장해 화면이 1.5초마다 읽으며 진행을 보여 준다.
- LangGraph 진행 상태는 **Postgres 체크포인트**(`langgraph-checkpoint-postgres`)라 서버가 재시작돼도 승인 대기 중인 실행을 이어갈 수 있다.
- 승인 API(`POST /api/runs/{id}/review/`)가 멈춘 그래프를 `Command(resume=...)`로 이어 준다. 승인·수정 요청·반려는 `Review` 표에 기록된다. 수정 요청은 초안을 다시 쓰고 다시 승인 대기로 돌아간다.
- 티켓은 Django 모델(`Ticket`, `TicketEvent`)이다. `backend/api/ticket_backend.py`가 그래프의 `tickets.*` 도구를 SQLite 대신 이 모델로 바꿔 끼운다 (에이전트 코드는 그대로).
- 도구 호출은 `TOOL_MODE=local`로 고정한다 (MCP 서버는 선택 사항으로 유지).
- 하루 토큰 상한(`FAE_DAILY_TOKEN_BUDGET`)을 llm 모드에서만 적용하고, 실행마다 쓴 토큰을 `Run`에 기록한다. 접근 보호는 앞단에서 한다: nginx 비밀번호(basic auth, 환경변수로 켜짐), 문의 접수 분당 10회 제한, 기본 보안 헤더, backend 포트는 밖에 열지 않음. `/healthz`만 비밀번호 없이 열려 있다. HTTP에서는 basic auth 비밀번호가 그대로 읽히므로 인터넷에 올릴 때는 HTTPS가 필요하다.

주요 API: `GET /api/health/`, `GET /api/config/`, `GET /api/samples/`, `POST·GET /api/runs/`, `GET /api/runs/{id}/`, `POST /api/runs/{id}/review/` (문서: `/api/docs/`).

## MCP 서버와 도구

| 서버 | 도구 |
| --- | --- |
| calc | buck_output_voltage, buck_ripple_estimate, lc_resonance, thermal_estimate, ldo_thermal_estimate, startup_inrush, i2c_pullup_check, noninverting_gain, opamp_closed_loop_gain, rc_cutoff, check_value |
| kb | search_docs, search_cases, get_part_card, list_parts |
| spice | run_netlist, run_circuit, compare_circuit (회로 템플릿은 아래 참고) |
| tickets | create_ticket, update_ticket, add_event, get_ticket, list_tickets |

## 시뮬레이션 회로 템플릿 (`circuits/`)

`spice.run_circuit(circuit, params)`가 템플릿의 `$변수`를 채워 ngspice로 돌립니다. 부품 종류에 맞지 않는 회로는 검수(`CIRCUIT_PART_TYPES`)가 되돌립니다.

| 회로 | 부품 | 모델 | 주요 지표 |
| --- | --- | --- | --- |
| `lc_load_step` | 벅·LDO | 이상적 전원 → 인덕터 → 출력 커패시터(ESR) → 부하 전류 스텝. 제어 루프 없음 | `undershoot_pct`, `overshoot_pct` (시간 영역, 파형 `waveform`) |
| `opamp_ac` | 연산증폭기 | 비반전 증폭기 + 1차 연산증폭기(개방 이득 a0, 극점 GBW/a0). 주파수 응답만, 슬루율·출력 스윙·오프셋 없음 | `dc_gain`, `bw_hz`, `gain_at_freq`, `gain_ratio`, `vout_pp` (주파수 영역, `freq_response`) |

- `opamp_ac`의 지표 이름은 `calc.opamp_closed_loop_gain`과 같다. 같은 인자(`rf_ohm`, `rg_ohm`, `gbw_hz`, `freq_hz`)로 돌린 두 결과가 5 % 안이면 검수가 "교차 확인: 일치", 벗어나면 "교차 확인 불일치"를 경고로 알린다 (재분석은 요구하지 않는다).
- 실측 예 (이득 10, GBW 1 MHz, 200 kHz, 입력 0.2 Vpp): 계산 대역폭 100 kHz, 이득 4.472, 출력 0.894 Vpp / ngspice 99.99 kHz, 4.472, 0.894 Vpp (차이 0.01 % 안팎, 개방 이득 100 dB의 유한값 때문).
- 주파수 응답은 측정한 대역폭 앞뒤 3자리 범위의 160점(로그 간격)만 상태에 싣는다. 파형·주파수 응답은 화면에만 그리고 AI 프롬프트에는 넣지 않는다 (`waveform.py`의 `BULKY_KEYS`).
- ngspice는 `.meas`에 `vdb()`를 쓰면 저장 목록을 못 만들어 종료 코드 1이 되므로 템플릿에 `.save v(out)`을 직접 적는다. 실행 파일은 Windows `ngspice_con.exe`, 서버(Docker)는 `ngspice`(39)이고 두 환경에서 같은 값이 나오는 것을 확인했다.
- 새 회로를 넣을 때는 템플릿, `CIRCUIT_PART_TYPES`, `checker.describe_test_tools`의 회로 설명, reproduce·verify 테스트를 함께 추가한다.

## 상태 키

| 키 | 채우는 에이전트 | 내용 |
| --- | --- | --- |
| complaint | 입력 | 고객 문의 원문 |
| intake | 접수·분류 | 분류, 경로, 부품, 증상, 조건, 부족한 정보 |
| research | 기술 조사 | 문서 근거, 사례, 부품 카드 |
| diagnosis | 원인 분석 | 가설 3개, 해결책, 테스트 계획, 재시도 상태(meta) |
| check_report | 테스트 | 단계별 결과 (pass·fail·skipped·error) |
| qa | 검수 | 통과 여부, 지적(issue_codes), 경고 |
| rating_check | 정격 점검 (품질 클레임 경로) | 고객이 알려 준 값 대 부품 카드 한계: 절대 최대 초과, 권장 범위 밖, 한계 안, 데이터시트 출처 |
| cpr | CPR 점검 | 반품 필수 자료 체크리스트, 8D 초안 (정격 점검 결과가 D4에 반영됨) |
| response | 응답 | 고객 답변 초안, 내부 메모 |
| review | 사람 승인 | approve·revise·reject, 의견 |

## 품질 클레임 경로의 정격 점검

"타 버림", "불량" 문의도 접수 뒤 CPR 점검 전에 **정격 점검 노드**(`rating_agent`)를 거칩니다.
`fae_agent/ratings.py`의 `rating_facts`가 접수 단계에서 뽑은 조건을 부품 카드(`kb.get_part_card`)의 한계와 수치로 비교합니다 (검수의 정격 판정과 같은 로직).

- 경로는 바꾸지 않습니다. 결과(`rating_check`)만 답변 초안·8D D4·내부 메모에 붙고, 불량 여부는 사람이 판단합니다.
- 절대 최대를 넘으면 답변 첫 문단에 "알려 주신 값 기준"으로 값과 한계(권장 최대 포함)와 데이터시트 출처를 밝히고 사용 조건 문제 가능성을 알립니다. 반품 절차와 자료 요청은 그대로 안내합니다.
- 부품 카드가 없거나 부품 번호가 없으면 "정격 점검 못 함"만 표시하고 아무 주장도 하지 않습니다. 첫 번째 부품 번호만 봅니다.
- 접수 단계의 조건 추출이 틀리면 잘못된 비교가 됩니다 (규칙 기반 접수는 "입력 4.5V에서 60V까지" 같은 고객의 오해 문장에서 4.5 V를 입력 전압으로 읽음).
- 권장 범위 초과(절대 최대 이하)는 내부 메모와 화면에만 표시하고 고객 답변에는 넣지 않습니다.

## 검수 → 재분석 규칙

| 검수 지적 | 원인 분석이 하는 일 |
| --- | --- |
| reproduce_fail | 1순위 가설이 증상을 재현하지 못함 → 다음 가설을 1순위로 |
| verify_fail | 해결책이 목표 미달 → 변경 폭을 키움 |
| rating_not_addressed | 정격 초과를 놓침 → 정격 초과 가설을 맨 앞에 추가 |
| no_evidence, test_error | LLM 모드에서 지적 내용을 프롬프트로 전달해 다시 작성 |
| self_referential_test | **재현(reproduce)** 테스트의 값 대조(`check_value`) 합격 범위 경계가 검사한 값과 같아 항상 통과함 → 실제 조건의 값과 데이터시트 한계로 다시 작성 (LLM 모드). 해결 확인(verify)은 설계값이 한계와 같은 경우가 흔하고 재분석해도 고쳐지지 않아 지적하지 않고 경고만 한다 |
| test_mismatch | 부품 종류에 맞지 않는 계산·회로(예: 연산증폭기에 LC 필터 시뮬레이션) → 맞는 도구를 쓰거나 테스트 없이 근거 문서로 설명 (LLM 모드) |
| rating_false_claim | 고객 값이 한계를 넘지 않는데 '정격 초과' 가설을 씀 → 그 가설을 빼거나 실제 원인으로 제목을 바꿈 (LLM 모드) |
| trivial_test | 재현·확인 테스트의 기대 범위가 어떤 값이나 통과함 (`gain_ratio`·`duty`를 0~1 전체로 두거나 min·max를 모두 비움) → 합격과 불합격이 갈리는 범위로 다시 정함 (LLM 모드). 한 단계 안에서 일부 기대 범위만 그러면 경고만 한다 |
| tests_dropped | 바로 앞 시도가 `test_error`(또는 `tests_dropped`)로 되돌려졌는데 재분석이 오류를 고치지 않고 테스트(참고 계산 제외)를 모두 뺌 → 인자·지표 이름을 고쳐 테스트를 유지 (LLM 모드). 그 부품 종류에 쓸 도구가 `TOOL_PART_TYPES`에 있을 때만 지적하고, 센서처럼 도구가 없거나 처음부터 테스트가 없는 시도는 경고만 한다 |

- 정격 판정은 문자열이 아니라 **부품 카드의 한계와 고객 조건을 수치로 비교**한다 (입력·전원 전압, 부하 전류, 출력 커패시터, ESR. 연산증폭기 카드는 `vs_*` 키).
  부품 카드에 한계가 없는 조건은 판단하지 않는다. 절대 최대를 넘는데 가설에 '정격'이 없으면 `rating_not_addressed`, 넘지 않는데 '정격 초과'를 쓰면 `rating_false_claim`이다.
- 값 대조(`check_value`)가 실패하면 지적문에 합격 범위 방향 힌트가 붙는다. 합격 범위는 '증상이 나타나는 쪽'이라 값이 한계보다 커서 문제면 `min=한계`, 작아서 문제면 `max=한계`이고, 확인(verify)은 반대(정상 쪽)다. 새 부품 측정에서 이 방향을 틀린 채 재분석 3번을 다 쓴 사례가 3건 있었고, 방향이 맞는데도 실패하면 가설이 틀린 것이라는 안내도 함께 붙여 통과시키려고 방향만 뒤집는 일을 막는다.
- 재현·참고 계산의 인자가 고객이 알려 준 조건(`vin_v`, `vout_v`, `iout_a`, `fsw_hz` 등, 오차 1 %)과 다르면 경고한다 (`ARG_TO_CONDITION`). 조건을 바꾸는 확인(verify)과 값 대조는 제외. 재분석을 요구하지는 않고, 답변에 쓰지 말라는 문구만 남긴다.
- 도구와 부품 종류의 짝은 `qa.py`의 `TOOL_PART_TYPES`, `CIRCUIT_PART_TYPES` 표에 있다. 표에 없는 도구·부품 종류는 검사하지 않는다.
  `tests_dropped`가 "쓸 수 있는 도구"를 고르는 데도 같은 표를 쓴다 (새 도구를 넣으면 표에 부품 종류를 함께 적는다).
- 연산증폭기 이득-대역폭 계산(`calc.opamp_closed_loop_gain`)은 폐루프 대역폭(GBW÷이득), 주파수별 이득(`gain_ratio`), 출력 진폭, 필요한 슬루율(`slew_ratio` > 1이면 슬루율 한계)을 돌려준다. 1차 모델이라 대역폭 근처의 위상·피킹은 다루지 않는다.
- 검수 경고에 테스트 구성(공식 계산·시뮬레이션·값 대조 개수)이 표시된다. 값 대조만 있으면 검증이라고 보기 어렵다는 문구가 붙는다.

재분석은 최대 `MAX_QA_RETRIES`회(기본 2회)이고, 넘으면 검수 미통과 사실을 내부 메모에 남긴 채 응답 단계로 갑니다.

## 테스트의 두 목적

- **reproduce**: 고객이 말한 조건으로 계산·시뮬레이션했을 때 증상이 나오는가 (가설이 맞는지)
- **verify**: 해결책을 적용한 값으로 목표를 만족하는가 (해결책이 맞는지)

예) LDO 과열: 입력 7 V → 접합 온도 계산이 한계를 넘음(reproduce 통과) → 입력 3.8 V로 낮추면 한계 이하(verify 통과).

## 확장 방향

- 검색: `TfidfIndex`를 임베딩 + 벡터 DB로 교체
- 테스트: `circuits/`에 회로 템플릿 추가, `calc.py`에 공식 추가
- 사람 승인: Streamlit 대신 Slack 승인 버튼 연동
