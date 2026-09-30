"""고객이 알려 준 조건을 부품 카드의 한계와 수치로 비교합니다 (검수와 품질 클레임 정격 점검이 함께 씁니다)."""

from __future__ import annotations

from typing import Any

from .schemas import IntakeResult

# (조건 키, 이름, 단위, 절대 최대 키, 권장 최대 키, 권장 최소 키). 연산증폭기 카드는 vs_* 키를 쓴다.
RATING_SPECS: tuple[tuple[str, str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
    ("vin_v", "입력 전압", "V", ("vin_v", "vs_v"), ("vin_max_v", "vs_max_v"), ("vin_min_v", "vs_min_v")),
    ("vdd_v", "전원 전압(VDD)", "V", ("vdd_v", "vs_v"), ("vdd_max_v", "vs_max_v"), ("vdd_min_v", "vs_min_v")),
    ("iout_a", "부하 전류", "A", (), ("iout_max_a",), ()),
    ("cout_f", "출력 커패시터", "F", (), ("cout_max_f",), ("cout_min_f",)),
    ("esr_ohm", "출력 커패시터 ESR", "Ω", (), ("esr_max_ohm",), ()),
)


def _limit(section: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if section.get(key) is not None:
            return float(section[key])
    return None


def rating_facts(intake: IntakeResult, card: dict[str, Any]) -> dict[str, list[str]]:
    """고객 조건을 부품 카드의 한계와 수치로 비교합니다.

    abs_over: 절대 최대를 넘음 / rec_over: 권장 범위를 벗어남 / within: 한계 안임을 확인한 항목.
    부품 카드에 한계가 없는 조건은 판단할 수 없으므로 어느 쪽에도 넣지 않습니다.
    """
    abs_max, recommended = card.get("abs_max") or {}, card.get("recommended") or {}
    conditions = intake.conditions.known()
    facts: dict[str, list[str]] = {"abs_over": [], "rec_over": [], "within": []}
    for key, name, unit, abs_keys, high_keys, low_keys in RATING_SPECS:
        value = conditions.get(key)
        abs_high, rec_high, rec_low = _limit(abs_max, abs_keys), _limit(recommended, high_keys), _limit(recommended, low_keys)
        if value is None or (abs_high, rec_high, rec_low) == (None, None, None):
            continue
        if abs_high is not None and value > abs_high:
            note = f"(권장 최대 {rec_high:g} {unit})" if rec_high is not None else ""
            facts["abs_over"].append(f"{name} {value:g} {unit}가 절대 최대 정격 {abs_high:g} {unit}{note}를 넘습니다")
        elif rec_high is not None and value > rec_high:
            facts["rec_over"].append(f"{name} {value:g} {unit}가 권장 최대 {rec_high:g} {unit}를 넘습니다")
        elif rec_low is not None and value < rec_low:
            facts["rec_over"].append(f"{name} {value:g} {unit}가 권장 최소 {rec_low:g} {unit}보다 작습니다")
        else:
            facts["within"].append(name)
    return facts
