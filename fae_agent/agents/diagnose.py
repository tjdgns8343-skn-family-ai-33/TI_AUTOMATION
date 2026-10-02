"""원인 분석·해결 에이전트: 원인 가설 3개와 해결책, 테스트 계획을 만듭니다.

검수에서 되돌아오면 지적 내용(issue_codes)을 반영해 다시 분석합니다.
- reproduce_fail      : 1순위 가설이 증상을 재현 못함 → 다음 가설을 1순위로
- verify_fail         : 해결책이 목표 미달 → 변경 폭을 키움
- rating_not_addressed: 정격 초과를 놓침 → 정격 초과 가설을 맨 앞에 추가
- tests_dropped        : 테스트 오류를 고치지 않고 테스트를 뺌 → LLM 모드에서 지적 내용을 프롬프트로 전달 (mock은 해당 없음)
- trivial_test         : 기대 범위가 어떤 값이나 통과함 → 마찬가지로 LLM 모드에서 지적 내용을 전달
- design_not_addressed : 코드가 계산한 전류 제한 초과(design_check)를 가설이 다루지 않음 → LLM 모드에서 지적 내용을 전달
"""

from __future__ import annotations

import json
from typing import Any

from ..design_check import format_facts
from ..llm import is_llm_mode, structured_call
from ..schemas import Complaint, ComponentChange, Diagnosis, Evidence, Hypothesis, IntakeResult
from ..tools.gateway import ToolGateway
from . import playbook
from .checker import describe_test_tools
from .prompts import DIAGNOSE_SYSTEM


def _find_evidence(gateway: ToolGateway, keywords: list[str], part: str) -> list[Evidence]:
    """가설 키워드로 문서를 찾고, 키워드가 실제로 들어 있는 조각만 근거로 씁니다."""
    if not keywords:
        return []
    hits = gateway.call("kb.search_docs", query=" ".join(keywords), part_number=part, k=4)["results"]
    matched = [h for h in hits if any(k in h["snippet"].lower() for k in keywords)]
    return [Evidence(source=h["source"], snippet=h["snippet"][:300], score=h["score"]) for h in matched[:2]]


def next_meta(previous: dict[str, Any] | None, qa: dict[str, Any] | None) -> dict[str, Any]:
    meta = dict((previous or {}).get("meta") or {"candidate_index": 0, "strength": 1, "rating": False})
    codes = set((qa or {}).get("issue_codes", []))
    if "reproduce_fail" in codes:
        meta["candidate_index"] += 1
    if "verify_fail" in codes:
        meta["strength"] += 1
    if "rating_not_addressed" in codes:
        meta["rating"], meta["candidate_index"] = True, 0
    return meta


CURRENT_LIMIT_CAUSE = "인덕터 전류 리플이 커서 전류 제한에 걸림"


def _with_current_limit(
    diagnosis: Diagnosis, design: list[dict[str, Any]] | None, gateway: ToolGateway, part: str
) -> Diagnosis:
    """설계 점검에서 인덕터 최대 전류가 전류 제한 최소값을 넘으면(mock 규칙은 몰라서) 그 가설을 맨 앞에 넣는다."""
    fact = next((f for f in design or [] if f["kind"] == "current_limit" and f["status"] == "exceeds_min"), None)
    if fact is None or any("전류 제한" in h.cause for h in diagnosis.hypotheses):
        return diagnosis
    hypothesis = Hypothesis(
        cause=CURRENT_LIMIT_CAUSE,
        mechanism=fact["text"],
        confidence=0.7,
        evidence=_find_evidence(gateway, ["current limit", "전류 제한"], part),
    )
    solution = diagnosis.solution
    if solution is not None:
        change = ComponentChange(target="인덕터", before="현재 값", after="더 큰 값 (최대 전류가 전류 제한 최소값 아래가 되도록)")
        solution = solution.model_copy(update={"changes": [change, *solution.changes]})
    return Diagnosis(hypotheses=[hypothesis, *diagnosis.hypotheses][:3], solution=solution)


def _rule_diagnose(
    intake: IntakeResult,
    research: dict[str, Any],
    gateway: ToolGateway,
    meta: dict[str, Any],
    design: list[dict[str, Any]] | None = None,
) -> Diagnosis:
    candidates, ctx = playbook.build_candidates(
        intake.symptoms,
        intake.conditions.model_dump(),
        research.get("part_card"),
        strength=meta["strength"],
        add_rating=meta["rating"],
    )
    start = min(meta["candidate_index"], len(candidates) - 1)
    ordered = (candidates[start:] + candidates[:start])[:3]
    part = research.get("part_number", "")
    for candidate in ordered:
        if not candidate.hypothesis.evidence:
            candidate.hypothesis.evidence = _find_evidence(gateway, candidate.keywords, part)
    solution = ordered[0].solution.model_copy(update={"assumptions": [*ctx.assumptions.values(), *ctx.notes]})
    diagnosis = Diagnosis(hypotheses=[c.hypothesis for c in ordered], solution=solution)
    return _with_current_limit(diagnosis, design, gateway, part)


def _llm_diagnose(
    complaint: Complaint,
    intake: IntakeResult,
    research: dict[str, Any],
    qa: dict[str, Any] | None,
    design: list[dict[str, Any]] | None = None,
) -> Diagnosis:
    evidence = "\n".join(f"- [{e['source']}] {e['snippet'][:400]}" for e in research.get("evidence", []))
    cases = "\n".join(
        f"- [{c['source']}] {c['title']} / 원인: {c['cause']} / 조치: {c['fix']}" for c in research.get("cases", [])
    )
    card = json.dumps(research.get("part_card") or {}, ensure_ascii=False, default=str)
    feedback = "\n".join((qa or {}).get("issues", [])) or "없음"
    user = (
        f"# 고객 문의\n{complaint.text}\n\n# 접수 결과\n{intake.model_dump_json()}\n\n"
        f"# 부품 스펙 카드\n{card}\n\n# 근거 목록\n{evidence or '없음'}\n\n"
        f"# 비슷한 과거 사례\n{cases or '없음'}\n\n# 설계 점검 (코드가 계산한 값)\n{format_facts(design) or '없음'}\n\n"
        f"# 사용 가능한 도구\n{describe_test_tools()}\n\n"
        f"# 검수 피드백\n{feedback}"
    )
    return structured_call(Diagnosis, DIAGNOSE_SYSTEM, user)


def run_diagnose(
    complaint: Complaint,
    intake: IntakeResult,
    research: dict[str, Any],
    gateway: ToolGateway,
    previous: dict[str, Any] | None = None,
    qa: dict[str, Any] | None = None,
    design: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """진단 결과(dict)를 돌려줍니다. meta에는 재시도 상태가 들어 있습니다."""
    meta = next_meta(previous, qa)
    if is_llm_mode():
        diagnosis = _llm_diagnose(complaint, intake, research, qa, design)
    else:
        diagnosis = _rule_diagnose(intake, research, gateway, meta, design)
    return {**diagnosis.model_dump(), "meta": meta}
