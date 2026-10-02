"""테스트 에이전트: 해결책의 테스트 계획을 계산·시뮬레이션 도구로 실행하고 기대 범위와 비교합니다."""

from __future__ import annotations

import inspect
from typing import Any

from ..schemas import CheckReport, CheckStep, Solution, StepResult
from ..tools.calc import CALC_TOOLS
from ..tools.gateway import ToolGateway
from ..tools.spice import SPICE_TOOLS


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, name + "."))
        else:
            flat[name] = value
    return flat


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    """상태에 저장할 때 긴 로그는 뺍니다."""
    return {k: (_compact(v) if isinstance(v, dict) else v) for k, v in data.items() if k != "log_tail"}


def evaluate(step: CheckStep, outputs: dict[str, Any]) -> tuple[str, str]:
    """기대 범위(expect)와 비교해 (상태, 설명)을 돌려줍니다."""
    flat = _flatten(outputs)
    problems, found = [], []
    for bound in step.expect:
        value = flat.get(bound.metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "error", f"결과에 '{bound.metric}' 지표가 없습니다."
        found.append(f"{bound.metric}={value:.4g}")
        if bound.min is not None and value < bound.min:
            problems.append(f"{bound.metric}={value:.4g} < {bound.min:.4g}")
        if bound.max is not None and value > bound.max:
            problems.append(f"{bound.metric}={value:.4g} > {bound.max:.4g}")
    if problems:
        return "fail", "; ".join(problems)
    return "pass", ", ".join(found)


def run_checks(solution: Solution | None, gateway: ToolGateway) -> CheckReport:
    steps = solution.test_plan if solution else []
    if not steps:
        return CheckReport(status="skipped", steps=[])
    results: list[StepResult] = []
    for step in steps:
        common = {"label": step.label, "purpose": step.purpose, "tool": step.tool}
        try:
            outputs = gateway.call(step.tool, **step.args())
        except Exception as exc:  # 잘못된 인자·계산 오류는 테스트 오류로 기록 (검수가 되돌려 보냄)
            results.append(StepResult(**common, status="error", notes=f"{type(exc).__name__}: {exc}"))
            continue
        if outputs.get("skipped"):
            results.append(
                StepResult(**common, status="skipped", outputs=_compact(outputs), notes=str(outputs.get("error", "")))
            )
        elif not step.expect:
            results.append(StepResult(**common, status="pass", outputs=_compact(outputs), notes="참고 계산"))
        else:
            status, notes = evaluate(step, outputs)
            results.append(StepResult(**common, status=status, outputs=_compact(outputs), notes=notes))
    statuses = {r.status for r in results}
    if statuses & {"fail", "error"}:
        overall = "fail"
    elif statuses == {"skipped"}:
        overall = "skipped"
    elif "skipped" in statuses:
        overall = "partial"
    else:
        overall = "pass"
    return CheckReport(status=overall, steps=results)


# calc 도구가 돌려주는 지표 이름. LLM이 expect에 지표 이름을 지어내지 않도록 목록에 함께 알려 준다.
CALC_METRICS: dict[str, str] = {
    "buck_output_voltage": "vout_v",
    "buck_ripple_estimate": (
        "duty, delta_il_a, ripple_cap_v, ripple_esr_v, ripple_v, ripple_pct, on_time_s, "
        "ripple_ratio·iout_crit_a·il_max_a(인덕터 최대 전류, 전류 제한과 비교)·dcm(iout_a>0일 때, dcm=1이면 불연속 모드라 식이 맞지 않음)"
    ),
    "lc_resonance": "f0_hz, z0_ohm",
    "thermal_estimate": "ploss_w, tj_c",
    "ldo_thermal_estimate": "pdiss_w, tj_c",
    "startup_inrush": "i_inrush_a, i_total_a",
    "i2c_pullup_check": "r_min_ohm, r_max_ohm, pullup_ohm, weak_ratio, strong_ratio",
    "noninverting_gain": "gain",
    "opamp_closed_loop_gain": (
        "dc_gain, bw_hz, gain_at_freq, gain_ratio, vout_pp(vin_pp>0일 때), slew_needed_v_per_us(vin_pp>0일 때), "
        "slew_ratio(vin_pp>0, slew_rate_v_per_us>0일 때)"
    ),
    "rc_cutoff": "fc_hz",
    "check_value": "value (검사한 값 그대로. 범위는 expect의 min/max로 비교하고 in_range 같은 지표는 없음)",
}


def describe_test_tools() -> str:
    """LLM에게 알려 줄 테스트 도구 목록 (결과 지표 이름 포함)."""
    lines = []
    for server, tools in (("calc", CALC_TOOLS), ("spice", SPICE_TOOLS)):
        for name, fn in tools.items():
            if name == "run_netlist":
                continue
            signature = str(inspect.signature(fn)).replace("'", "")
            doc = (inspect.getdoc(fn) or "").splitlines()[0]
            metrics = f" → 지표 {CALC_METRICS[name]}" if server == "calc" and name in CALC_METRICS else ""
            lines.append(f"- {server}.{name}{signature}: {doc}{metrics}")
    lines.append(
        "- spice 회로 템플릿 lc_load_step: params = vout_v, l_h, c_f, esr_ohm, istep_a, rbase_ohm "
        "→ 지표 undershoot_pct (run_circuit), improvement_pct·after_undershoot_pct (compare_circuit)"
    )
    lines.append(
        "- spice 회로 템플릿 opamp_ac (연산증폭기 주파수 응답, 비반전 증폭기 + 1차 모델): "
        "params = rf_ohm, rg_ohm, gbw_hz, freq_hz, vin_pp(선택), a0(선택, 개방 이득 선형값, 기본 1e5) "
        "→ 지표 dc_gain, bw_hz, gain_at_freq, gain_ratio, vout_pp(vin_pp가 있을 때). run_circuit으로만 쓴다 "
        "(compare_circuit은 언더슈트 전용). 지표 이름이 calc.opamp_closed_loop_gain과 같다"
    )
    lines.append(
        "- spice 회로 템플릿 buck_switch (벅 컨버터의 스위칭 동작, 비동기·개루프, 듀티는 자동으로 찾음): "
        "params = vin_v, vout_v, iout_a, l_h, fsw_hz, cout_f, esr_ohm 필수, rds_ohm·dcr_ohm·vd_v 선택 "
        "(기본 0.1 Ω·0.02 Ω·0.4 V) → 지표 duty, on_time_s, ripple_v, ripple_pct, delta_il_a, il_min_a, il_max_a, "
        "ripple_ratio, vout_avg_v, dcm(1이면 불연속). run_circuit으로만 쓴다. 지표 이름이 calc.buck_ripple_estimate와 같다. "
        "부하가 가벼우면 불연속이 되어 계산식과 다르다. 동기 벅(카드에 rdson_low_ohm)은 vd_v=iout_a×rdson_low_ohm, rds_ohm=rdson_high_ohm으로 지정한다"
    )
    return "\n".join(lines)
