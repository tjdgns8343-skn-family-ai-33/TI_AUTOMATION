"""ngspice 배치 실행 도구.

circuits/<이름>.cir 템플릿의 $변수를 채워 ngspice로 돌리고, .meas 결과를 읽어 옵니다.
Windows는 콘솔용 ngspice_con.exe를 쓰세요 (.env의 NGSPICE_PATH 또는 PATH).
TI의 암호화 모델은 PSpice for TI·TINA-TI 전용이라 여기서는 비암호화 모델만 쓸 수 있습니다.
"""

from __future__ import annotations

import bisect
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from string import Template
from typing import Any

from ..config import get_settings

WAVE_FILE = "wave.txt"  # 회로 템플릿의 `wrdata wave.txt ...`이 만드는 파일 (시간·전압, 또는 주파수·dB 두 열)
WAVE_POINTS = 500  # 화면·상태에 싣는 파형 점 개수 (균일한 시간 간격으로 다시 뽑는다)
RESPONSE_POINTS = 160  # 주파수 응답 점 개수 (로그 간격으로 다시 뽑는다)

# 회로 템플릿에서 값을 주지 않으면 쓰는 기본값
CIRCUIT_DEFAULTS: dict[str, dict[str, float]] = {"opamp_ac": {"a0": 1e5}}  # 개방 이득 100 dB

_AC_DECL = re.compile(r"^\s*\.ac\b", re.IGNORECASE | re.MULTILINE)
_MEAS_DECL = re.compile(r"^\s*\.meas(?:ure)?\s+\w+\s+(\w+)", re.IGNORECASE | re.MULTILINE)
_MEAS_VALUE = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)", re.MULTILINE)


def find_ngspice() -> str | None:
    """ngspice 실행 파일 경로를 찾습니다. 없으면 None."""
    configured = get_settings().ngspice_path.strip()
    if configured:
        if Path(configured).exists():
            return configured
        return shutil.which(configured)
    for name in ("ngspice_con", "ngspice"):
        found = shutil.which(name)
        if found:
            return found
    return None


def parse_measures(output: str, names: list[str] | None = None) -> dict[str, float]:
    """ngspice 출력에서 `이름 = 값` 형태의 .meas 결과를 읽습니다."""
    wanted = {n.lower() for n in names} if names else None
    measures: dict[str, float] = {}
    for name, value in _MEAS_VALUE.findall(output):
        key = name.lower()
        if wanted is None or key in wanted:
            measures[key] = float(value)
    return measures


def parse_wrdata(text: str) -> tuple[list[float], list[float]]:
    """ngspice `wrdata`가 만든 텍스트(시간, 값 두 열)를 읽습니다. 읽을 수 없는 줄은 건너뜁니다."""
    times: list[float] = []
    values: list[float] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            time, value = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        times.append(time)
        values.append(value)
    return times, values


def resample_waveform(times: list[float], values: list[float], points: int = WAVE_POINTS) -> dict[str, list[float]]:
    """ngspice의 가변 간격 결과를 균일한 시간 간격으로 다시 뽑습니다 (수정 전·후 파형을 같은 축에 겹치기 위해).

    시간은 µs, 값은 V. 점이 2개 미만이면 빈 파형을 돌려줍니다.
    """
    if len(times) < 2 or times[-1] <= times[0]:
        return {"time_us": [], "v": []}
    start, stop = times[0], times[-1]
    grid, out = [], []
    for i in range(points):
        t = start + (stop - start) * i / (points - 1)
        j = min(max(bisect.bisect_right(times, t) - 1, 0), len(times) - 2)
        span = times[j + 1] - times[j]
        frac = 0.0 if span <= 0 else min(max((t - times[j]) / span, 0.0), 1.0)
        grid.append(round(t * 1e6, 4))
        out.append(round(values[j] + (values[j + 1] - values[j]) * frac, 5))
    return {"time_us": grid, "v": out}


def resample_response(
    freqs: list[float],
    values: list[float],
    points: int = RESPONSE_POINTS,
    f_min: float | None = None,
    f_max: float | None = None,
) -> dict[str, list[float]]:
    """주파수 응답(주파수 Hz, 이득 dB)을 로그 간격 주파수로 다시 뽑습니다.

    f_min~f_max 범위만 남깁니다 (없으면 전체). 점이 2개 미만이거나 범위가 비면 빈 응답을 돌려줍니다.
    """
    pairs = [(f, v) for f, v in zip(freqs, values, strict=False) if f > 0]
    if len(pairs) < 2:
        return {"freq_hz": [], "gain_db": []}
    lo = max(f_min or pairs[0][0], pairs[0][0])
    hi = min(f_max or pairs[-1][0], pairs[-1][0])
    if hi <= lo:
        return {"freq_hz": [], "gain_db": []}
    logs = [math.log10(f) for f, _ in pairs]
    grid, out = [], []
    for i in range(points):
        x = math.log10(lo) + (math.log10(hi) - math.log10(lo)) * i / (points - 1)
        j = min(max(bisect.bisect_right(logs, x) - 1, 0), len(pairs) - 2)
        span = logs[j + 1] - logs[j]
        frac = 0.0 if span <= 0 else min(max((x - logs[j]) / span, 0.0), 1.0)
        grid.append(float(f"{10**x:.5g}"))
        out.append(round(pairs[j][1] + (pairs[j + 1][1] - pairs[j][1]) * frac, 4))
    return {"freq_hz": grid, "gain_db": out}


def run_netlist(netlist: str, timeout_s: int = 60) -> dict[str, Any]:
    """넷리스트 문자열을 ngspice -b로 실행하고 .meas 결과를 돌려줍니다.

    넷리스트가 `wrdata wave.txt ...`로 결과를 저장하면 함께 담습니다: 시간 영역(.tran 등)은 `waveform`(time_us, v),
    주파수 영역(.ac)은 `freq_response`(freq_hz, gain_db). 주파수 응답은 측정한 대역폭(bw_hz) 앞뒤 3자리 범위만 남깁니다.
    """
    exe = find_ngspice()
    if exe is None:
        return {
            "ok": False,
            "skipped": True,
            "measures": {},
            "error": "ngspice를 찾지 못했습니다. 설치 후 PATH에 추가하거나 .env의 NGSPICE_PATH를 지정하세요.",
        }
    names = _MEAS_DECL.findall(netlist)
    with tempfile.TemporaryDirectory() as tmp:
        cir = Path(tmp) / "circuit.cir"
        cir.write_text(netlist, encoding="utf-8")
        try:
            proc = subprocess.run(
                [exe, "-b", str(cir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                cwd=tmp,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "skipped": False, "measures": {}, "error": f"{timeout_s}초 안에 끝나지 않았습니다."}
        wave_path = Path(tmp) / WAVE_FILE
        wave_text = wave_path.read_text(encoding="utf-8", errors="replace") if wave_path.exists() else ""
    output = f"{proc.stdout}\n{proc.stderr}"
    measures = parse_measures(output, names)
    missing = [n for n in names if n.lower() not in measures]
    ok = proc.returncode == 0 and not missing
    result: dict[str, Any] = {
        "ok": ok,
        "skipped": False,
        "measures": measures,
        "returncode": proc.returncode,
        "error": None if ok else f"실패 또는 측정값 없음: {missing or proc.returncode}",
        "log_tail": output[-1200:],
    }
    if wave_text and _AC_DECL.search(netlist):
        bw = measures.get("bw_hz")
        window = {"f_min": bw / 1e3, "f_max": bw * 1e3} if bw and bw > 0 else {}
        response = resample_response(*parse_wrdata(wave_text), **window)
        if response["freq_hz"]:
            result["freq_response"] = response
    elif wave_text:
        waveform = resample_waveform(*parse_wrdata(wave_text))
        if waveform["time_us"]:
            result["waveform"] = waveform
    return result


def render_circuit(circuit: str, params: dict[str, float]) -> str:
    """circuits/<circuit>.cir 템플릿의 $변수를 채운 넷리스트를 만듭니다."""
    path = get_settings().circuits_dir / f"{circuit}.cir"
    if not path.exists():
        raise FileNotFoundError(f"회로 템플릿이 없습니다: {path}")
    values = {k: f"{float(v):.6g}" for k, v in {**CIRCUIT_DEFAULTS.get(circuit, {}), **params}.items()}
    return Template(path.read_text(encoding="utf-8")).substitute(values)


def _derived(params: dict[str, float], measures: dict[str, float]) -> dict[str, float]:
    """출력 전압 기준 언더슈트·오버슈트, 연산증폭기 주파수 응답의 선형 이득·이득 비율·출력 진폭을 계산합니다.

    연산증폭기 지표 이름은 calc.opamp_closed_loop_gain과 같다 (같은 기준으로 계산과 시뮬레이션을 대조하기 위해).
    """
    derived: dict[str, float] = {}
    if "dc_gain_db" in measures:
        derived["dc_gain"] = 10 ** (measures["dc_gain_db"] / 20)
        if "bw_hz" in measures:
            derived["bw_hz"] = measures["bw_hz"]  # 테스트의 expect는 최상위 지표 이름으로 찾는다 (measures 안이 아니라)
        if "gain_db_at_freq" in measures:
            derived["gain_at_freq"] = 10 ** (measures["gain_db_at_freq"] / 20)
            derived["gain_ratio"] = derived["gain_at_freq"] / derived["dc_gain"]
            if params.get("vin_pp"):
                derived["vout_pp"] = params["vin_pp"] * derived["gain_at_freq"]
    vout = params.get("vout_v")
    if vout:
        if "vmin" in measures:
            derived["undershoot_v"] = vout - measures["vmin"]
            derived["undershoot_pct"] = derived["undershoot_v"] / vout * 100
        if "vmax" in measures:
            derived["overshoot_v"] = measures["vmax"] - vout
            derived["overshoot_pct"] = derived["overshoot_v"] / vout * 100
    return derived


def run_circuit(circuit: str, params: dict[str, float]) -> dict[str, Any]:
    """회로 템플릿 하나를 실행합니다. 결과에 .meas 값과 언더슈트·오버슈트(%) 또는 연산증폭기 이득·대역폭을 담습니다."""
    result = run_netlist(render_circuit(circuit, params))
    result["circuit"] = circuit
    result.update(_derived(params, result.get("measures", {})))
    if result.get("waveform") and params.get("vout_v"):
        result["waveform"]["ref_v"] = params["vout_v"]  # 화면에 목표 출력 전압선을 그리기 위한 기준값
    if result.get("freq_response") and "dc_gain_db" in result.get("measures", {}):
        measures = result["measures"]
        result["freq_response"]["marks"] = {  # 화면에 읽은 주파수·−3 dB 대역폭·기준 이득을 표시하기 위한 값
            "freq_hz": params.get("freq_hz"),
            "bw_hz": measures.get("bw_hz"),
            "dc_gain_db": measures["dc_gain_db"],
            "gain_db_at_freq": measures.get("gain_db_at_freq"),
        }
    return result


def compare_circuit(circuit: str, before: dict[str, float], after: dict[str, float]) -> dict[str, Any]:
    """같은 회로를 변경 전·후 값으로 돌려 언더슈트 개선율(improvement_pct)을 비교합니다."""
    first = run_circuit(circuit, before)
    if first.get("skipped"):
        return {"skipped": True, "error": first.get("error"), "circuit": circuit}
    second = run_circuit(circuit, after)
    result: dict[str, Any] = {"skipped": False, "circuit": circuit, "before": first, "after": second}
    b, a = first.get("undershoot_v"), second.get("undershoot_v")
    if b and a is not None:
        result["improvement_pct"] = (b - a) / b * 100
        result["after_undershoot_pct"] = second.get("undershoot_pct")
    return result


SPICE_TOOLS = {fn.__name__: fn for fn in (run_netlist, run_circuit, compare_circuit)}
