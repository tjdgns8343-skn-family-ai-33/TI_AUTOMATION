"""기술 조사 에이전트: 부품 스펙 카드, 관련 문서 조각, 비슷한 과거 사례를 모읍니다."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..llm import is_llm_mode, structured_call
from ..schemas import Complaint, IntakeResult
from ..tools.gateway import ToolGateway
from .prompts import QUERY_SYSTEM

# 증상 코드 → 영어 문서 검색어
SYMPTOM_QUERIES = {
    "oscillation": "output oscillation ringing load step phase margin compensation output capacitor ESR",
    "ripple_noise": "output ripple noise output capacitor ESR inductance probe ground",
    "overheating": "thermal junction temperature power loss theta_JA dissipation",
    "wrong_output": "output voltage setting Vref feedback divider current limit",
    "startup_fail": "startup soft-start UVLO enable inrush current limit hiccup",
    "communication": "I2C pull-up resistor rise time bus capacitance address",
    "damage": "absolute maximum ratings electrical overstress EOS",
}


class SearchQuery(BaseModel):
    query: str


def build_query(complaint: Complaint, intake: IntakeResult) -> str:
    terms = [SYMPTOM_QUERIES[s] for s in intake.symptoms if s in SYMPTOM_QUERIES]
    return " ".join([*intake.part_numbers, *terms, complaint.subject]).strip()


def run_research(complaint: Complaint, intake: IntakeResult, gateway: ToolGateway) -> dict[str, Any]:
    part = intake.part_numbers[0] if intake.part_numbers else ""
    query = build_query(complaint, intake)
    if is_llm_mode():  # LLM이 영어 검색어를 만들어 한국어 문의와 영어 문서 사이를 잇습니다
        query = f"{structured_call(SearchQuery, QUERY_SYSTEM, complaint.text).query} {query}"
    evidence = gateway.call("kb.search_docs", query=query, part_number=part, k=5)["results"]
    cases = gateway.call("kb.search_cases", query=query, k=3)["results"]
    card = gateway.call("kb.get_part_card", part_number=part)["card"] if part else None
    return {"part_number": part, "query": query, "evidence": evidence, "cases": cases, "part_card": card}
