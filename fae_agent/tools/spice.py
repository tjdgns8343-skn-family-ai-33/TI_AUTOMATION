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

# buck_switch: 고객이 주지 않는 값의 기본값(부품 카드 값이 아니라 일반적인 값. 결과의 assumed에 그대로 적는다)
BUCK_DEFAULTS = {"rds_ohm": 0.1, "dcr_ohm": 0.02, "vd_v": 0.4}  # 스위치 저항, 인덕터 직류 저항, 캐치 다이오드 순방향 전압
BUCK_REQUIRED = ("vin_v", "vout_v", "iout_a", "l_h", "fsw_hz", "cout_f", "esr_ohm")
BUCK_MEAS_CYCLES = 10  # 정상 상태에서 재는 마지막 스위칭 주기 수 (화면에 그리는 파형도 이 구간)
BUCK_STEPS_PER_CYCLE = 50  # 한 주기당 최대 시간 간격 수
BUCK_CYCLE_LIMITS = (150, 2500)  # 시뮬레이션 길이(주기 수)의 하한·상한
BUCK_CURRENT_TOLERANCE = 0.005  # 인덕터 평균 전류가 부하 전류에서 벗어나도 되는 비율 (듀티 탐색 종료 기준)
BUCK_VOUT_TOLERANCE = 0.01  # 마지막 실행에서 평균 출력이 목표에서 벗어나도 되는 비율 (넘으면 수렴하지 않은 것으로 표시)
BUCK_MAX_RUNS = 8  # 듀티 탐색의 ngspice 실행 횟수 상한
BUCK_PIN_OHM = 1e-4  # 출력을 목표 전압 전원에 묶는 저항

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
    merged = {**CIRCUIT_DEFAULTS.get(circuit, {}), **params}
    values = {k: v if isinstance(v, str) else f"{float(v):.6g}" for k, v in merged.items()}  # 문자열은 그대로 넣는다
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


def _buck_inputs(params: dict[str, float]) -> dict[str, float]:
    """buck_switch 입력을 검사하고 기본값을 채웁니다. 잘못된 값이면 ValueError (검수가 테스트 오류로 되돌린다)."""
    missing = [k for k in BUCK_REQUIRED if k not in params]
    if missing:
        raise ValueError(f"buck_switch에 필요한 값이 없습니다: {', '.join(missing)}")
    p = {**BUCK_DEFAULTS, **{k: float(v) for k, v in params.items()}}
    for key in ("vin_v", "vout_v", "iout_a", "l_h", "fsw_hz", "cout_f"):
        if p[key] <= 0:
            raise ValueError(f"{key}는 0보다 커야 합니다 (받은 값 {p[key]:g}).")
    if p["vout_v"] >= p["vin_v"]:
        raise ValueError("벅 컨버터는 입력 전압이 출력 전압보다 커야 합니다.")
    for key in ("esr_ohm", "rds_ohm", "dcr_ohm"):
        p[key] = max(p[key], 1e-6)  # ngspice는 0 Ω 저항을 받지 않는다
    p["vd_v"] = max(p["vd_v"], 0.01)
    return p


def _buck_initial_duty(p: dict[str, float]) -> float:
    """듀티의 첫 추정. 연속 전도(CCM)는 손실을 넣은 식, 부하가 가벼워 불연속(DCM)이면 DCM 식을 쓴다."""
    vin, vout, iout, vd = p["vin_v"], p["vout_v"], p["iout_a"], p["vd_v"]
    duty = (vout + iout * p["dcr_ohm"] + vd) / (vin - iout * p["rds_ohm"] + vd)
    ripple_a = (vout + vd) * (1 - duty) / (p["l_h"] * p["fsw_hz"])
    if iout < ripple_a / 2:
        duty = math.sqrt(2 * p["l_h"] * p["fsw_hz"] * iout * (vout + vd) / ((vin - vout) * (vin + vd)))
    return min(max(duty, 0.01), 0.99)


def _buck_cycles(p: dict[str, float], duty: float, pinned: bool) -> int:
    """시뮬레이션 길이(주기 수): 정착 속도의 7배 시간. 출력 고정이면 인덕터 전류(L/R), 아니면 출력 필터의 감쇠."""
    r_series = p["rds_ohm"] * duty + p["dcr_ohm"]
    if pinned:
        rate = r_series / p["l_h"]
    else:
        rate = 1 / (2 * (p["vout_v"] / p["iout_a"]) * p["cout_f"]) + (r_series + p["esr_ohm"]) / (2 * p["l_h"])
    low, high = BUCK_CYCLE_LIMITS
    return min(max(math.ceil(7 * p["fsw_hz"] / rate), low), high)


def _buck_run_once(p: dict[str, float], duty: float, cycles: int, pinned: bool, il0_a: float | None = None) -> dict[str, Any]:
    """한 번 실행. pinned면 출력 전압을 전원으로 고정해(무한대 커패시터) 인덕터 전류만 빠르게 정착시킨다."""
    period = 1 / p["fsw_hz"]
    ripple_est = (p["vout_v"] + p["vd_v"]) * (1 - duty) / (p["l_h"] * p["fsw_hz"])
    tstop = cycles * period
    pin_lines = [f"Vp vp 0 DC {p['vout_v']:.6g}", f"Rpin out vp {BUCK_PIN_OHM:g}"] if pinned else ["* 출력 고정 없음"]
    values = {
        **p,
        "ton_s": duty * period,
        "period_s": period,
        "step_s": period / BUCK_STEPS_PER_CYCLE,
        "tstart_s": tstop - BUCK_MEAS_CYCLES * period,
        "tstop_s": tstop,
        "rload_ohm": p["vout_v"] / p["iout_a"],
        "pin_block": "\n".join(pin_lines),
        "il0_a": max(p["iout_a"] - ripple_est / 2, 0.0) if il0_a is None else il0_a,  # 정상 상태의 최소 전류
    }
    return run_netlist(render_circuit("buck_switch", values))


def _buck_next_duty(p: dict[str, float], history: list[tuple[float, float, float, bool]]) -> float:
    """다음 듀티. 기준은 인덕터 평균 전류 − 부하 전류 (출력을 고정했으므로 0이면 출력 커패시터 전하가 균형이다)."""
    duty, error, il_avg, dcm = history[-1]
    if len(history) >= 2 and history[-1][1] != history[-2][1]:
        (d1, e1), (d2, e2) = history[-2][:2], history[-1][:2]
        nxt = d2 - e2 * (d2 - d1) / (e2 - e1)
    elif dcm:
        nxt = duty * math.sqrt(p["iout_a"] / max(il_avg, 1e-9))  # 불연속 모드의 평균 전류는 듀티의 제곱에 비례
    else:  # 연속 모드: 듀티 1당 평균 전류는 (입력 전압 + 다이오드 전압) / 직렬 저항만큼 변한다
        nxt = duty - error * (p["rds_ohm"] * duty + p["dcr_ohm"]) / (p["vin_v"] + p["vd_v"])
    return min(max(nxt, 0.01), 0.99)


def run_buck_switch(params: dict[str, float]) -> dict[str, Any]:
    """벅 컨버터(비동기, 개루프)의 스위칭 동작을 시뮬레이션해 정상 상태의 출력 리플과 인덕터 전류를 구합니다.

    1단계: 출력 전압을 목표값으로 고정하고 인덕터 평균 전류가 부하 전류와 같아지는 듀티를 찾는다 (ngspice 여러 번).
    2단계: 그 듀티로 출력 커패시터와 부하를 넣어 한 번 더 돌려 출력 리플을 잰다. 부하가 가벼우면 불연속 전도(DCM)가
    되고 연속 전도 계산식(calc.buck_ripple_estimate)이 맞지 않는다. 제어 루프와 Eco-mode는 모델에 없다.
    """
    p = _buck_inputs(params)
    duty = _buck_initial_duty(p)
    history: list[tuple[float, float, float, bool]] = []  # (듀티, 평균 전류 − 부하 전류, 평균 전류, 불연속 여부)
    pinned: dict[str, Any] = {}
    runs = 0
    for _ in range(BUCK_MAX_RUNS):
        pinned = _buck_run_once(p, duty, _buck_cycles(p, duty, True), pinned=True)
        runs += 1
        if not pinned.get("ok"):
            pinned["circuit"] = "buck_switch"
            return pinned
        m = pinned["measures"]
        history.append((duty, m["il_avg"] - p["iout_a"], m["il_avg"], m["il_min"] <= 0.01 * p["iout_a"]))
        if abs(history[-1][1]) <= BUCK_CURRENT_TOLERANCE * p["iout_a"]:
            break
        duty = _buck_next_duty(p, history)
    duty, current_error = history[-1][0], history[-1][1]
    result = _buck_run_once(
        p, duty, _buck_cycles(p, duty, False), pinned=False, il0_a=max(pinned["measures"]["il_min"], 0.0)
    )
    runs += 1
    if not result.get("ok"):
        result["circuit"] = "buck_switch"
        return result
    m = result["measures"]
    ripple = m["vout_max"] - m["vout_min"]
    delta_il = m["il_max"] - m["il_min"]
    vout_error = (m["vout_avg"] - p["vout_v"]) / p["vout_v"]
    result.update(
        {
            "circuit": "buck_switch",
            "duty": duty,
            "on_time_s": duty / p["fsw_hz"],
            "vout_avg_v": m["vout_avg"],
            "vout_error_pct": vout_error * 100,
            "converged": abs(current_error) <= BUCK_CURRENT_TOLERANCE * p["iout_a"]
            and abs(vout_error) <= BUCK_VOUT_TOLERANCE,
            "runs": runs,
            "ripple_v": ripple,
            "ripple_pct": ripple / p["vout_v"] * 100,
            "delta_il_a": delta_il,
            "il_min_a": m["il_min"],
            "il_max_a": m["il_max"],
            "ripple_ratio": delta_il / p["iout_a"],
            "dcm": int(m["il_min"] <= 0.01 * p["iout_a"]),  # 1이면 인덕터 전류가 0까지 내려가는 불연속 전도
            "assumed": {k: p[k] for k in BUCK_DEFAULTS if k not in params},
        }
    )
    waveform = result.get("waveform")
    if waveform and waveform["time_us"]:
        start = waveform["time_us"][0]
        waveform["time_us"] = [round(t - start, 4) for t in waveform["time_us"]]
        waveform["ref_v"] = p["vout_v"]
    return result


def run_circuit(circuit: str, params: dict[str, float]) -> dict[str, Any]:
    """회로 템플릿 하나를 실행합니다. 결과에 .meas 값과 언더슈트·오버슈트(%) 또는 연산증폭기 이득·대역폭을 담습니다."""
    if circuit == "buck_switch":
        return run_buck_switch(params)
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
    """같은 회로를 변경 전·후 값으로 돌려 언더슈트(buck_switch는 출력 리플) 개선율(improvement_pct)을 비교합니다."""
    first = run_circuit(circuit, before)
    if first.get("skipped"):
        return {"skipped": True, "error": first.get("error"), "circuit": circuit}
    second = run_circuit(circuit, after)
    result: dict[str, Any] = {"skipped": False, "circuit": circuit, "before": first, "after": second}
    metric, pct = ("ripple", "after_ripple_pct") if circuit == "buck_switch" else ("undershoot", "after_undershoot_pct")
    b, a = first.get(f"{metric}_v"), second.get(f"{metric}_v")
    if b and a is not None:
        result["improvement_pct"] = (b - a) / b * 100
        result[pct] = second.get(f"{metric}_pct")
    return result


SPICE_TOOLS = {fn.__name__: fn for fn in (run_netlist, run_circuit, compare_circuit)}
