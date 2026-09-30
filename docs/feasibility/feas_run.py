"""가능성 검증용 실행기: 평가 문의를 mock / llm / baseline(도구 없는 LLM 1회)으로 돌려 결과를 jsonl로 저장한다.

사용 (프로젝트 루트에서, ti_project 환경):
  python docs/feasibility/feas_run.py --mode mock|llm|baseline --cases data/eval/eval_real_parts.jsonl
      --out docs/feasibility/results/<이름>.jsonl [--only ID ...]
주의: llm·baseline 모드는 OpenAI API를 호출한다(비용 발생). 이미 결과가 있는 ID는 건너뛴다.

문의 파일의 선택 항목:
  expected_numbers: 정답 답변에 나와야 하는 숫자 [{"any": [값, ...], "rel": 허용 상대오차(기본 0.02)}]
  expected_asks:    정보 부족 문의에서 되물어야 할 항목 [[동의어, ...], ...]
  accept_categories: 정답으로 인정할 분류 목록 (없으면 expected_category 하나)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # 프로젝트 루트
sys.path.insert(0, str(ROOT))


def cause_text(hypotheses: list[dict], n: int = 3) -> str:
    return " ".join(f"{h.get('cause', '')} {h.get('mechanism', '')}" for h in hypotheses[:n])


def hit(keywords: list[str], text: str) -> bool | None:
    if not keywords:
        return None
    lowered = text.lower()
    return any(k.lower() in lowered for k in keywords)


NUM_RE = re.compile(r"\d[\d,]*\.?\d*")


def numbers_in(text: str) -> list[float]:
    found = []
    for raw in NUM_RE.findall(text or ""):
        try:
            found.append(float(raw.replace(",", "").rstrip(".")))
        except ValueError:
            continue
    return found


def extra_judge(case: dict, text: str, asks_text: str) -> dict:
    """정답 숫자가 답에 나오는지, 정보 부족 문의에서 되물었는지 (해당 항목이 있는 문의만)."""
    out: dict = {}
    specs = case.get("expected_numbers") or []
    if specs:
        nums = numbers_in(text)
        hits = sum(
            1 for spec in specs
            if any(abs(n - v) <= max(spec.get("tol", 0.0), spec.get("rel", 0.02) * abs(v)) for v in spec["any"] for n in nums)
        )
        out.update(numbers_hit=hits, numbers_total=len(specs))
    groups = case.get("expected_asks") or []
    if groups:
        lowered = asks_text.lower()
        out.update(asks_hit=sum(1 for g in groups if any(w.lower() in lowered for w in g)), asks_total=len(groups))
    return out


def category_ok(predicted: str | None, case: dict) -> bool:
    return predicted in case.get("accept_categories", [case["expected_category"]])


def usage_totals(usage: dict) -> dict:
    total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for value in (usage or {}).values():
        for key in total:
            total[key] += int(value.get(key, 0) or 0)
    return total


def run_pipeline(case: dict, usage_cb) -> dict:
    from fae_agent.graph import build_graph, new_thread_id, start_run
    from fae_agent.schemas import Complaint
    from fae_agent.waveform import strip_waveforms

    graph = build_graph()
    complaint = Complaint(customer=case.get("customer", "평가"), subject=case.get("subject", ""), body=case["body"])
    result = start_run(graph, complaint, new_thread_id())
    result = strip_waveforms(result)
    intake = result.get("intake") or {}
    diagnosis = result.get("diagnosis") or {}
    hyps = diagnosis.get("hypotheses") or []
    report = result.get("check_report") or {}
    steps = report.get("steps") or []
    evidence_sources = {e.get("source") for e in (result.get("research") or {}).get("evidence", [])}
    cited = [e.get("source") for h in hyps for e in h.get("evidence", []) if e.get("source")]
    numeric = [
        s for s in steps
        if s["status"] in ("pass", "fail") and s["tool"].split(".")[0] in ("calc", "spice") and s["purpose"] != "info"
    ]
    keywords = case.get("expected_cause_keywords", [])
    text3 = cause_text(hyps, 3)
    return {
        "intake": {k: intake.get(k) for k in ("category", "route", "part_numbers", "symptoms", "missing_info")},
        "hypotheses": [
            {"cause": h.get("cause"), "mechanism": h.get("mechanism"), "sources": [e.get("source") for e in h.get("evidence", [])]}
            for h in hyps
        ],
        "solution_summary": (diagnosis.get("solution") or {}).get("summary"),
        "solution_changes": (diagnosis.get("solution") or {}).get("changes"),
        "test_plan": (diagnosis.get("solution") or {}).get("test_plan"),
        "evidence_sources": sorted(
            {e.get("source") for e in (result.get("research") or {}).get("evidence", [])}
            | {c.get("source") for c in (result.get("research") or {}).get("cases", [])}
            | {(result.get("research") or {}).get("part_card", {}).get("_source") if (result.get("research") or {}).get("part_card") else None}
            - {None}
        ),
        "test_status": report.get("status"),
        "test_steps": [
            {"purpose": s["purpose"], "tool": s["tool"], "label": s["label"], "status": s["status"], "notes": s["notes"]}
            for s in steps
        ],
        "qa_passed": (result.get("qa") or {}).get("passed"),
        "qa_retries": result.get("qa_retries", 0),
        "qa_issue_codes": (result.get("qa") or {}).get("issue_codes"),
        "qa_issues": (result.get("qa") or {}).get("issues"),
        "response": result.get("response"),
        "judge": {
            "category_ok": category_ok(intake.get("category"), case),
            "symptom_ok": None if not case.get("expected_symptom") else case["expected_symptom"] in (intake.get("symptoms") or []),
            **extra_judge(
                case,
                " ".join(
                    [text3, str((diagnosis.get("solution") or {}).get("summary")), json.dumps(result.get("response") or {}, ensure_ascii=False)]
                    + [s["notes"] for s in steps]
                ),
                " ".join(intake.get("missing_info") or []) + json.dumps(result.get("response") or {}, ensure_ascii=False),
            ),
            "part_ok": None if not case.get("part") else case["part"].upper() in [p.upper() for p in intake.get("part_numbers") or []],
            "cause_hit_top3_auto": hit(keywords, text3),
            "cause_hit_top1_auto": hit(keywords, cause_text(hyps, 1)),
            "n_numeric_tests": len(numeric),
            "numeric_all_pass": bool(numeric) and all(s["status"] == "pass" for s in numeric),
            "n_cited": len(cited),
            "n_cited_not_in_evidence": sum(1 for c in cited if c not in evidence_sources),
        },
    }


def run_baseline(case: dict) -> dict:
    from pydantic import BaseModel, Field

    from fae_agent.llm import structured_call

    class BaselineAnswer(BaseModel):
        category: str = Field(description="technical | quality_claim | logistics | other")
        causes: list[str] = Field(description="가능성 높은 순서로 최대 3개의 원인 가설")
        fix: str = Field(description="권장 조치")
        reply: str = Field(description="고객에게 보낼 답변 초안")

    system = (
        "너는 반도체 회사의 FAE(현장 응용 엔지니어)다. 고객 문의를 읽고 네가 아는 지식만으로 답한다. "
        "데이터시트 검색이나 계산 도구는 없다. 원인은 가능성 높은 순서로 최대 3개, 한국어로 쓴다."
    )
    answer = structured_call(BaselineAnswer, system, f"제목: {case.get('subject', '')}\n{case['body']}")
    text = " ".join(answer.causes)
    keywords = case.get("expected_cause_keywords", [])
    return {
        "baseline": answer.model_dump(),
        "judge": {
            "category_ok": category_ok(answer.category, case),
            "cause_hit_top3_auto": hit(keywords, text),
            "cause_hit_top1_auto": hit(keywords, answer.causes[0] if answer.causes else ""),
            **extra_judge(case, json.dumps(answer.model_dump(), ensure_ascii=False), json.dumps(answer.model_dump(), ensure_ascii=False)),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["mock", "llm", "baseline"], required=True)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--max-tokens", type=int, default=None, help="이 출력 파일의 누적 토큰이 넘으면 다음 사례부터 멈춘다")
    args = parser.parse_args()

    os.environ["FAE_MODE"] = "mock" if args.mode == "mock" else "llm"
    os.environ["AUTO_APPROVE"] = "true"
    os.environ["TOOL_MODE"] = "local"
    from fae_agent.config import get_settings

    get_settings.cache_clear()

    try:
        from langchain_core.callbacks import get_usage_metadata_callback
    except ImportError:  # 토큰 집계를 못 해도 실행은 한다
        get_usage_metadata_callback = None

    cases = [json.loads(line) for line in Path(args.cases).read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {json.loads(line)["id"] for line in out.read_text(encoding="utf-8").splitlines() if line.strip()}

    for case in cases:
        if case["id"] in done:
            print(f"{case['id']} 이미 있음, 건너뜀", flush=True)
            continue
        started = time.time()
        row = {"id": case["id"], "mode": args.mode, "part": case.get("part"), "expected_category": case["expected_category"]}
        try:
            if get_usage_metadata_callback and args.mode != "mock":
                with get_usage_metadata_callback() as cb:
                    row.update(run_baseline(case) if args.mode == "baseline" else run_pipeline(case, cb))
                row["usage"] = usage_totals(cb.usage_metadata)
            else:
                row.update(run_pipeline(case, None))
        except Exception as exc:  # 한 건이 실패해도 나머지는 계속
            row["error"] = f"{type(exc).__name__}: {exc}"[:400]
        row["seconds"] = round(time.time() - started, 1)
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        j = row.get("judge", {})
        print(
            f"{case['id']:<6} {args.mode:<8} 분류 {j.get('category_ok')} 원인Top3 {j.get('cause_hit_top3_auto')} "
            f"검수 {row.get('qa_passed')} 재분석 {row.get('qa_retries')} {row['seconds']}s {row.get('error', '')}",
            flush=True,
        )
        if args.max_tokens:
            spent = sum(
                json.loads(line).get("usage", {}).get("total_tokens", 0)
                for line in out.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
            if spent > args.max_tokens:
                print(f"토큰 한도 도달({spent:,} > {args.max_tokens:,}). 남은 사례는 실행하지 않고 멈춥니다.", flush=True)
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
