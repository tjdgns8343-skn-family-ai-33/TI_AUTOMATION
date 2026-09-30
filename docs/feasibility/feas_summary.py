"""feas_run.py 결과(jsonl)를 모드별로 집계해 표로 출력한다.

사용: python docs/feasibility/feas_summary.py docs/feasibility/results data/eval/eval_real_parts.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RES = Path(sys.argv[1])
CASES = {json.loads(l)["id"]: json.loads(l) for l in Path(sys.argv[2]).read_text(encoding="utf-8").splitlines() if l.strip()}


ROOT = Path(__file__).resolve().parents[2]  # 프로젝트 루트


def valid_sources() -> set[str]:
    """색인에 실제 존재하는 출처: 문서 조각, 부품 카드, 사례 카드."""
    lines = (ROOT / "data/processed/chunks.jsonl").read_text(encoding="utf-8").splitlines()
    found = {json.loads(l)["source"] for l in lines if l.strip()}
    found |= {f"parts/{p.name}" for p in (ROOT / "data/parts").glob("*.y*ml")}
    case_lines = (ROOT / "data/cases/case_cards.jsonl").read_text(encoding="utf-8").splitlines()
    found |= {f"case:{json.loads(l)['id']}" for l in case_lines if l.strip()}
    return found


VALID = valid_sources()


def cite_stats(row: dict) -> tuple[int, int]:
    cited = [s for h in row.get("hypotheses", []) for s in h.get("sources", []) if s]
    return len(cited), sum(1 for c in cited if c not in VALID)


def load(mode: str) -> list[dict]:
    path = RES / f"{mode}.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def frac(values: list) -> str:
    valid = [v for v in values if v is not None]
    return f"{sum(1 for v in valid if v)}/{len(valid)}" if valid else "-"


for mode in ("mock", "llm", "baseline"):
    rows = load(mode)
    if not rows:
        continue
    tech = [r for r in rows if CASES[r["id"]]["expected_category"] == "technical" and "error" not in r]
    errs = [r["id"] for r in rows if "error" in r]
    print(f"\n===== {mode}  (전체 {len(rows)}건, 기술 문의 {len(tech)}건, 오류 {errs})")
    print("분류 정확도(전체):", frac([r.get("judge", {}).get("category_ok") for r in rows if "error" not in r]))
    if mode != "baseline":
        print("증상 코드 일치:", frac([r["judge"]["symptom_ok"] for r in tech]))
        print("부품 번호 인식:", frac([r["judge"]["part_ok"] for r in tech]))
    print("원인 Top-3 적중(자동, 키워드):", frac([r["judge"]["cause_hit_top3_auto"] for r in tech]))
    print("원인 Top-1 적중(자동, 키워드):", frac([r["judge"]["cause_hit_top1_auto"] for r in tech]))
    number_rows = [r["judge"] for r in tech if r.get("judge", {}).get("numbers_total")]
    if number_rows:
        print("정답 숫자가 답에 나온 비율:", sum(j["numbers_hit"] for j in number_rows), "/", sum(j["numbers_total"] for j in number_rows))
    ask_rows = [r["judge"] for r in tech if r.get("judge", {}).get("asks_total")]
    if ask_rows:
        print("정보 부족 문의에서 되물은 항목:", sum(j["asks_hit"] for j in ask_rows), "/", sum(j["asks_total"] for j in ask_rows))
    if mode != "baseline":
        print("숫자 검증 테스트가 있는 건:", frac([r["judge"]["n_numeric_tests"] > 0 for r in tech]))
        print("  그중 전부 통과:", frac([r["judge"]["numeric_all_pass"] for r in tech if r["judge"]["n_numeric_tests"] > 0]))
        print("검수 통과:", frac([r["qa_passed"] for r in tech]))
        print("재분석 횟수 합/평균:", sum(r["qa_retries"] for r in tech), round(sum(r["qa_retries"] for r in tech) / max(len(tech), 1), 2))
        stats = [cite_stats(r) for r in tech]
        print("가설에 붙은 인용 수 / 색인에 없는 출처(환각):", sum(a for a, _ in stats), "/", sum(b for _, b in stats))
    secs = [r["seconds"] for r in rows if "error" not in r]
    print("건당 평균 시간(초):", round(sum(secs) / max(len(secs), 1), 1))
    if any("usage" in r for r in rows):
        tot = {k: sum(r.get("usage", {}).get(k, 0) for r in rows) for k in ("input_tokens", "output_tokens", "total_tokens")}
        print("토큰 합계:", tot, "| 건당 평균 총 토큰:", round(tot["total_tokens"] / max(len(rows), 1)))
    print(f"{'ID':<6} 분류 증상 부품 Top3 Top1  검수 재분석 테스트  숫자테스트")
    for r in rows:
        j = r.get("judge", {})
        print(
            f"{r['id']:<6} {str(j.get('category_ok'))[:1]:<3} {str(j.get('symptom_ok'))[:1]:<4} {str(j.get('part_ok'))[:1]:<4} "
            f"{str(j.get('cause_hit_top3_auto'))[:1]:<4} {str(j.get('cause_hit_top1_auto'))[:1]:<4} "
            f"{str(r.get('qa_passed'))[:1]:<4} {str(r.get('qa_retries')):<5} {str(r.get('test_status')):<8} {j.get('n_numeric_tests', '-')}"
            + (f"  숫자 {j['numbers_hit']}/{j['numbers_total']}" if j.get("numbers_total") else "")
            + (f"  되묻기 {j['asks_hit']}/{j['asks_total']}" if j.get("asks_total") else "")
            + f"  {r.get('error', '')}"
        )
