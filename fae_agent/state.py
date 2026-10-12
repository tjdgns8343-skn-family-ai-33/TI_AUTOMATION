"""LangGraph 그래프 상태.

체크포인트에 저장되므로 값은 모두 JSON으로 바꿀 수 있는 dict·list·str로 둡니다.
(이 파일은 LangGraph가 타입 힌트를 읽으므로 `from __future__ import annotations`를 쓰지 않습니다.)
"""

import operator
from typing import Annotated, Any, TypedDict


class FAEState(TypedDict, total=False):
    ticket_id: str
    complaint: dict[str, Any]  # Complaint
    intake: dict[str, Any]  # IntakeResult
    research: dict[str, Any]  # 검색된 근거·사례·부품 카드
    design_check: list[dict[str, Any]]  # 코드가 계산한 설계 점검 (전류 제한 여유, 잰 리플과 계산 비교)
    diagnosis: dict[str, Any]  # Diagnosis (+ meta)
    check_report: dict[str, Any]  # CheckReport
    qa: dict[str, Any]  # QAReview
    qa_retries: int
    rating_check: dict[str, Any]  # 품질 클레임 경로의 정격 점검 (고객 조건 대 부품 카드 한계)
    cpr: dict[str, Any]  # CPRCheck
    response: dict[str, Any]  # ResponseDraft
    documents: dict[str, Any]  # 승인 뒤 만든 문서 {status, dir, files, issues}
    review: dict[str, Any]  # 사람 검토 결과 {"action", "comment"}
    status: str
    log: Annotated[list[str], operator.add]  # 단계별 진행 기록 (계속 누적)
