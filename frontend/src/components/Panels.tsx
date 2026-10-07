import type { ReactNode } from "react";

import type { RunState, RunStatus } from "../types";
import ResponseChart, { extractResponses } from "./ResponseChart";
import WaveChart, { extractWaveforms } from "./WaveChart";

export const STATUS_KO: Record<RunStatus, string> = {
  queued: "대기 중",
  running: "처리 중",
  awaiting_review: "승인 대기",
  completed: "완료",
  rejected: "반려",
  failed: "실패",
};

export const CATEGORY_KO: Record<string, string> = {
  technical: "기술 문의",
  quality_claim: "품질 클레임",
  logistics: "배송·주문",
  other: "기타",
};

const CONDITION_KO: Record<string, string> = {
  vin_v: "입력 전압(V)",
  vout_v: "출력 전압(V)",
  vout_meas_v: "측정 출력(V)",
  ripple_meas_v: "측정 리플(V)",
  tcase_meas_c: "측정 표면 온도(°C)",
  css_f: "SS 커패시터(F)",
  rf_ohm: "피드백 저항 Rf(Ω)",
  rg_ohm: "접지 쪽 저항 Rg(Ω)",
  vin_min_v: "최저 입력 전압(V)",
  vcc_v: "VCC 전압(V)",
  rilim_ohm: "ILIM 저항(Ω)",
  rds_low_ohm: "하단 FET RDS(on)(Ω)",
  iinlim_a: "입력 전류 제한 IINLIM(A)",
  ichg_set_a: "설정 충전 전류(A)",
  vbat_v: "배터리 전압(V)",
  isys_a: "시스템 부하(A)",
  rt1_ohm: "TS 분압 RT1(Ω)",
  rt2_ohm: "TS 분압 RT2(Ω)",
  ntc_r25_ohm: "NTC 25 °C 저항(Ω)",
  iout_a: "부하 전류(A)",
  ta_c: "주변 온도(°C)",
  fsw_hz: "스위칭 주파수(Hz)",
  cout_f: "출력 커패시터(F)",
  esr_ohm: "ESR(Ω)",
  l_h: "인덕터(H)",
  vdd_v: "전원 전압(V)",
  pullup_ohm: "풀업 저항(Ω)",
  cb_f: "버스 커패시턴스(F)",
  r_top_ohm: "분압 상단(Ω)",
  r_bottom_ohm: "분압 하단(Ω)",
};

const STEP_ICON: Record<string, string> = { pass: "✅", fail: "❌", skipped: "⏭️", error: "⚠️" };

export function Panel({ title, badge, children }: { title: string; badge?: ReactNode; children: ReactNode }) {
  return (
    <section className="panel">
      <h3>
        {title}
        {badge}
      </h3>
      {children}
    </section>
  );
}

export function Pill({ tone, children }: { tone: "good" | "warn" | "bad" | "neutral"; children: ReactNode }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

export function IntakePanel({ intake }: { intake: NonNullable<RunState["intake"]> }) {
  const conditions = Object.entries(intake.conditions).filter(([, value]) => value !== null);
  return (
    <Panel title="1. 접수·분류">
      <dl className="kv">
        <dt>분류</dt>
        <dd>
          {CATEGORY_KO[intake.category] ?? intake.category} → 경로 {intake.route} · 긴급도 {intake.urgency}
        </dd>
        <dt>부품</dt>
        <dd>{intake.part_numbers.join(", ") || "미상"}</dd>
        <dt>증상</dt>
        <dd>{intake.symptoms.join(", ") || "미상"}</dd>
        <dt>추출한 조건</dt>
        <dd>{conditions.map(([key, value]) => `${CONDITION_KO[key] ?? key} = ${value}`).join(" · ") || "없음"}</dd>
        {intake.missing_info.length > 0 && (
          <>
            <dt>더 받을 정보</dt>
            <dd>{intake.missing_info.join(", ")}</dd>
          </>
        )}
      </dl>
    </Panel>
  );
}

export function ResearchPanel({ research }: { research: NonNullable<RunState["research"]> }) {
  return (
    <Panel title="2. 기술 조사">
      <p className="muted">문서 근거 {research.evidence.length}건 · 사례 {research.cases.length}건</p>
      <ul className="list">
        {research.evidence.map((e) => (
          <li key={e.source + e.score}>
            <strong>{e.source}</strong> <span className="muted">(점수 {e.score.toFixed(2)})</span>
            <div className="snippet">{e.snippet.slice(0, 160)}…</div>
          </li>
        ))}
      </ul>
      {research.part_card && (
        <details>
          <summary>부품 카드 ({research.part_number})</summary>
          <pre>{JSON.stringify(research.part_card, null, 2)}</pre>
        </details>
      )}
    </Panel>
  );
}

export function DiagnosisPanel({ diagnosis }: { diagnosis: NonNullable<RunState["diagnosis"]> }) {
  return (
    <Panel title="3. 원인 분석·해결책">
      <ol className="list">
        {diagnosis.hypotheses.map((h, index) => (
          <li key={h.cause + index}>
            <strong>{h.cause}</strong>
            {h.mechanism && <div>{h.mechanism}</div>}
            <div className="muted">
              근거: {h.evidence.map((e) => e.source).join(", ") || "없음"}
            </div>
          </li>
        ))}
      </ol>
      {diagnosis.solution && (
        <div className="callout">
          <strong>{diagnosis.solution.summary}</strong>
          <ul>
            {diagnosis.solution.changes.map((c) => (
              <li key={c.target}>
                {c.target}: {c.before} → {c.after}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

export function TestPanel({ report }: { report: NonNullable<RunState["check_report"]> }) {
  const tone = report.status === "pass" ? "good" : report.status === "fail" ? "bad" : "warn";
  return (
    <Panel title="4. 테스트" badge={<Pill tone={tone}>{report.status}</Pill>}>
      {report.steps.length === 0 && <p className="muted">자동 테스트가 없습니다. 엔지니어가 직접 확인해야 합니다.</p>}
      <ul className="steps">
        {report.steps.map((step, index) => {
          const waves = extractWaveforms(step.outputs);
          const responses = extractResponses(step.outputs);
          return (
            <li key={step.label + index}>
              <div>
                {STEP_ICON[step.status]} <span className="tag">{step.purpose}</span> {step.label}
                <span className="muted"> — {step.notes}</span>
              </div>
              {waves.length > 0 && <WaveChart waves={waves} />}
              {responses.map((response) => (
                <ResponseChart key={step.label + index + "r"} response={response} />
              ))}
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

export function QaPanel({ qa, retries }: { qa: NonNullable<RunState["qa"]>; retries: number }) {
  return (
    <Panel
      title="5. 검수"
      badge={<Pill tone={qa.passed ? "good" : "bad"}>{qa.passed ? "통과" : "미통과"} · 재분석 {retries}회</Pill>}
    >
      {qa.issues.map((issue) => (
        <div key={issue} className="note note-bad">
          {issue}
        </div>
      ))}
      {qa.warnings.map((warning) => (
        <div key={warning} className="note note-warn">
          {warning}
        </div>
      ))}
    </Panel>
  );
}

export function RatingPanel({ state }: { state: RunState }) {
  const rating = state.rating_check;
  const cpr = state.cpr;
  if (!rating && !cpr) return null;
  return (
    <Panel title="품질 클레임 점검">
      {rating && rating.abs_over.length > 0 && (
        <div className="note note-warn">
          {rating.abs_over.map((text) => (
            <div key={text}>정격 점검(알려 준 값 기준): {text}. 사용 조건 문제 가능성이 높아 불량 클레임 신뢰도가 낮습니다.</div>
          ))}
        </div>
      )}
      {rating && rating.abs_over.length === 0 && rating.found && (
        <div className="note note-good">
          정격 점검: 알려 준 값은 부품 카드의 절대 최대를 넘지 않습니다 (확인: {rating.within.join(", ") || "없음"}).
        </div>
      )}
      {rating && !rating.found && (
        <div className="note">정격 점검을 하지 못했습니다 (부품 카드 없음). 데이터시트로 직접 확인이 필요합니다.</div>
      )}
      {rating?.rec_over.map((text) => (
        <div key={text} className="muted">
          권장 범위 밖: {text}
        </div>
      ))}
      {cpr && (
        <>
          <ul className="checklist">
            {Object.entries(cpr.checklist).map(([item, ok]) => (
              <li key={item}>
                {ok ? "✅" : "⬜"} {item}
              </li>
            ))}
          </ul>
          <details>
            <summary>8D 보고서 초안</summary>
            <dl className="kv">
              {Object.entries(cpr.eight_d_draft).map(([key, value]) => (
                <div key={key} className="kv-row">
                  <dt>{key}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          </details>
        </>
      )}
    </Panel>
  );
}
