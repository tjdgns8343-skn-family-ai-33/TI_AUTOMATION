"""영업 요청 점검 에이전트(견적·샘플): 견적·샘플 처리에 필요한 정보가 있는지 코드로 점검하고, 아는 부품은 카드의 사실만 붙인다.

이 시스템에는 가격·납기·재고 데이터가 없다. 그래서 가격과 납기는 절대 말하지 않고 "영업 시스템에서 확인"으로 넘기며,
고객이 알려 준 값(수량, 시점, 용도)만 정리하고 빠진 것을 되묻는다. 부품 소개는 부품 카드의 설명(데이터시트 값)만 쓴다.
"""

from __future__ import annotations

from typing import Any

from ..schemas import IntakeResult
from ..tools.gateway import ToolGateway

CHECKLIST = (
    ("part_numbers", "부품 번호(전체 주문 번호)"),
    ("quantity", "필요 수량"),
    ("application", "사용 용도·제품"),
    ("need_by", "필요한 시점(납기)"),
    ("stage", "시제품(평가)인지 양산인지"),
)
OUT_OF_SCOPE_NOTE = "가격·납기·재고는 이 시스템에 데이터가 없어 영업 시스템에서 확인한 뒤 담당자가 회신합니다."


def run_sales_check(intake: IntakeResult, gateway: ToolGateway) -> dict[str, Any]:
    request = intake.request
    values = {
        "part_numbers": ", ".join(intake.part_numbers),
        "quantity": f"{request.quantity}개" if request.quantity else "",
        "application": request.application,
        "need_by": request.need_by,
        "stage": {"prototype": "시제품(평가)", "production": "양산"}.get(request.stage, ""),
    }
    collected = {label: values[key] for key, label in CHECKLIST if values[key]}
    missing = [label for key, label in CHECKLIST if not values[key]]
    parts = []
    for part in intake.part_numbers:
        card = gateway.call("kb.get_part_card", part_number=part)["card"]
        parts.append(
            {"part": part, "known": bool(card), "description": (card or {}).get("description", ""), "source": (card or {}).get("source", "")}
        )
    return {
        "kind": request.kind or "quote",
        "collected": collected,
        "missing": missing,
        "parts": parts,
        "notes": request.notes,
        "note": OUT_OF_SCOPE_NOTE,
    }
