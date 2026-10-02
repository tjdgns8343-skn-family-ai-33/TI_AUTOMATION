"""부품 카드 YAML: 숫자가 문자열로 읽히지 않는지 확인한다.

PyYAML은 지수에 부호가 없는 `1.0e6`을 숫자가 아니라 문자열로 읽는다 (`1.0e+6`이나 `1.0e-6`은 숫자).
문자열이면 프롬프트에 따옴표 숫자로 들어가고, 코드가 float()로 감싸지 않으면 계산이 깨진다.
"""

import re
from pathlib import Path

import yaml

PARTS = Path(__file__).resolve().parent.parent / "data" / "parts"
NUMBER_LIKE = re.compile(r"[-+]?\d+(\.\d+)?[eE][-+]?\d+")


def _strings(node, path=""):
    for key, value in node.items():
        here = f"{path}.{key}" if path else str(key)
        if isinstance(value, dict):
            yield from _strings(value, here)
        elif isinstance(value, str) and NUMBER_LIKE.fullmatch(value):
            yield here, value


def test_no_number_in_a_part_card_is_read_as_a_string():
    problems = []
    for path in sorted(PARTS.glob("*.y*ml")):
        card = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        problems += [f"{path.name}: {where} = {value!r} (지수에 부호가 필요: 1.0e+6)" for where, value in _strings(card)]
    assert not problems, "\n".join(problems)
