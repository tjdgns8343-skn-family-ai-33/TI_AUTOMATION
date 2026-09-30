import { useState } from "react";

import { api } from "../api";
import type { RunDetail, Sample } from "../types";

export default function InquiryForm({
  samples,
  onCreated,
}: {
  samples: Sample[];
  onCreated: (run: RunDetail) => void;
}) {
  const [customer, setCustomer] = useState("가상전자");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const groups = Array.from(new Set(samples.map((s) => s.group)));

  function pickSample(id: string) {
    const sample = samples.find((s) => s.id === id);
    if (!sample) return;
    setCustomer(sample.customer || "고객");
    setSubject(sample.subject);
    setBody(sample.body);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      onCreated(await api.createRun({ customer, subject, body }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="form" onSubmit={submit}>
      <h2>새 문의</h2>
      <label htmlFor="sample">예시 문의 (고르면 아래가 채워집니다)</label>
      <select id="sample" defaultValue="" onChange={(e) => pickSample(e.target.value)}>
        <option value="">직접 입력</option>
        {groups.map((group) => (
          <optgroup key={group} label={group}>
            {samples
              .filter((s) => s.group === group)
              .map((s) => (
                <option key={s.id} value={s.id}>
                  {s.subject || s.id}
                </option>
              ))}
          </optgroup>
        ))}
      </select>

      <label htmlFor="customer">고객사</label>
      <input id="customer" value={customer} onChange={(e) => setCustomer(e.target.value)} />
      <label htmlFor="subject">제목</label>
      <input id="subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
      <label htmlFor="body">문의 내용</label>
      <textarea
        id="body"
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={6}
        placeholder="예: EXAMPLE-LDO1에 입력 7V, 출력 3.3V, 부하 0.3A로 쓰는데 IC가 너무 뜨겁습니다."
      />
      {error && <div className="note note-bad">{error}</div>}
      <button className="primary" type="submit" disabled={busy || body.trim().length < 5}>
        {busy ? "접수 중…" : "분석 시작"}
      </button>
    </form>
  );
}
