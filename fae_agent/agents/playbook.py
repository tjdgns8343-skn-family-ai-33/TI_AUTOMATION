"""mock 모드용 규칙 기반 진단 플레이북.

증상 코드마다 후보(원인 가설 + 해결책 + 테스트 계획)를 가능성 순으로 만듭니다.
LLM 모드에서는 쓰지 않지만, LLM 결과와 비교하는 기준선(baseline)으로 쓸 수 있습니다.

테스트 계획의 purpose
- reproduce: 현재 조건으로 계산·시뮬레이션해 고객 증상이 재현되는지 (가설 확인)
- verify:    해결책을 적용한 값으로 목표를 만족하는지 (해결책 확인)
- info:      참고 계산
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..schemas import Bound, CheckStep, ComponentChange, Evidence, Hypothesis, Solution

# 부품 카드·고객 정보에 값이 없을 때 쓰는 가정값 (결과에 '가정'으로 표시)
ASSUMED = {
    "fsw_hz": 500e3,
    "esr_ohm": 0.005,
    "efficiency": 0.9,
    "theta_ja_c_per_w": 40.0,
    "tj_limit_c": 125.0,
    "ta_c": 25.0,
    "cb_f": 200e-12,
    "iout_a": 1.0,
    "vdd_v": 3.3,
}
E12 = (1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2)
LDO_HEADROOM_V = 0.5


@dataclass
class Candidate:
    hypothesis: Hypothesis
    solution: Solution
    keywords: list[str] = field(default_factory=list)  # 근거 문서 확인용 키워드 (영문 소문자)


class Ctx:
    """고객 조건과 부품 카드에서 값을 꺼내고, 가정값을 쓰면 기록합니다."""

    def __init__(self, conditions: dict[str, Any], card: dict[str, Any] | None):
        self.cond = {k: float(v) for k, v in conditions.items() if v is not None}
        self.card = card or {}
        self.assumptions: dict[str, str] = {}
        self.notes: list[str] = []

    @property
    def part_type(self) -> str:
        return str(self.card.get("type", "")).lower()

    def card_value(self, section: str, key: str) -> float | None:
        value = (self.card.get(section) or {}).get(key)
        return float(value) if value is not None else None

    def get(
        self,
        key: str,
        section: str | None = None,
        card_key: str | None = None,
        assumed: str | None = None,
        label: str = "",
    ) -> float | None:
        if key in self.cond:
            return self.cond[key]
        if section:
            value = self.card_value(section, card_key or key)
            if value is not None:
                return value
        if assumed:
            value = ASSUMED[assumed]
            self.assumptions[assumed] = f"{label or key} = {value:g} (가정)"
            return value
        return None

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


def _step(label: str, purpose: str, tool: str, args: dict[str, Any], *bounds: Bound) -> CheckStep:
    return CheckStep(label=label, purpose=purpose, tool=tool, args_json=json.dumps(args), expect=list(bounds))


def _eng(value: float, unit: str) -> str:
    """공학 표기: 2.2e-05, 'F' → '22 µF'."""
    if value == 0:
        return f"0 {unit}"
    for scale, prefix in (
        (1e9, "G"),
        (1e6, "M"),
        (1e3, "k"),
        (1, ""),
        (1e-3, "m"),
        (1e-6, "µ"),
        (1e-9, "n"),
        (1e-12, "p"),
    ):
        if abs(value) >= scale:
            return f"{value / scale:.3g} {prefix}{unit}"
    return f"{value:.3g} {unit}"


def _pick_e12(r_min: float, r_max: float) -> float | None:
    """허용 범위 안의 E12 저항값 중 기하 평균에 가장 가까운 값."""
    if r_min > r_max:
        return None
    target = math.sqrt(r_min * r_max)
    options = [base * 10**decade for decade in range(0, 7) for base in E12 if r_min <= base * 10**decade <= r_max]
    return min(options, key=lambda r: abs(math.log(r / target))) if options else None


def _hyp(cause: str, mechanism: str, confidence: float) -> Hypothesis:
    return Hypothesis(cause=cause, mechanism=mechanism, confidence=confidence)


# ---------------------------------------------------------------- 증상별 후보


def oscillation(ctx: Ctx, strength: int) -> list[Candidate]:
    vout, l_h, cout = ctx.get("vout_v"), ctx.get("l_h"), ctx.get("cout_f")
    factor = 1 + strength  # 첫 시도 2배, 검증 실패 후 재시도마다 더 크게
    tests: list[CheckStep] = []
    changes = [ComponentChange(target="보상 회로", before="현재 값", after="새 Cout 기준으로 데이터시트 설계식 재계산")]
    if cout:
        changes.insert(
            0, ComponentChange(target="출력 커패시터 Cout", before=_eng(cout, "F"), after=_eng(cout * factor, "F"))
        )
    if vout and l_h and cout:
        esr = ctx.get("esr_ohm", assumed="esr_ohm", label="출력 커패시터 ESR(Ω)")
        istep = ctx.get("iout_a", assumed="iout_a", label="부하 스텝 전류(A)")
        base = {"vout_v": vout, "l_h": l_h, "c_f": cout, "esr_ohm": esr, "istep_a": istep, "rbase_ohm": 100.0}
        tests = [
            _step("현재 LC 필터 공진 주파수", "info", "calc.lc_resonance", {"l_h": l_h, "c_f": cout}),
            _step(
                "현재 값으로 부하 스텝 언더슈트 재현",
                "reproduce",
                "spice.run_circuit",
                {"circuit": "lc_load_step", "params": base},
                Bound(metric="undershoot_pct", min=5.0),
            ),
            _step(
                f"Cout {factor}배 적용 후 언더슈트 개선율",
                "verify",
                "spice.compare_circuit",
                {"circuit": "lc_load_step", "before": base, "after": {**base, "c_f": cout * factor}},
                Bound(metric="improvement_pct", min=10.0),
            ),
        ]
    else:
        ctx.note("출력 전압·인덕터·출력 커패시터 값이 없어 발진 시뮬레이션을 만들지 못했습니다.")
    return [
        Candidate(
            _hyp(
                "출력 커패시터 값·ESR이 권장 범위를 벗어나 제어 루프 위상 여유가 부족함",
                "출력 커패시터와 ESR이 바뀌면 루프의 극점·영점이 이동합니다. 보상 회로가 새 값에 맞지 않으면 "
                "위상 여유가 줄어 부하 변동 때 링잉·발진이 생깁니다.",
                0.55,
            ),
            Solution(
                summary=f"출력 커패시터를 약 {factor}배로 늘리고, 데이터시트 설계식으로 보상값을 다시 계산합니다.",
                changes=changes,
                test_plan=tests,
            ),
            ["phase margin", "compensation", "output capacitor"],
        ),
        Candidate(
            _hyp(
                "경부하에서 펄스 스킵(PFM) 동작을 발진으로 오인",
                "경부하에서는 스위칭 주파수가 부하에 따라 바뀌어 리플이 불규칙하게 보입니다.",
                0.25,
            ),
            Solution(
                summary="강제 PWM 모드로 설정하거나 최소 부하를 달고 다시 측정합니다.",
                changes=[
                    ComponentChange(target="동작 모드", before="자동(PFM 포함)", after="강제 PWM 또는 최소 부하 추가")
                ],
            ),
            ["pulse-skipping", "light load"],
        ),
        Candidate(
            _hyp(
                "피드백(FB) 배선이 스위칭 노드 가까이 지나 노이즈가 루프에 들어감",
                "긴 FB 배선이 스위칭 노드와 결합하면 지터나 불안정이 생깁니다.",
                0.2,
            ),
            Solution(
                summary="FB 배선을 짧게 하고 스위칭 노드에서 떨어뜨리며, 분압 저항을 FB 핀 가까이 둡니다.",
                changes=[
                    ComponentChange(target="PCB 레이아웃", before="FB 배선이 SW 노드에 인접", after="FB 배선 단축·이격")
                ],
            ),
            ["feedback", "switch node"],
        ),
    ]


def ripple_noise(ctx: Ctx, strength: int) -> list[Candidate]:
    vin, vout, l_h, cout, iout = (ctx.get(k) for k in ("vin_v", "vout_v", "l_h", "cout_f", "iout_a"))
    factor = 1 + strength
    l_factor = 1 + 0.5 * strength
    cap_tests: list[CheckStep] = []
    ind_tests: list[CheckStep] = []
    if vin and vout and l_h and cout and vout < vin:
        fsw = ctx.get("fsw_hz", "typical", "fsw_hz", assumed="fsw_hz", label="스위칭 주파수(Hz)")
        esr = ctx.get("esr_ohm", assumed="esr_ohm", label="출력 커패시터 ESR(Ω)")
        base = {
            "vin_v": vin,
            "vout_v": vout,
            "l_h": l_h,
            "fsw_hz": fsw,
            "cout_f": cout,
            "esr_ohm": esr,
            "iout_a": iout or 0.0,
        }
        cap_tests = [
            _step(
                "현재 값의 출력 리플",
                "reproduce",
                "calc.buck_ripple_estimate",
                base,
                Bound(metric="ripple_pct", min=1.0),
            ),
            _step(
                f"Cout {factor}배 적용 후 출력 리플",
                "verify",
                "calc.buck_ripple_estimate",
                {**base, "cout_f": cout * factor},
                Bound(metric="ripple_pct", max=1.0),
            ),
        ]
        if iout:
            ind_tests = [
                _step(
                    "현재 인덕터 리플 전류 비율",
                    "reproduce",
                    "calc.buck_ripple_estimate",
                    base,
                    Bound(metric="ripple_ratio", min=0.4),
                ),
                _step(
                    f"L {l_factor:g}배 적용 후 리플 전류 비율",
                    "verify",
                    "calc.buck_ripple_estimate",
                    {**base, "l_h": l_h * l_factor},
                    Bound(metric="ripple_ratio", max=0.4),
                ),
            ]
    else:
        ctx.note("입력·출력 전압, 인덕터, 출력 커패시터 값이 있어야 리플을 계산할 수 있습니다.")
    return [
        Candidate(
            _hyp(
                "출력 커패시터 용량이 부족하거나 ESR이 커서 출력 리플이 큼",
                "출력 리플 ≈ ΔIL×ESR + ΔIL/(8·fsw·Cout) 이므로 Cout이 작거나 ESR이 크면 리플이 커집니다.",
                0.5,
            ),
            Solution(
                summary=f"저ESR 세라믹 커패시터로 출력 용량을 약 {factor}배 늘립니다.",
                changes=[
                    ComponentChange(
                        target="출력 커패시터 Cout",
                        before=_eng(cout, "F") if cout else "현재 값",
                        after=f"약 {factor}배, 저ESR 세라믹",
                    )
                ],
                test_plan=cap_tests,
            ),
            ["ripple", "esr"],
        ),
        Candidate(
            _hyp(
                "측정 방법 문제: 프로브 접지선이 길어 스위칭 노이즈가 섞임",
                "긴 접지선이 스위칭 노이즈를 받아 실제보다 큰 스파이크로 보이게 합니다.",
                0.3,
            ),
            Solution(
                summary="프로브 스프링 접지로 출력 커패시터 양단에서 다시 측정합니다.",
                changes=[
                    ComponentChange(target="측정 방법", before="긴 접지선", after="스프링 접지, 출력 커패시터 양단")
                ],
            ),
            ["probe", "ground spring"],
        ),
        Candidate(
            _hyp(
                "인덕터 값이 작아 인덕터 리플 전류가 큼",
                "ΔIL = (Vin−Vout)·D/(L·fsw) 이므로 L이 작으면 리플 전류와 출력 리플이 함께 커집니다.",
                0.2,
            ),
            Solution(
                summary="인덕터 값을 키워 리플 전류를 부하 전류의 20~40%로 맞춥니다.",
                changes=[
                    ComponentChange(
                        target="인덕터 L", before=_eng(l_h, "H") if l_h else "현재 값", after=f"약 {l_factor:g}배"
                    )
                ],
                test_plan=ind_tests,
            ),
            ["inductance", "ripple"],
        ),
    ]


def overheating(ctx: Ctx, strength: int) -> list[Candidate]:
    vin, vout, iout = ctx.get("vin_v"), ctx.get("vout_v"), ctx.get("iout_a")
    ta = ctx.get("ta_c", assumed="ta_c", label="주변 온도(°C)")
    theta = ctx.get("theta_ja_c_per_w", "thermal", assumed="theta_ja_c_per_w", label="θJA(°C/W)")
    tj_limit = ctx.get("tj_max_c", "recommended", assumed="tj_limit_c", label="접합 온도 한계(°C)")
    better_theta = theta * 0.75**strength
    tests: list[CheckStep] = []
    if ctx.part_type == "ldo" and vin and vout and iout and vout < vin:
        args = {"vin_v": vin, "vout_v": vout, "iout_a": iout, "t_ambient_c": ta, "theta_ja_c_per_w": theta}
        tool = "calc.ldo_thermal_estimate"
    elif ctx.part_type != "ldo" and vout and iout:
        eff = ctx.get("efficiency", "typical", assumed="efficiency", label="효율")
        args = {"pout_w": vout * iout, "efficiency": eff, "t_ambient_c": ta, "theta_ja_c_per_w": theta}
        tool = "calc.thermal_estimate"
    else:
        args, tool = {}, ""
        ctx.note("출력 전압·부하 전류(LDO는 입력 전압도)가 있어야 발열을 계산할 수 있습니다.")
    if tool:
        tests = [
            _step("현재 조건의 접합 온도", "reproduce", tool, args, Bound(metric="tj_c", min=100.0)),
            _step(
                f"θJA {better_theta:.0f} °C/W로 방열 개선 후 접합 온도",
                "verify",
                tool,
                {**args, "theta_ja_c_per_w": better_theta},
                Bound(metric="tj_c", max=tj_limit),
            ),
        ]
    iout_max = ctx.card_value("recommended", "iout_max_a")
    current_tests = []
    if iout and iout_max:
        current_tests = [
            _step(
                "부하 전류와 권장 최대 전류 비교",
                "reproduce",
                "calc.check_value",
                {"value": iout, "max_value": iout_max},
                Bound(metric="value", min=iout_max),
            )
        ]
    return [
        Candidate(
            _hyp(
                "손실 전력에 비해 방열(θJA)이 부족해 접합 온도가 높음",
                "접합 온도는 Tj = Ta + P·θJA 입니다. θJA는 PCB 동박 면적과 서멀 비아에 크게 좌우됩니다.",
                0.55,
            ),
            Solution(
                summary=f"노출 패드 아래 서멀 비아와 동박 면적을 늘려 θJA를 약 {better_theta:.0f} °C/W로 낮춥니다.",
                changes=[
                    ComponentChange(
                        target="PCB 방열", before=f"θJA {theta:g} °C/W", after=f"θJA {better_theta:.0f} °C/W 목표"
                    )
                ],
                test_plan=tests,
            ),
            ["junction temperature", "theta"],
        ),
        Candidate(
            _hyp(
                "부하 전류가 권장 최대 전류에 가깝거나 넘음",
                "전류가 커지면 도통 손실(I²R)이 전류의 제곱으로 늘어납니다.",
                0.25,
            ),
            Solution(summary="부하 전류를 줄이거나 전류 정격이 더 큰 부품을 검토합니다.", test_plan=current_tests),
            ["current limit", "load"],
        ),
        Candidate(
            _hyp(
                "입력 전압이 높아 스위칭 손실이 큼",
                "스위칭 손실은 입력 전압과 스위칭 주파수에 비례해 늘어납니다.",
                0.2,
            ),
            Solution(summary="입력 전압이나 스위칭 주파수를 낮추는 방안을 검토합니다."),
            ["power loss", "efficiency"],
        ),
    ]


def wrong_output(ctx: Ctx, strength: int) -> list[Candidate]:
    vref = ctx.get("vref_v", "typical")
    r_top, r_bottom = ctx.get("r_top_ohm"), ctx.get("r_bottom_ohm")
    measured, target = ctx.get("vout_meas_v"), ctx.get("vout_v")
    divider_tests: list[CheckStep] = []
    changes: list[ComponentChange] = []
    if vref and r_top and r_bottom and measured:
        divider_tests.append(
            _step(
                "분압 저항으로 계산한 출력 전압이 측정값과 맞는지",
                "reproduce",
                "calc.buck_output_voltage",
                {"vref_v": vref, "r_top_ohm": r_top, "r_bottom_ohm": r_bottom},
                Bound(metric="vout_v", min=measured * 0.97, max=measured * 1.03),
            )
        )
    elif not (r_top and r_bottom):
        ctx.note("피드백 분압 저항 값(상단·하단)이 있어야 출력 전압을 계산할 수 있습니다.")
    if vref and target and r_bottom and target > vref:
        new_top = r_bottom * (target / vref - 1)
        changes.append(
            ComponentChange(
                target="피드백 상단 저항", before=_eng(r_top, "Ω") if r_top else "현재 값", after=_eng(new_top, "Ω")
            )
        )
        divider_tests.append(
            _step(
                "새 상단 저항으로 목표 전압이 나오는지",
                "verify",
                "calc.buck_output_voltage",
                {"vref_v": vref, "r_top_ohm": new_top, "r_bottom_ohm": r_bottom},
                Bound(metric="vout_v", min=target * 0.99, max=target * 1.01),
            )
        )
    limit, iout = ctx.card_value("typical", "current_limit_a"), ctx.get("iout_a")
    limit_tests, limit_changes = [], []
    if limit and iout:
        limit_tests = [
            _step(
                "부하 전류와 전류 제한값 비교",
                "reproduce",
                "calc.check_value",
                {"value": iout, "max_value": limit},
                Bound(metric="value", min=limit),
            )
        ]
        limit_changes = [ComponentChange(target="부하 전류", before=f"{iout:g} A", after=f"{limit:g} A 미만")]
    return [
        Candidate(
            _hyp(
                "피드백 분압비가 목표 출력 전압과 맞지 않음",
                "출력 전압은 Vout = Vref·(1 + Rtop/Rbottom)으로 정해집니다.",
                0.45,
            ),
            Solution(
                summary="목표 전압에 맞게 상단 저항을 다시 계산하고 1% 저항을 씁니다.",
                changes=changes,
                test_plan=divider_tests,
            ),
            ["vref", "rtop"],
        ),
        Candidate(
            _hyp(
                "부하 전류가 전류 제한값을 넘어 과전류 보호가 동작함",
                "과전류 보호가 동작하면 출력 전압이 떨어지거나 주기적으로 꺼졌다 켜집니다(hiccup).",
                0.3,
            ),
            Solution(
                summary="부하 전류를 전류 제한값 아래로 낮추거나 전류 정격이 더 큰 부품을 씁니다.",
                changes=limit_changes,
                test_plan=limit_tests,
            ),
            ["current limit", "overcurrent"],
        ),
        Candidate(
            _hyp(
                "분압 저항이 너무 커서 FB 핀 누설 전류·노이즈의 영향을 받음",
                "FB 핀 누설 전류가 큰 저항에 흐르면 전압 오차가 생깁니다.",
                0.2,
            ),
            Solution(summary="분압 저항 값을 낮춥니다 (예: 하단 10 kΩ 수준)."),
            ["leakage"],
        ),
    ]


def startup_fail(ctx: Ctx, strength: int) -> list[Candidate]:
    factor = 1 + strength
    vin, vin_min = ctx.get("vin_v"), ctx.card_value("recommended", "vin_min_v")
    uvlo_tests = []
    if vin and vin_min:
        uvlo_tests = [
            _step(
                "입력 전압과 권장 최소 전압 비교",
                "reproduce",
                "calc.check_value",
                {"value": vin, "min_value": vin_min},
                Bound(metric="value", max=vin_min),
            )
        ]
    cout, vout, iout = ctx.get("cout_f"), ctx.get("vout_v"), ctx.get("iout_a")
    t_ss, limit = ctx.card_value("typical", "soft_start_s"), ctx.card_value("typical", "current_limit_a")
    inrush_tests = []
    if cout and vout and t_ss and limit:
        args = {"cout_f": cout, "vout_v": vout, "t_ss_s": t_ss, "iout_a": iout or 0.0}
        inrush_tests = [
            _step(
                "기동 중 돌입 전류 + 부하 전류",
                "reproduce",
                "calc.startup_inrush",
                args,
                Bound(metric="i_total_a", min=limit),
            ),
            _step(
                f"소프트스타트 {factor}배로 늘린 후",
                "verify",
                "calc.startup_inrush",
                {**args, "t_ss_s": t_ss * factor},
                Bound(metric="i_total_a", max=limit),
            ),
        ]
    return [
        Candidate(
            _hyp(
                "입력 전압이 UVLO·권장 최소 전압보다 낮음",
                "입력 전압이 UVLO 문턱보다 낮으면 스위칭을 시작하지 않습니다.",
                0.4,
            ),
            Solution(
                summary="입력 전압을 권장 최소값 이상으로 올리거나 입력 전원의 전류 용량을 확인합니다.",
                test_plan=uvlo_tests,
            ),
            ["uvlo"],
        ),
        Candidate(
            _hyp(
                "큰 출력 커패시터와 짧은 소프트스타트로 기동 중 과전류 보호가 동작(hiccup)",
                "돌입 전류는 Cout·Vout/t_ss 입니다. 여기에 부하 전류를 더한 값이 전류 제한을 넘으면 기동에 실패합니다.",
                0.3,
            ),
            Solution(
                summary=f"소프트스타트 시간을 약 {factor}배로 늘리거나 기동이 끝난 뒤 부하를 연결합니다.",
                changes=[
                    ComponentChange(
                        target="소프트스타트 시간",
                        before=_eng(t_ss, "s") if t_ss else "현재 값",
                        after=f"약 {factor}배",
                    )
                ],
                test_plan=inrush_tests,
            ),
            ["soft-start", "inrush"],
        ),
        Candidate(
            _hyp("EN 핀이 떠 있거나 문턱 전압보다 낮음", "EN 전압이 문턱보다 낮으면 소자가 켜지지 않습니다.", 0.2),
            Solution(summary="EN 핀 전압을 측정하고 풀업이나 분압으로 문턱 이상을 보장합니다."),
            ["enable", "threshold"],
        ),
    ]


def communication(ctx: Ctx, strength: int) -> list[Candidate]:
    vdd = ctx.get("vdd_v") or ctx.get("vout_v") or ctx.get("vdd_v", assumed="vdd_v", label="VDD(V)")
    cb = ctx.get("cb_f", assumed="cb_f", label="버스 커패시턴스(F)")
    pullup = ctx.get("pullup_ohm")
    tests: list[CheckStep] = []
    changes: list[ComponentChange] = []
    if vdd and cb and pullup:
        base = {"vdd_v": vdd, "cb_f": cb, "pullup_ohm": pullup}
        tests.append(
            _step(
                "현재 풀업이 허용 범위를 넘는지",
                "reproduce",
                "calc.i2c_pullup_check",
                base,
                Bound(metric="weak_ratio", min=1.0),
            )
        )
        proposal = _pick_e12((vdd - 0.4) / 3e-3, 300e-9 / (0.8473 * cb))
        if proposal:
            changes.append(ComponentChange(target="I2C 풀업 저항", before=_eng(pullup, "Ω"), after=_eng(proposal, "Ω")))
            tests.append(
                _step(
                    f"풀업 {_eng(proposal, 'Ω')} 적용 후",
                    "verify",
                    "calc.i2c_pullup_check",
                    {**base, "pullup_ohm": proposal},
                    Bound(metric="weak_ratio", max=1.0),
                    Bound(metric="strong_ratio", max=1.0),
                )
            )
        else:
            ctx.note("버스 커패시턴스가 커서 규격을 만족하는 풀업 값이 없습니다. 배선 길이나 소자 수를 줄여야 합니다.")
    else:
        ctx.note("VDD, 풀업 저항, 버스 커패시턴스 값이 있어야 풀업을 계산할 수 있습니다.")
    return [
        Candidate(
            _hyp(
                "풀업 저항이 버스 커패시턴스에 비해 커서 상승 시간이 규격을 넘음",
                "상승 시간은 약 0.8473·R·Cb 입니다. 풀업이 크면 상승이 느려져 비트를 잘못 읽거나 NACK이 납니다.",
                0.5,
            ),
            Solution(
                summary="풀업 저항을 허용 범위 안의 값으로 낮추고, 가능하면 버스 커패시턴스도 줄입니다.",
                changes=changes,
                test_plan=tests,
            ),
            ["pull-up", "rise time"],
        ),
        Candidate(
            _hyp(
                "같은 버스에 주소가 같은 소자가 있음(주소 충돌)",
                "주소가 겹치면 두 소자가 동시에 응답해 통신이 깨집니다.",
                0.25,
            ),
            Solution(summary="주소 설정 핀 상태와 버스의 모든 소자 주소를 확인합니다."),
            ["address"],
        ),
        Candidate(
            _hyp(
                "전원 투입 직후 소자가 준비되기 전에 통신을 시작함",
                "전원 안정 시간 전에는 소자가 응답하지 않습니다.",
                0.2,
            ),
            Solution(summary="전원 투입 후 데이터시트의 준비 시간만큼 기다린 뒤 통신을 시작합니다."),
            ["power-up"],
        ),
    ]


def generic(ctx: Ctx, strength: int) -> list[Candidate]:
    return [
        Candidate(
            _hyp("정보가 부족해 원인을 특정하기 어려움", "증상과 동작 조건이 더 필요합니다.", 0.2),
            Solution(summary="회로도, 동작 조건, 측정 파형을 받아 다시 분석합니다."),
            [],
        )
    ]


def rating_candidate(ctx: Ctx) -> Candidate | None:
    """입력 전압이 절대 최대 정격을 넘는 경우의 후보 (검수 피드백으로 추가)."""
    for key in ("vin_v", "vdd_v"):
        value, limit = ctx.cond.get(key), ctx.card_value("abs_max", key)
        if value and limit and value > limit:
            break
    else:
        return None
    rec_max = ctx.card_value("recommended", key.replace("_v", "_max_v")) or limit
    vout, iout = ctx.get("vout_v"), ctx.get("iout_a")
    target = rec_max
    if ctx.part_type == "ldo" and vout:
        target = min(rec_max, vout + LDO_HEADROOM_V)
        ctx.assumptions["ldo_headroom"] = f"LDO 입력 여유 = {LDO_HEADROOM_V} V (가정)"
    tests = [
        _step(
            "입력 전압과 절대 최대 정격 비교",
            "reproduce",
            "calc.check_value",
            {"value": value, "max_value": limit},
            Bound(metric="value", min=limit),
        )
    ]
    if ctx.part_type == "ldo" and vout and iout and vout < target:
        ta = ctx.get("ta_c", assumed="ta_c", label="주변 온도(°C)")
        theta = ctx.get("theta_ja_c_per_w", "thermal", assumed="theta_ja_c_per_w", label="θJA(°C/W)")
        tj_limit = ctx.get("tj_max_c", "recommended", assumed="tj_limit_c", label="접합 온도 한계(°C)")
        tests.append(
            _step(
                f"입력을 {target:g} V로 낮춘 뒤 접합 온도",
                "verify",
                "calc.ldo_thermal_estimate",
                {"vin_v": target, "vout_v": vout, "iout_a": iout, "t_ambient_c": ta, "theta_ja_c_per_w": theta},
                Bound(metric="tj_c", max=tj_limit),
            )
        )
    hypothesis = _hyp(
        f"입력 {value:g} V가 절대 최대 정격 {limit:g} V를 넘음 (전기적 과부하·EOS 위험)",
        "절대 최대 정격을 넘으면 소자가 영구 손상될 수 있고, 발열과 오동작이 함께 나타날 수 있습니다.",
        0.8,
    )
    hypothesis.evidence = [
        Evidence(
            source=f"{ctx.card.get('_source', 'parts')} abs_max.{key}",
            snippet=f"절대 최대 {key} = {limit:g} V",
            score=1.0,
        )
    ]
    return Candidate(
        hypothesis,
        Solution(
            summary=f"입력 전압을 권장 범위 안({target:g} V)으로 낮추고, 이미 과전압을 겪은 부품은 교체를 권합니다.",
            changes=[ComponentChange(target="입력 전압", before=f"{value:g} V", after=f"{target:g} V")],
            test_plan=tests,
        ),
        ["absolute maximum", "overstress"],
    )


PLAYBOOKS: dict[str, Callable[[Ctx, int], list[Candidate]]] = {
    "oscillation": oscillation,
    "ripple_noise": ripple_noise,
    "overheating": overheating,
    "wrong_output": wrong_output,
    "startup_fail": startup_fail,
    "communication": communication,
}


def build_candidates(
    symptoms: list[str],
    conditions: dict[str, Any],
    card: dict[str, Any] | None,
    strength: int = 1,
    add_rating: bool = False,
) -> tuple[list[Candidate], Ctx]:
    ctx = Ctx(conditions, card)
    builder = next((PLAYBOOKS[s] for s in symptoms if s in PLAYBOOKS), generic)
    candidates = builder(ctx, strength)
    if add_rating:
        rating = rating_candidate(ctx)
        if rating:
            candidates.insert(0, rating)
    return candidates, ctx
