"""영업 도구: 문의 글에서 분류·요청 점검·부품 추천을 코드로 돌린다 (MCP 서버 이름: sales).

모두 규칙 기반이라 LLM을 부르지 않고 토큰이 들지 않는다. 가격·납기·재고는 데이터가 없어 어떤 도구도 말하지 않는다.
에이전트 모듈은 순환 import를 피하려고 함수 안에서 가져온다 (gateway가 이 모듈을 불러오고, 에이전트는 gateway를 부른다).
"""

from __future__ import annotations

from typing import Any


def _intake(subject: str, body: str):
    from ..agents.intake import classify_rules
    from ..schemas import Complaint

    return classify_rules(Complaint(subject=subject, body=body))


def _summary(intake) -> dict[str, Any]:
    conditions = {k: v for k, v in intake.conditions.model_dump().items() if v is not None}
    return {
        "category": intake.category,
        "route": intake.route,
        "part_numbers": intake.part_numbers,
        "symptoms": intake.symptoms,
        "conditions": conditions,
        "request": intake.request.model_dump(),
        "urgency": intake.urgency,
    }


def classify_inquiry(subject: str, body: str) -> dict[str, Any]:
    """고객 문의를 규칙으로 분류한다(기술 문의, 품질 클레임, 견적·샘플, 설계 상담, 배송, 기타). 부품 번호, 증상, 입력·출력 조건, 수량·시점 같은 요청 항목도 뽑는다."""
    return _summary(_intake(subject, body))


def check_request(subject: str, body: str) -> dict[str, Any]:
    """견적·샘플 문의에서 고객이 준 항목과 빠진 항목(부품 번호, 수량, 용도, 필요 시점, 시제품/양산)을 정리한다. 가격·납기·재고는 말하지 않는다."""
    from ..agents.sales import run_sales_check
    from .gateway import ToolGateway

    intake = _intake(subject, body)
    if intake.category != "quote_sample":
        return {"applicable": False, "category": intake.category, "message": "견적·샘플 문의로 분류되지 않았습니다."}
    return {"applicable": True, **run_sales_check(intake, ToolGateway("local"))}


def recommend_parts(subject: str, body: str) -> dict[str, Any]:
    """설계 상담 문의의 요구사항(입력 범위, 출력 전압, 부하 전류)을 부품 카드와 수치로 비교해 후보별 통과·탈락 이유를 돌려준다. 전원(LDO·벅)만, 카드가 있는 부품 안에서만 비교한다."""
    from ..agents.recommend import run_recommend
    from ..schemas import Complaint
    from .gateway import ToolGateway

    intake = _intake(subject, body)
    if intake.category != "design_request":
        return {"applicable": False, "category": intake.category, "message": "설계 상담 문의로 분류되지 않았습니다."}
    return {"applicable": True, **run_recommend(intake, Complaint(subject=subject, body=body).text, ToolGateway("local"))}


SALES_TOOLS = {fn.__name__: fn for fn in (classify_inquiry, check_request, recommend_parts)}
