import { useState } from "react";

import { api } from "../api";
import type { RunDetail } from "../types";
import { ACTIVE } from "../types";
import {
  CATEGORY_KO,
  DiagnosisPanel,
  IntakePanel,
  Panel,
  Pill,
  QaPanel,
  RatingPanel,
  ResearchPanel,
  STATUS_KO,
  TestPanel,
} from "./Panels";

const STATUS_TONE = {
  queued: "neutral",
  running: "warn",
  awaiting_review: "warn",
  completed: "good",
  rejected: "bad",
  failed: "bad",
} as const;

function Progress({ run }: { run: RunDetail }) {
  const log = run.state.log ?? [];
  const active = ACTIVE.includes(run.status);
  return (
    <Panel title="처리 진행" badge={active ? <span className="spinner" aria-label="처리 중" /> : undefined}>
      <ol className="progress">
        {log.map((line, index) => (
          <li key={index + line}>
            <span className="tick">✓</span> {line}
          </li>
        ))}
        {active && (
          <li className="pending">
            <span className="tick">…</span> {run.status === "queued" ? "작업자를 기다리는 중" : "다음 단계 처리 중"}
          </li>
        )}
        {!active && log.length === 0 && <li className="muted">기록이 없습니다.</li>}
      </ol>
    </Panel>
  );
}

function ReviewBox({ run, onDone }: { run: RunDetail; onDone: (run: RunDetail) => void }) {
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(action: "approve" | "revise" | "reject") {
    if (action === "revise" && !comment.trim()) {
      setError("수정 요청에는 수정할 내용을 적어 주세요.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      onDone(await api.review(run.id, action, comment));
      setComment("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="review">
      <label htmlFor="review-comment">수정 요청 내용 (수정 요청일 때만 필요)</label>
      <textarea
        id="review-comment"
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        rows={2}
        placeholder="예: 답변을 더 짧게 써 주세요"
      />
      <div className="buttons">
        <button className="primary" disabled={busy} onClick={() => submit("approve")}>
          승인
        </button>
        <button disabled={busy} onClick={() => submit("revise")}>
          수정 요청
        </button>
        <button className="danger" disabled={busy} onClick={() => submit("reject")}>
          반려
        </button>
      </div>
      {error && <div className="note note-bad">{error}</div>}
    </div>
  );
}

function ReplyPanel({ run, onUpdated }: { run: RunDetail; onUpdated: (run: RunDetail) => void }) {
  const draft = run.pending_review?.draft ?? run.state.response;
  if (!draft) return null;
  return (
    <Panel title="고객 답변 초안">
      <pre className="reply">{draft.customer_reply}</pre>
      <details>
        <summary>내부 메모</summary>
        <pre>{draft.internal_note}</pre>
      </details>
      {run.status === "awaiting_review" && <ReviewBox run={run} onDone={onUpdated} />}
      {run.reviews.length > 0 && (
        <ul className="reviews">
          {run.reviews.map((review, index) => (
            <li key={index}>
              {new Date(review.created_at).toLocaleTimeString("ko-KR")} ·{" "}
              {{ approve: "승인", revise: "수정 요청", reject: "반려" }[review.action] ?? review.action}
              {review.comment && ` — ${review.comment}`}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export default function RunView({ run, onUpdated }: { run: RunDetail; onUpdated: (run: RunDetail) => void }) {
  const { state } = run;
  return (
    <div className="run">
      <header className="run-head">
        <div>
          <h2>{run.subject || "(제목 없음)"}</h2>
          <div className="muted">
            {run.customer} · {run.ticket_id || "티켓 생성 전"} · {CATEGORY_KO[run.category] ?? "분류 전"} ·{" "}
            {run.mode} 모드
            {run.mode === "llm" && ` · 토큰 ${(run.input_tokens + run.output_tokens).toLocaleString()}`}
          </div>
        </div>
        <Pill tone={STATUS_TONE[run.status]}>{STATUS_KO[run.status]}</Pill>
      </header>

      <details className="inquiry">
        <summary>고객 문의 원문</summary>
        <p>{run.body}</p>
      </details>

      {run.status === "failed" && <div className="note note-bad">처리에 실패했습니다: {run.error}</div>}
      {run.status === "completed" && <div className="note note-good">승인되어 완료되었습니다.</div>}
      {run.status === "rejected" && <div className="note note-warn">반려되었습니다.</div>}

      <Progress run={run} />
      {state.intake && <IntakePanel intake={state.intake} />}
      {state.research && <ResearchPanel research={state.research} />}
      {state.diagnosis && <DiagnosisPanel diagnosis={state.diagnosis} />}
      {state.check_report && <TestPanel report={state.check_report} />}
      {state.qa && <QaPanel qa={state.qa} retries={state.qa_retries ?? 0} />}
      <RatingPanel state={state} />
      <ReplyPanel run={run} onUpdated={onUpdated} />
    </div>
  );
}
