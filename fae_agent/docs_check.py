"""승인 뒤에 만든 문서의 검사: 승인된 내용에 없는 숫자·부품 번호·약속·내부 표현이 문서에 들어갔는지 코드로 본다.

고객에게 나가는 문서는 사람이 승인한 문장만 담아야 한다. 템플릿이 실수로 새 숫자를 만들거나, 나중에 문장을 다듬는 단계(LLM 등)가
가격·납기·보증 같은 약속을 끼워 넣으면 문서를 만들지 않고 이유를 알린다. 규칙(정규식)이라 문장의 어색함이나 어조는 보지 못한다.
승인된 내용(source)에 이미 있는 표현은 사람이 본 것이므로 허용한다.
"""

from __future__ import annotations

import math
import re

from .agents.intake import extract_parts

_NUMBER = re.compile(r"(?<![A-Za-z0-9_])\d[\d,]*(?:\.\d+)?")
# 가격·납기·보증·보상 같은 약속 표현. 승인된 내용에 없는데 문서에 있으면 문제다
_COMMITMENT = re.compile(
    r"보증|약속|확약|무상|교환|환불|보상|할인|리드\s*타임|납기\s*\d|\d\s*(?:원|달러|USD|EUR)|[$€]\s*\d|guarantee|warrant|refund",
    re.IGNORECASE,
)
_INTERNAL = re.compile(r"[\w\-]+\.(?:pdf|ya?ml|jsonl?)\b|\bparts/|\bcase:[\w\-]+|부품\s*카드", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\{\{?[a-z_]+\}?\}|TODO|XXX", re.IGNORECASE)


def _numbers(text: str) -> list[float]:
    values = []
    for token in _NUMBER.findall(text):
        try:
            values.append(float(token.replace(",", "")))
        except ValueError:
            continue
    return values


def _contains(values: list[float], number: float) -> bool:
    return any(math.isclose(number, other, rel_tol=1e-6, abs_tol=1e-12) for other in values)


def check_document(text: str, source: str, allowed: str = "") -> list[str]:
    """문서(text)가 승인된 내용(source)과 허용 문구(allowed: 티켓 번호, 날짜 같은 머리말)만 담았는지 본다. 문제 목록을 돌려준다."""
    issues: list[str] = []
    known = _numbers(source) + _numbers(allowed)
    unsupported = []
    for number in _numbers(text):
        if not _contains(known, number) and f"{number:g}" not in unsupported:
            unsupported.append(f"{number:g}")
    if unsupported:
        issues.append(f"승인된 내용에 없는 숫자: {', '.join(unsupported[:8])}")
    new_parts = sorted(set(extract_parts(text)) - set(extract_parts(source)) - set(extract_parts(allowed)))
    if new_parts:
        issues.append(f"승인된 내용에 없는 부품 번호: {', '.join(new_parts)}")
    for label, pattern in (("약속·가격·납기 표현", _COMMITMENT), ("내부 파일·카드 표현", _INTERNAL)):
        added = sorted({m.group(0).strip() for m in pattern.finditer(text)} - {m.group(0).strip() for m in pattern.finditer(source)})
        if added:
            issues.append(f"승인된 내용에 없는 {label}: {', '.join(added[:6])}")
    leftovers = sorted({m.group(0) for m in _PLACEHOLDER.finditer(text)} - {m.group(0) for m in _PLACEHOLDER.finditer(source)})
    if leftovers:
        issues.append(f"채우지 않은 자리표시: {', '.join(leftovers[:6])}")
    return issues
