import type { AppConfig, RunDetail, RunSummary, Sample } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { headers: { "Content-Type": "application/json" }, ...init });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    if (response.status === 429) detail = "요청이 너무 많습니다. 1분 정도 기다린 뒤 다시 시도해 주세요.";
    try {
      const data = await response.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data);
    } catch {
      /* 본문이 JSON이 아니면(nginx의 429 등) 위에서 정한 문구를 그대로 쓴다 */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  config: () => request<AppConfig>("/config/"),
  samples: () => request<Sample[]>("/samples/"),
  runs: () => request<RunSummary[]>("/runs/"),
  run: (id: string) => request<RunDetail>(`/runs/${id}/`),
  createRun: (payload: { customer: string; subject: string; body: string }) =>
    request<RunDetail>("/runs/", { method: "POST", body: JSON.stringify(payload) }),
  review: (id: string, action: "approve" | "revise" | "reject", comment: string) =>
    request<RunDetail>(`/runs/${id}/review/`, { method: "POST", body: JSON.stringify({ action, comment }) }),
};
