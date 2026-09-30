import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import InquiryForm from "./components/InquiryForm";
import { CATEGORY_KO, Pill, STATUS_KO } from "./components/Panels";
import RunView from "./components/RunView";
import type { AppConfig, RunDetail, RunSummary, Sample } from "./types";
import { ACTIVE } from "./types";

const TONE = {
  queued: "neutral",
  running: "warn",
  awaiting_review: "warn",
  completed: "good",
  rejected: "bad",
  failed: "bad",
} as const;

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState("");

  const refreshList = useCallback(async () => {
    try {
      const [list, conf] = await Promise.all([api.runs(), api.config()]);
      setRuns(list);
      setConfig(conf);
      setError("");
    } catch (e) {
      setError(`서버에 연결할 수 없습니다: ${(e as Error).message}`);
    }
  }, []);

  const loadDetail = useCallback(async (id: string) => {
    try {
      setDetail(await api.run(id));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    api.samples().then(setSamples).catch(() => undefined);
    void refreshList();
    const timer = setInterval(() => void refreshList(), 4000);
    return () => clearInterval(timer);
  }, [refreshList]);

  useEffect(() => {
    if (selectedId) void loadDetail(selectedId);
    else setDetail(null);
  }, [selectedId, loadDetail]);

  // 처리 중인 문의는 1.5초마다 다시 읽어 진행 단계가 실시간으로 채워지게 한다
  const active = detail !== null && ACTIVE.includes(detail.status);
  useEffect(() => {
    if (!selectedId || !active) return;
    const timer = setInterval(() => void loadDetail(selectedId), 1500);
    return () => clearInterval(timer);
  }, [selectedId, active, loadDetail]);

  function select(run: RunDetail) {
    setDetail(run);
    setSelectedId(run.id);
    void refreshList();
  }

  const budget = config
    ? config.fae_mode === "llm"
      ? `llm 모드 (${config.llm_model}) · 오늘 토큰 ${config.tokens_used_today.toLocaleString()} / ${config.daily_token_budget.toLocaleString()}`
      : "mock 모드 · OpenAI 요금 없음"
    : "서버 확인 중…";

  return (
    <div className="app">
      <header className="topbar">
        <h1>FAE Agent</h1>
        <span className="muted">반도체 고객 기술 문의 → 근거·계산·검수 → 사람 승인</span>
        <span className={`mode ${config?.fae_mode === "llm" ? "mode-llm" : ""}`}>{budget}</span>
      </header>
      {error && <div className="note note-bad banner">{error}</div>}
      <div className="layout">
        <aside>
          <InquiryForm samples={samples} onCreated={select} />
          <section className="runs">
            <h2>최근 문의</h2>
            {runs.length === 0 && <p className="muted">아직 문의가 없습니다.</p>}
            <ul>
              {runs.map((run) => (
                <li key={run.id}>
                  <button className={run.id === selectedId ? "run-item selected" : "run-item"} onClick={() => setSelectedId(run.id)}>
                    <span className="run-title">{run.subject || "(제목 없음)"}</span>
                    <span className="run-meta">
                      {CATEGORY_KO[run.category] ?? "분류 전"} · {new Date(run.created_at).toLocaleTimeString("ko-KR")}
                    </span>
                    <Pill tone={TONE[run.status]}>{STATUS_KO[run.status]}</Pill>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </aside>
        <main>
          {detail && detail.id === selectedId ? (
            <RunView run={detail} onUpdated={select} />
          ) : (
            <div className="empty">
              <h2>문의를 선택하거나 새로 접수하세요</h2>
              <p className="muted">
                왼쪽에서 예시 문의를 고르고 "분석 시작"을 누르면, 접수 → 조사 → 원인 분석 → 계산·시뮬레이션 → 검수를 거쳐
                답변 초안이 나오고 승인을 기다립니다.
              </p>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
