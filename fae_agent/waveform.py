"""테스트 결과에 담긴 시뮬레이션 파형·주파수 응답을 꺼내거나 빼는 보조 함수 (도구 호출 없음)."""

from __future__ import annotations

from typing import Any

# 긴 숫자 목록이 들어 있는 키: 화면에는 그리지만 AI 프롬프트에는 넣지 않는다
BULKY_KEYS = ("waveform", "freq_response")

_NODES = (("결과", None), ("변경 전", "before"), ("변경 후", "after"))


def _extract(outputs: dict[str, Any], key: str, valid_axis: str) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    for name, child in _NODES:
        node = outputs if child is None else outputs.get(child)
        item = node.get(key) if isinstance(node, dict) else None
        if isinstance(item, dict) and item.get(valid_axis):
            found.append((name, item))
    return found


def extract_waveforms(outputs: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """테스트 결과(outputs)에서 시간 영역 파형을 찾아 (이름, 파형) 목록으로 돌려줍니다.

    run_circuit은 `waveform` 하나, compare_circuit은 `before`·`after` 안에 각각 하나씩 담깁니다.
    """
    return _extract(outputs, "waveform", "time_us")


def extract_responses(outputs: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """테스트 결과(outputs)에서 주파수 응답(freq_hz, gain_db)을 찾아 (이름, 응답) 목록으로 돌려줍니다."""
    return _extract(outputs, "freq_response", "freq_hz")


def strip_waveforms(data: Any) -> Any:
    """AI 프롬프트에 넣기 전에 파형·주파수 응답(긴 숫자 목록)을 뺀 사본을 돌려줍니다."""
    if isinstance(data, dict):
        return {k: strip_waveforms(v) for k, v in data.items() if k not in BULKY_KEYS}
    if isinstance(data, list):
        return [strip_waveforms(v) for v in data]
    return data
