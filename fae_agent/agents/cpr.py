"""품질 클레임 점검 에이전트: 반품(CPR) 접수에 필요한 정보가 있는지 보고 8D 보고서 초안을 만듭니다.

필수 정보 목록은 TI 반품 가이드(Guidelines for Returns, SPRAC01B)를 따릅니다.
실제 불량 분석(전기 특성, 열화상, 디캡)은 실험실 작업이라 자동화하지 않습니다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..schemas import Complaint, CPRCheck, IntakeResult

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".heic")


def _mentions(*words: str) -> Callable[[str, IntakeResult, Complaint], bool]:
    return lambda text, intake, complaint: any(w in text for w in words)


REQUIRED: dict[str, Callable[[str, IntakeResult, Complaint], bool]] = {
    "TI 전체 주문 부품 번호": lambda text, intake, c: bool(intake.part_numbers),
    "불량 현상과 사용 조건 설명": lambda text, intake, c: bool(intake.symptoms or intake.conditions.known()),
    "A-B-A 교차 검증 결과(부품을 바꿔 끼워 본 결과와 파형)": _mentions("a-b-a", "aba", "교차", "바꿔 끼", "swap"),
    "부품 상·하면 마킹 사진": lambda text, intake, c: (
        _mentions("사진", "photo", "마킹", "marking")(text, intake, c)
        or any(a.lower().endswith(IMAGE_EXT) for a in c.attachments)
    ),
    "응용 회로도(전압·전류 조건 포함)": lambda text, intake, c: (
        _mentions("회로도", "schematic")(text, intake, c) or any(a.lower().endswith(".pdf") for a in c.attachments)
    ),
}


def run_cpr(complaint: Complaint, intake: IntakeResult, rating: dict[str, Any] | None = None) -> CPRCheck:
    text = complaint.text.lower()
    checklist = {name: bool(check(text, intake, complaint)) for name, check in REQUIRED.items()}
    missing = [name for name, ok in checklist.items() if not ok]
    parts = ", ".join(intake.part_numbers) or "미상"
    conditions = ", ".join(f"{k}={v:g}" for k, v in intake.conditions.known().items()) or "미상"
    d4 = "불량 분석(FA) 결과 후 작성. 과전기 스트레스(EOS)나 원인 미확인(TNI) 가능성도 함께 검토"
    if rating and rating.get("abs_over"):
        d4 = (
            f"고객이 알려 준 값이 부품 카드의 절대 최대를 넘음 ({'; '.join(rating['abs_over'])}). "
            "EOS(과전기 스트레스)를 우선 검토하고 FA 결과로 확정"
        )
    eight_d = {
        "D1 팀 구성": "품질 엔지니어, FAE, 고객 담당자 (지정 필요)",
        "D2 문제 기술": f"부품 {parts} / 증상 {', '.join(intake.symptoms) or '미상'} / 조건 {conditions} / "
        f"고객 설명: {complaint.body[:200]}",
        "D3 임시 조치": "고객 재고와 같은 로트 격리 여부 확인 필요",
        "D4 근본 원인": d4,
        "D5 시정 조치": "FA 결과 후 작성",
        "D6 효과 검증": "시정 조치 후 재현 시험으로 확인",
        "D7 재발 방지": "설계·공정·검사 기준에 반영할 항목 정리",
        "D8 종결": "고객 확인 후 종결",
    }
    return CPRCheck(checklist=checklist, missing=missing, eight_d_draft=eight_d)
