export type RunStatus = "queued" | "running" | "awaiting_review" | "completed" | "rejected" | "failed";

export const ACTIVE: RunStatus[] = ["queued", "running"];

export interface RunSummary {
  id: string;
  customer: string;
  subject: string;
  status: RunStatus;
  mode: string;
  ticket_id: string;
  category: string;
  input_tokens: number;
  output_tokens: number;
  created_at: string;
  updated_at: string;
}

export interface Waveform {
  time_us: number[];
  v: number[];
  ref_v?: number;
}

export interface FreqResponse {
  freq_hz: number[];
  gain_db: number[];
  marks?: {
    freq_hz?: number | null; // 고객이 알려 준(읽은) 주파수
    bw_hz?: number | null; // 측정한 -3 dB 대역폭
    dc_gain_db?: number;
    gain_db_at_freq?: number | null;
  };
}

export interface TestStep {
  label: string;
  purpose: string;
  tool: string;
  status: "pass" | "fail" | "skipped" | "error";
  outputs: Record<string, unknown>;
  notes: string;
}

export interface Hypothesis {
  cause: string;
  mechanism: string;
  confidence: number;
  evidence: { source: string; snippet: string; score: number }[];
}

export interface RunState {
  log?: string[];
  qa_retries?: number;
  intake?: {
    category: string;
    route: string;
    part_numbers: string[];
    symptoms: string[];
    conditions: Record<string, number | null>;
    urgency: string;
    missing_info: string[];
    summary: string;
  };
  research?: {
    part_number: string;
    evidence: { source: string; snippet: string; score: number }[];
    cases: { source: string; title: string; cause: string; fix: string }[];
    part_card: Record<string, unknown> | null;
  };
  diagnosis?: {
    hypotheses: Hypothesis[];
    solution: {
      summary: string;
      changes: { target: string; before: string; after: string }[];
      assumptions: string[];
    } | null;
  };
  check_report?: { status: string; steps: TestStep[] };
  qa?: { passed: boolean; issues: string[]; warnings: string[]; issue_codes: string[] };
  rating_check?: {
    part_number: string;
    found: boolean;
    abs_over: string[];
    rec_over: string[];
    within: string[];
    source_text?: string | null;
  };
  cpr?: { checklist: Record<string, boolean>; missing: string[]; eight_d_draft: Record<string, string> };
  response?: { customer_reply: string; internal_note: string };
}

export interface Review {
  action: string;
  comment: string;
  created_at: string;
}

export interface RunDetail extends RunSummary {
  body: string;
  state: RunState;
  pending_review: { draft: { customer_reply: string; internal_note: string } } | null;
  reviews: Review[];
  error: string;
  finished_at: string | null;
}

export interface AppConfig {
  fae_mode: string;
  llm_model: string;
  tool_mode: string;
  daily_token_budget: number;
  tokens_used_today: number;
}

export interface Sample {
  id: string;
  group: string;
  customer: string;
  subject: string;
  body: string;
}
