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
| `buck_switch` | 벅 | 비동기 벅의 스위칭 동작: 스위치(Ron) → 캐치 다이오드 → 인덕터(DCR) → 출력 커패시터(ESR) → 저항 부하. 개루프(제어 루프 없음) | `ripple_v`, `ripple_pct`, `delta_il_a`, `il_min_a`, `il_max_a`, `ripple_ratio`, `duty`, `on_time_s`, `vout_avg_v`, `dcm` (시간 영역, 정상 상태 10주기 `waveform`) |

- `opamp_ac`의 지표 이름은 `calc.opamp_closed_loop_gain`과 같다. 같은 인자(`rf_ohm`, `rg_ohm`, `gbw_hz`, `freq_hz`)로 돌린 두 결과가 5 % 안이면 검수가 "교차 확인: 일치", 벗어나면 "교차 확인 불일치"를 경고로 알린다 (재분석은 요구하지 않는다).
- 실측 예 (이득 10, GBW 1 MHz, 200 kHz, 입력 0.2 Vpp): 계산 대역폭 100 kHz, 이득 4.472, 출력 0.894 Vpp / ngspice 99.99 kHz, 4.472, 0.894 Vpp (차이 0.01 % 안팎, 개방 이득 100 dB의 유한값 때문).
- `buck_switch`는 듀티를 두 단계로 정한다. 1단계에서 출력 전압을 목표값 전원에 묶고(커패시터가 무한대인 셈) 인덕터 평균 전류가 부하 전류와 같아지는 듀티를 찾는다 (ngspice를 2~5회, 처음 추정은 손실을 넣은 연속 모드 식이나 불연속 모드 식). 2단계에서 묶음을 풀고 커패시터와 부하를 넣어 한 번 더 돌려 출력 리플을 잰다. 출력 전압은 부하 저항×커패시터의 느린 시간 상수(예: 50 Ω × 220 µF = 11 ms)로 움직여서, 한 번에 듀티를 맞추면 목표에서 벗어난 가짜 정상 상태로 수렴했기 때문이다. 한 번에 0.2~4초 걸린다.
- `buck_switch`의 지표 이름은 `calc.buck_ripple_estimate`와 같다 (`delta_il_a`, `ripple_v`, `ripple_ratio`, `duty`, `on_time_s`). 계산은 연속 전도 모드(CCM)만 맞다. 부하가 인덕터 전류 리플의 절반(`iout_crit_a`)보다 작으면 인덕터 전류가 0까지 내려가는 불연속 모드(DCM)이고, 계산과 시뮬레이션 모두 `dcm=1`을 돌려준다. 고객이 알려 주지 않은 값(스위치 저항 0.1 Ω, 인덕터 직류 저항 0.02 Ω, 다이오드 순방향 전압 0.4 V)은 일반값을 가정하고 결과의 `assumed`와 검수 경고에 적는다.
- 실측 (계산 대 시뮬레이션 대 해석식, 인덕터 전류 리플 ΔIL과 출력 리플):

  | 조건 | 계산(CCM 식) | 시뮬레이션 | 비고 |
  | --- | --- | --- | --- |
  | LM2596 12→5 V, 1 A, 33 µH, 100 µF·ESR 0.3 Ω (L-02) | 0.589 A, 182 mV | 0.613 A, 174 mV | 평가 정답 177 mV, 손실 넣은 해석식 0.610 A |
  | 같은 조건에서 470 µF·ESR 0.02 Ω (H-L1) | 0.589 A, 12.8 mV | 0.613 A, 12.2 mV | 평가 정답 12.8 mV |
  | LM2596 12→5 V, 0.1 A, 33 µH (경부하) | 0.589 A (맞지 않음, 임계 0.295 A) | 0.350 A, 불연속 | 불연속 해석식 0.351 A |
  | LM2596 12→5 V, 2 A, 4.7 µH (L-03) | 4.137 A (임계 2.069 A라 계산도 불연속) | 4.13 A, 불연속 | 부하 2 A가 임계 부하 바로 아래인 경계 조건 |
  | TPS54331 28→1.2 V, 1 A, 10 µH | 0.202 A | 0.268 A | 다이오드 강하 0.4 V가 출력 1.2 V에 비해 커서 시뮬레이션이 계산보다 33 % 큼 (온타임 75 ns) |

- 검수의 교차 확인(`qa._cross_check_buck`)은 같은 조건(`vin_v`, `vout_v`, `l_h`, `fsw_hz`, `cout_f`, `esr_ohm`)의 계산과 시뮬레이션이 전류 리플 15 %, 출력 리플 30 % 안이면 "교차 확인: 일치"를 알린다. 출력 리플의 허용 오차가 큰 것은 계산이 용량 성분과 ESR 성분을 단순히 더한 보수적 값이기 때문이다 (실제 파형에서는 두 성분의 위상이 달라 22 µF 세라믹에서 시뮬레이션이 20 % 작았다). 시뮬레이션이 불연속이면 숫자가 비슷해도 "교차 확인 불일치: 불연속 전도"를 알린다. 계산만 있고 불연속이면 "불연속 전도 영역" 경고를, 수렴하지 못한 시뮬레이션과 가정한 값도 경고로 남긴다. 모두 재분석을 요구하지 않는다.
- 모델에 없는 것: 제어 루프(전류 모드 보상), TPS54331의 Eco-mode 펄스 스키핑, 최소 온타임 제한, 기판의 기생 성분, 인덕터 포화. 최소 온타임은 계산의 `on_time_s`를 부품 카드의 `min_on_time_s`와 비교해서 다룬다. 이 모델은 TI 공식 SPICE 모델이 아니고, 평가 보드 측정값과는 비교하지 못했다.
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

## 설계 점검 (`fae_agent/design_check.py`)

LLM이 규칙 목록에서 놓치기 쉬운 수치 비교를 **코드가 직접** 한다 (정격 비교 `ratings.py`와 같은 방식). 조사 노드가 부품 카드를 찾은 직후 계산해
상태의 `design_check`에 담고, 원인 분석 프롬프트("# 설계 점검")와 답변 프롬프트, 검수, 사람 검토자용 내부 메모가 같은 값을 쓴다. 부품 카드의 `type`마다
점검이 다르고(`CHECKS` 표), 필요한 값이나 카드의 값이 없으면 점검하지 않는다 (추측하지 않는다). 계산 도구는 `ToolGateway.call`로 부른다.

| 부품 | 점검 | 상태 | 하는 일 |
| --- | --- | --- | --- |
| 벅 | `current_limit` | `exceeds_min` / `ok` | 인덕터 최대 전류(연속: 부하 + ΔIL/2, 불연속: 피크)를 카드의 `current_limit_a_min`과 비교하고, 최소 제한 부품이 걸리기 시작하는 부하를 역산한다. 평가 문의 D-1에서 이 비교가 없어 놓쳤다 |
| 벅 | `measured_ripple` | `consistent` / `unexplained` / `needs_esr` / `skipped` | 고객이 잰 리플을 같은 조건의 계산과 비교한다. 3배 이상 다르면 `unexplained`(계산으로 설명되지 않음), ESR을 모르면 잰 값을 설명하는 데 필요한 ESR을 역산해(`needs_esr`) 알려 준다. 불연속 전도면 비교하지 않는다 |
| 벅 | `soft_start` | `too_slow` / `too_fast` / `ok` | SS 커패시터로 소프트 스타트 시간 TSS = CSS × (기준 전압) ÷ (충전 전류)를 계산해 카드의 권장 시간 범위·커패시터 상한과 비교하고, 약 5 ms가 되는 커패시터를 알려 준다. 권장 범위가 없는 부품(TPS62130A)은 시간만 알려 준다 (M-3) |
| 벅 | `min_on_time` | `ok` / `uncertain` / `marginal` / `below_typ` | 온타임을 이상적 듀티와 다이오드 강하 0.4 V(가정)를 넣은 듀티 두 가지로 계산해 카드의 최소 제어 가능 온타임(전형·최대)과 비교한다. 두 값이 최대값을 사이에 두고 갈리면 `uncertain`으로 확정하지 않는다 (M-3). 동기 벅(카드에 `synchronous: true` 또는 `rdson_low_ohm`, 예: LM5146)은 다이오드 강하를 가정하지 않고 듀티 ÷ 주파수 하나로 비교하며 `uncertain`이 없다. 측정 조건 문구는 카드의 `min_on_time_note`를 쓴다 |
| LDO | `ldo_thermal` | `shutdown` / `over_recommended` / `ok` | 소모전력과 접합 온도를 열 차단·권장 최대와 비교하고, 접합 온도 한계 안이 되는 최대 부하·입력 전압을 역산한다. 고객이 잰 표면 온도가 있으면 접합 온도가 아니라는 점을 알린다 (M-1) |
| 벅 컨트롤러 | `ilim_valley` | `too_low` / `marginal` / `ok` / `info` | ILIM 저항이 정하는 골짜기 전류 한계(IRDSON × RILIM ÷ RDS(on))와 평균 한계 부하(+ΔIL/2)를 부하와 비교하고, 한계가 전부하 골짜기와 같아지는 RILIM(식 6, IRDSON 편차 범위 포함)과 히컵 주기를 알린다. 골짜기 방식이라 인덕터를 키우면 평균 한계 부하가 오히려 낮아진다는 문구가 붙는다. 카드에 `ilim_current_rdson_a`가 있고 고객이 하단 FET RDS(on)을 알려 줘야 한다 (Q-1) |
| 벅 | `max_duty` | `cannot_regulate` / `marginal` / `ok` | 최소 오프타임(전형·최대)으로 최대 듀티 = 1 − tOFF × fsw를 구하고 최저 입력에서 필요한 듀티(Vout/Vin)와 비교한다. 못 내면 유지 가능한 입력 하한과 최악 듀티가 필요 듀티보다 3 %p 높아지는 주파수를 알린다. tOFF가 없는 카드는 `max_duty`(최소 보장값)만으로 `marginal`까지 판정한다 (Q-2) |
| 전 부품 | `rating` | `abs_over` / `rec_over` | 고객 조건이 카드의 절대 최대·권장 범위를 넘으면 사실로 올린다 (`ratings.py`와 같은 로직, 이제 VCC와 최저 입력 전압 포함). 기술 문의 경로에서도 분석·답변 프롬프트에 들어가 모델이 한계보다 큰지 작은지를 틀리게 쓰지 않게 한다. 검수는 이미 `rating_not_addressed`로 다루므로 `design_not_addressed`에는 넣지 않는다 (Q-1) |
| 충전기 | `input_current_limit` | `pin_limits` / `register_limits` / `ok` / `info` | ILIM 핀 저항의 IINMAX = KILIM ÷ RILIM(KILIM 최소·전형·최대)과 레지스터(IINLIM) 중 낮은 값을 비교하고 필요한 RILIM을 알린다 (Q-3) |
| 충전기 | `charge_power` | `power_limited` / `ok` | 입력 전압 × 입력 전류 한계 × 효율 90 %(가정)에서 시스템 부하를 빼 배터리 전압에서 낼 수 있는 충전 전류를 구하고 설정값과 비교한다 (Q-3) |
| 충전기 | `ts_window` | `hot_low` / `cold_high` / `both` / `ok` | TS 분압(RT1, RT2)과 NTC로 충전 시작·중 차단 문턱 저항과 온도를 계산한다. 온도는 카드의 103AT 두 점(0 °C 27.28 kΩ, 45 °C 4.91 kΩ)으로 맞춘 β 곡선의 **추정**이다 (Q-4) |
| 연산증폭기 | `offset` | `info` | 비반전 이득 1 + Rf/Rg에서 입력 오프셋 전압의 출력 전형·최대값을 알린다. 입력 0 V의 출력이 최대값 이하면 불량이 아니다 (M-2) |

- 접수가 읽는 조건: 잰 리플 `ripple_meas_v`(V, "리플 규격 10mV 이하" 같은 목표값은 건너뜀), SS 커패시터 `css_f`, 피드백 저항 `rf_ohm`·접지 쪽 저항 `rg_ohm`, 고객이 잰 표면 온도 `tcase_meas_c`.
  "입력을 0V로 접지", "출력이 0V로 떨어짐" 같은 증상 표현의 0은 조건으로 읽지 않는다 (`POSITIVE_ONLY`). "전원 6V"는 `vdd_v`로 읽는다.
  2026-10-07에 추가한 조건: 최저 입력 `vin_min_v`, VCC `vcc_v`, ILIM 저항 `rilim_ohm`, 하단 FET `rds_low_ohm`, 충전기 `iinlim_a`·`ichg_set_a`·`vbat_v`·`isys_a`,
  TS 분압 `rt1_ohm`·`rt2_ohm`(없으면 비움)·`ntc_r25_ohm`. `ILIM 100kHz`의 kHz를 kΩ으로 읽지 않는다. LLM 접수 프롬프트에도 같은 설명이 있다.
- (2026-10-07) 새 `COVERAGE` 항목: `ilim_valley`/`too_low`, `max_duty`/`cannot_regulate`·`marginal`, `input_current_limit`/`pin_limits`, `charge_power`/`power_limited`, `ts_window`/`hot_low`·`cold_high`·`both`.
  '히컵'·'전류 제한'처럼 증상을 가리키는 말은 다른 원인(소프트 스타트 돌입)의 가설도 걸려서 가설에 있어야 할 낱말에서 뺐고, ILIM·골짜기·'전류 제한 설정'처럼 설정 값을 가리키는 말만 쓴다.
  mock 분석은 VCC 같은 입력·전원 전압 밖의 절대 최대 초과도 가설로 넣는다.
- 원인으로 봐야 하는 상태는 `COVERAGE` 표(점검 상태 → 가설 제목, 가설에 있어야 하는 낱말, 해결책 변경)에 있다. 검수(`design_not_addressed`)와 mock 분석이 같은 표를 쓴다.
  `design_not_addressed`만 재분석을 요구하고, 나머지(`uncertain`, `info`, `ok`)는 경고와 답변·메모에만 반영된다.
- mock 모드는 표에 있는데 가설에 없는 상태를 가설로 맨 앞에 넣는다. 근거는 문서에서 찾고, 못 찾으면 점검에 쓴 부품 카드를 근거로 한다 (근거가 없으면 `no_evidence`로 헛재분석이 반복된다).
- mock 모드 평가 문의 61건을 변경 전후로 비교했다 (같은 데이터, 같은 ngspice 경로): 분류 변화 0건, 검수 통과 26 → 27건, 재분석 합계 23 → 22회. 접수·분석·검수 결과가 바뀐 22건은 모두 새 점검·새 조건 추출 때문이다
  (예: M-3은 소프트 스타트가 1순위로 들어가 검수를 통과, N-AQ는 `vdd_v` 36 V가 읽혀 LM358 절대 최대 32 V 초과를 잡음, N-T2는 주변 60 °C를 읽어 확인 기준이 엄격해져 재분석이 1회 늘었으나 통과).
- 새 점검(2026-10-07)을 넣은 뒤 mock 모드 평가 문의 65건(새 부품 4건 포함)을 변경 전후로 비교했다 (같은 데이터, 같은 ngspice 경로): 분류·검수 통과(31건)·재분석 합계(24회) 변화 0건.
  바뀐 15건은 모두 새 점검이 만든 사실(정격 6건, 최대 듀티 `ok` 7건)과 새 부품 문의 Q-1·Q-3·Q-4에서 원인이 가설로 들어간 것이다.
- 한계(새 점검): ILIM 한계는 하단 FET RDS(on)을 고객이 알려 준 25 °C 값으로 보고 온도 변화는 넣지 않는다. 최대 듀티는 손실 전 이상적 값이고 데이터시트 표에 없는 주파수는 최소 오프타임 식의 추정이다.
  충전 전류는 효율 90 %(가정)이고, TS 온도는 NTC 두 점으로 맞춘 곡선의 추정이다. 이 점검들은 평가 문의 Q-1~Q-4의 약점에서 만들었으므로 그 문의로는 효과를 증명하지 못한다.
- 한계: 연속·불연속 판정과 피크 전류는 이상적인 소자 식이고(다이오드·스위치 손실 없음), 인덕터 포화·전류 제한 동작 자체는 모델링하지 않는다. 다이오드 강하 0.4 V는 일반값 가정이다.
  소프트 스타트 시간은 카드의 식으로 계산하며 실제 보드 측정과 비교하지 않았다. LDO 열은 θJA(표준 기판 값)를 쓰므로 실제 기판에서는 다르다. 소프트 스타트 시간을 계산하는 도구(`calc.*`)는 없어서 LLM이 이 원인의 테스트를 만들지 못한다.

## 답변 검사 (`fae_agent/reply_check.py`)

고객 답변에 내부 용어(파일 이름 `tps54331.pdf`·`parts/X.yaml`, 사례 번호 `case:…`, "부품 카드", "카드 기준")가 섞이면 고객은 뜻을 모른다. 평가 M-1~M-3에서 프롬프트로 금지했는데도 새어 나갔다.
응답 단계의 마지막(mock·LLM 둘 다)에서 `scrub_reply`가 이를 "TPS54331 데이터시트", "과거 사례 C-0007", "데이터시트"로 바꾸고, 바꾼 내용과 남은 내부 필드 이름(`il_max_a` 같은 snake_case, 바꾸지는 못하고 알림만)은
내부 메모의 "답변 점검"으로 남긴다. mock 평가 문의 61건 중 내부 표현이 있던 답변 29건이 0건이 됐다. 한계: 규칙에 있는 표현만 바꾼다 ('그래픽 카드는' 같은 말은 건드리지 않도록 뜻이 분명한 꼴만 규칙에 있음).

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
| design_not_addressed | 코드가 계산한 설계 점검(`design_check`)에서 원인으로 봐야 할 상태(`COVERAGE`: 전류 제한 초과, 소프트 스타트 범위 이탈, 최소 온타임 미달·한계, LDO 열 차단·권장 초과, ILIM 한계 부족, 최대 듀티 부족, 충전기 ILIM 핀·입력 전력·TS 창)인데 원인 가설 3개에 해당 내용이 없음 → 점검의 숫자를 가설과 테스트에 쓰도록 재분석 (LLM 모드). mock은 그 가설을 앞에 넣는다 |
| tests_dropped | 바로 앞 시도가 `test_error`(또는 `tests_dropped`)로 되돌려졌는데 재분석이 오류를 고치지 않고 테스트(참고 계산 제외)를 모두 뺌 → 인자·지표 이름을 고쳐 테스트를 유지 (LLM 모드). 그 부품 종류에 쓸 도구가 `TOOL_PART_TYPES`에 있을 때만 지적하고, 센서처럼 도구가 없거나 처음부터 테스트가 없는 시도는 경고만 한다 |

- 정격 판정은 문자열이 아니라 **부품 카드의 한계와 고객 조건을 수치로 비교**한다 (입력·전원 전압, 부하 전류, 출력 커패시터, ESR. 연산증폭기 카드는 `vs_*` 키).
  부품 카드에 한계가 없는 조건은 판단하지 않는다. 절대 최대를 넘는데 가설에 '정격'이 없으면 `rating_not_addressed`, 넘지 않는데 '정격 초과'를 쓰면 `rating_false_claim`이다.
- 값 대조(`check_value`)가 실패하면 지적문에 합격 범위 방향 힌트가 붙는다. 합격 범위는 '증상이 나타나는 쪽'이라 값이 한계보다 커서 문제면 `min=한계`, 작아서 문제면 `max=한계`이고, 확인(verify)은 반대(정상 쪽)다. 새 부품 측정에서 이 방향을 틀린 채 재분석 3번을 다 쓴 사례가 3건 있었고, 방향이 맞는데도 실패하면 가설이 틀린 것이라는 안내도 함께 붙여 통과시키려고 방향만 뒤집는 일을 막는다.
- 재현·참고 계산의 인자가 고객이 알려 준 조건(`vin_v`, `vout_v`, `iout_a`, `fsw_hz` 등, 오차 1 %)과 다르면 경고한다 (`ARG_TO_CONDITION`). 조건을 바꾸는 확인(verify)과 값 대조는 제외. 재분석을 요구하지는 않고, 답변에 쓰지 말라는 문구만 남긴다.
- 도구와 부품 종류의 짝은 `qa.py`의 `TOOL_PART_TYPES`, `CIRCUIT_PART_TYPES` 표에 있다. 표에 없는 도구·부품 종류는 검사하지 않는다.
  `tests_dropped`가 "쓸 수 있는 도구"를 고르는 데도 같은 표를 쓴다 (새 도구를 넣으면 표에 부품 종류를 함께 적는다).
- 연산증폭기 이득-대역폭 계산(`calc.opamp_closed_loop_gain`)은 폐루프 대역폭(GBW÷이득), 주파수별 이득(`gain_ratio`), 출력 진폭, 필요한 슬루율(`slew_ratio` > 1이면 슬루율 한계)을 돌려준다. 1차 모델이라 대역폭 근처의 위상·피킹은 다루지 않는다.
- 벅 리플 계산(`calc.buck_ripple_estimate`)은 연속 전도 모드 식이다. `iout_a`를 주면 임계 부하 전류(`iout_crit_a` = ΔIL/2)와 불연속 여부(`dcm`)를, 항상 스위치가 켜져 있는 시간(`on_time_s` = 듀티/fsw)을 돌려준다. 온타임이 부품의 최소 온타임보다 짧으면 펄스를 건너뛴다.
- 벅 테스트가 모르는 값을 조용히 기본값으로 두면 경고한다 (재분석은 요구하지 않는다): 고객이 ESR을 주지 않았는데 `esr_ohm`에 0을 넣은 경우(ESR 성분이 없는 하한 리플이 나옴), 부품 카드에 `rdson_low_ohm`이 있는 동기 벅인데 `buck_switch`에 `vd_v`를 지정하지 않은 경우(기본 0.4 V는 외부 다이오드 값이라 하단 MOSFET 강하로 바꿔야 함).
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
