"""고객 답변 초안의 내부 표현 점검.

답변에 내부 파일 이름(tps54331.pdf, parts/X.yaml), 사례 번호(case:C-…), '부품 카드' 같은 말이 섞이면 고객은 뜻을 모른다
(평가 M-1~M-3에서 '카드 기준', '부품 카드', 'tps54331.pdf'가 실제로 새어 나갔다). 프롬프트로 막아도 모델이 가끔 어기므로
코드가 마지막에 검사해 고객이 아는 말로 바꾸고, 바꾼 내용은 사람 검토자가 볼 수 있게 알림으로 돌려준다.
내부 필드 이름(il_max_a 같은 snake_case)은 뜻을 알 수 없어 바꾸지 못하니 알림만 한다.
"""

from __future__ import annotations

import re
from collections.abc import Callable

MAX_LISTED = 5  # 알림에 적는 예시 개수
# 한국어 조사가 바로 붙는 경우(tps54331.pdf를)가 있어 단어 경계(\b) 대신 영숫자 경계로 본다
_B = r"(?<![A-Za-z0-9_\-])"
_E = r"(?![A-Za-z0-9_])"


def _part_name(match: re.Match[str]) -> str:
    name = match.group(1)
    if any(ch.isdigit() for ch in name):  # 부품 번호처럼 보이는 파일 이름(tps54331)만 대문자로
        name = name.upper()
    return f"{name} 데이터시트"


# (패턴, 바꿀 말). 긴 표현을 먼저 둔다
_RULES: list[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]] = [
    # 'parts/X.yaml abs_max.vin_v'처럼 카드 안의 항목 경로가 붙은 근거 표기도 함께 지운다
    (re.compile(rf"{_B}parts/([A-Za-z0-9\-]+)\.ya?ml(?: (?:abs_max|recommended|typical|thermal)\.[a-z0-9_]+)?{_E}"), _part_name),
    (re.compile(rf"{_B}(?:data/raw/docs/|docs/)?([A-Za-z0-9\-]+)\.pdf{_E}"), _part_name),
    (re.compile(rf"{_B}[A-Za-z0-9\-]+_notes(?:_[A-Za-z0-9\-]+)?\.md{_E}"), "설계 노트"),
    (re.compile(rf"{_B}case:([A-Za-z0-9\-]+)"), r"과거 사례 \1"),
    (re.compile(r"부품\s*카드"), "데이터시트"),
    # '카드'만 쓴 경우는 뜻이 분명한 꼴(카드 기준, 카드 값, 카드의 전형값 …)만 바꾼다. '그래픽 카드는' 같은 말은 건드리지 않는다
    (re.compile(r"(?<![가-힣A-Za-z0-9])카드(?=\s?기준|\s?값|의\s?(?:전형|최소|최대|한계|값|스펙|수치))"), "데이터시트"),
]
_FIELD_NAME = re.compile(r"(?<![A-Za-z0-9_])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![A-Za-z0-9_])")  # tj_c, il_max_a


def scrub_reply(text: str) -> tuple[str, list[str]]:
    """(고친 답변, 사람이 확인할 알림 목록). 바꿀 것도 남은 것도 없으면 (원문, [])."""
    changes: list[str] = []
    for pattern, replacement in _RULES:

        def swap(match: re.Match[str], replacement=replacement) -> str:
            new = replacement(match) if callable(replacement) else match.expand(replacement)
            changes.append(f"'{match.group(0)}' → '{new}'")
            return new

        text = pattern.sub(swap, text)
    notices: list[str] = []
    if changes:
        listed = list(dict.fromkeys(changes))
        extra = f" 외 {len(listed) - MAX_LISTED}종" if len(listed) > MAX_LISTED else ""
        notices.append(f"답변 초안의 내부 표현 {len(changes)}곳을 고쳤습니다: {', '.join(listed[:MAX_LISTED])}{extra}")
    names = list(dict.fromkeys(_FIELD_NAME.findall(text)))
    if names:
        notices.append(
            f"답변 초안에 내부 필드 이름으로 보이는 표현이 남아 있습니다 (고객이 뜻을 모를 수 있음): {', '.join(names[:MAX_LISTED])}"
        )
    return text, notices
