"""검수 에이전트: 근거·테스트·정격을 확인하고, 문제가 있으면 원인 분석으로 되돌려 보냅니다.

규칙 기반이라 결과가 항상 같습니다 (비용 없음, 재현 가능).
테스트가 통과했다는 사실만 보지 않고, 테스트 자체가 의미 있는지도 봅니다:
- self_referential_test: 값 대조의 합격 범위 경계가 검사한 값과 같아 항상 통과하는 재현(reproduce) 테스트.
  해결 확인(verify) 테스트는 재분석해도 고쳐지지 않는 경우가 많아 지적하지 않고 경고만 한다
- test_mismatch: 부품 종류에 맞지 않는 계산·회로를 쓴 테스트
- rating_false_claim: 고객 값이 한계를 넘지 않는데 원인 가설 제목에 '정격 초과'를 쓴 경우
- tests_dropped: 테스트 오류로 되돌려진 재분석이 오류를 고치지 않고 테스트를 모두 뺀 경우 (그 부품에 쓸 도구가 있을 때)
- trivial_test: 기대 범위가 어떤 값이나 통과하는 테스트 (gain_ratio를 0~1 전체로 두는 것처럼)
값 대조(check_value)가 실패하면 지적문에 합격 범위 방향 힌트를 붙인다 (방향을 틀려 3번 재분석하고도 못 고친 사례가 있었다).
경고만 하는 것: 같은 조건의 연산증폭기 계산과 시뮬레이션 결과 대조(일치 또는 불일치), 테스트 구성,
고객 조건과 다른 값을 쓴 재현·참고 계산, 일부만 무의미한 기대 범위.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from typing import Any

from ..ratings import rating_facts
from ..schemas import CheckReport, CheckStep, Diagnosis, IntakeResult, QAReview

# 부품 종류(부품 카드의 type)에 맞지 않는 도구를 거르는 표. 표에 없는 도구·부품 종류는 검사하지 않는다.
TOOL_PART_TYPES: dict[str, set[str]] = {
    "calc.buck_ripple_estimate": {"buck"},
    "calc.thermal_estimate": {"buck", "ldo"},  # 효율 기반 손실식. LDO는 효율=Vout/Vin으로 쓸 수 있어 허용
    "calc.ldo_thermal_estimate": {"ldo"},
    "calc.noninverting_gain": {"opamp"},
    "calc.opamp_closed_loop_gain": {"opamp"},
}
CIRCUIT_PART_TYPES: dict[str, set[str]] = {
    "lc_load_step": {"buck", "ldo"},  # 이상적 전원 + LC 출력 필터 모델
    "opamp_ac": {"opamp"},  # 비반전 증폭기 + 1차 연산증폭기 모델의 주파수 응답
}

# 같은 조건의 계산(calc.opamp_closed_loop_gain)과 시뮬레이션(opamp_ac)을 대조할 때 같아야 하는 인자와 허용 오차
CROSS_CHECK_KEYS = ("rf_ohm", "rg_ohm", "gbw_hz", "freq_hz")
CROSS_CHECK_TOLERANCE = 0.05

# 값 대조(check_value)가 실패했을 때 지적문에 붙이는 방향 힌트. 방향이 맞는데 실패하면 가설이 틀린 것이라는 말도 함께 적어
# 통과시키려고 방향만 뒤집는 일을 막는다.
DIRECTION_HINTS = {
    "reproduce": (
        " [값 대조의 합격 범위는 '증상이 나타나는 쪽'입니다. 값이 한계보다 커서 문제라는 가설이면 min=한계, 작아서 문제라는 "
        "가설이면 max=한계가 맞는지 확인하세요. 방향이 맞는데도 실패하면 값이 실제로 증상 쪽이 아니라는 뜻이니 가설을 바꾸세요]"
    ),
    "verify": (
        " [해결 후 값은 '정상 쪽'이어야 합니다. 큰 값이 문제였다면 max=한계, 작은 값이 문제였다면 min=한계로 (여유를 두고) "
        "쓰세요. 방향이 맞는데도 실패하면 해결책이 부족한 것이니 값을 다시 역산하세요]"
    ),
}
# 자연 범위가 알려진 지표: 이 범위 전체를 덮는 기대 범위는 어떤 값이나 통과한다 (값이 없는 한쪽은 제한 없음으로 본다)
NATURAL_RANGES: dict[str, tuple[float, float]] = {"gain_ratio": (0.0, 1.0), "duty": (0.0, 1.0)}
# 테스트 인자 이름 → 접수 조건 이름. 재현·참고 계산은 고객이 알려 준 조건으로 해야 한다 (조건을 바꾼 계산은 verify만)
ARG_TO_CONDITION = {
    "vin_v": "vin_v", "vout_v": "vout_v", "iout_a": "iout_a", "fsw_hz": "fsw_hz",
    "cout_f": "cout_f", "esr_ohm": "esr_ohm", "l_h": "l_h", "t_ambient_c": "ta_c",
    "ta_c": "ta_c", "vdd_v": "vdd_v", "cb_f": "cb_f", "pullup_ohm": "pullup_ohm",
}  # fmt: skip
CONDITION_TOLERANCE = 0.01

RATING_WORDS = ("정격", "절대 최대", "절대최대")
# 이전 시도가 테스트 문제로 되돌려졌음을 뜻하는 검수 지적 코드 (다음 시도가 테스트를 빼면 tests_dropped)
TEST_TROUBLE_CODES = {"test_error", "tests_dropped"}


def _check_ratings(
    intake: IntakeResult,
    research: dict[str, Any],
    diagnosis: Diagnosis,
    add: Callable[[str, str], None],
    warnings: list[str],
) -> None:
    facts = rating_facts(intake, research.get("part_card") or {})
    mentions_rating = any(any(word in h.cause for word in RATING_WORDS) for h in diagnosis.hypotheses)
    claims_rating = any("정격 초과" in h.cause for h in diagnosis.hypotheses)
    if facts["abs_over"] and not mentions_rating:
        add("rating_not_addressed", facts["abs_over"][0] + ". 그런데 원인 가설에 정격 초과가 없습니다.")
    if claims_rating and not facts["abs_over"] and not facts["rec_over"] and facts["within"]:
        add(
            "rating_false_claim",
            "원인 가설에 '정격 초과'가 있지만 고객 값은 부품 카드의 한계를 넘지 않습니다 "
            f"(확인한 항목: {', '.join(facts['within'])}). 실제 원인을 제목으로 쓰세요.",
        )
    warnings.extend(f"{text}." for text in facts["rec_over"])


def _self_referential(step: CheckStep) -> tuple[float, float] | None:
    """값 대조(check_value)의 합격 범위 경계가 검사한 값과 같으면 (값, 경계)를 돌려준다. 항상 통과하는 테스트다."""
    if step.purpose == "info" or step.tool != "calc.check_value":
        return None
    try:
        value = step.args().get("value")
    except (ValueError, TypeError):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    for bound in step.expect:
        for edge in (bound.min, bound.max):
            if edge is not None and math.isclose(value, edge, rel_tol=1e-9, abs_tol=0.0):
                return value, edge
    return None


def _mismatch(step: CheckStep, part_type: str) -> str | None:
    """부품 종류에 맞지 않는 계산·회로를 쓴 테스트를 지적한다. 부품 종류를 모르면 검사하지 않는다."""
    if not part_type or step.purpose == "info":
        return None
    allowed = TOOL_PART_TYPES.get(step.tool)
    if allowed is None and step.tool.startswith("spice."):
        try:
            allowed = CIRCUIT_PART_TYPES.get(str(step.args().get("circuit", "")))
        except (ValueError, TypeError, AttributeError):
            allowed = None
    if allowed and part_type not in allowed:
        return (
            f"테스트 '{step.label}'({step.tool})는 {part_type} 부품에 맞지 않는 계산입니다. "
            "부품 종류에 맞는 도구를 쓰거나, 맞는 도구가 없으면 근거 문서로 설명하세요."
        )
    return None


def _direction_hint(step: Any) -> str:
    """실패한 값 대조(check_value)의 지적문에 붙일 방향 힌트. 다른 도구에는 붙이지 않는다."""
    if step.tool != "calc.check_value":
        return ""
    return DIRECTION_HINTS["reproduce" if step.purpose == "reproduce" else "verify"]


def _vacuous(bound: Any) -> bool:
    """기대 범위가 어떤 값이나 통과하는지: 한쪽이 비었거나 자연 범위 바깥까지 열려 있고, 다른 쪽도 그렇다."""
    low, high = NATURAL_RANGES.get(bound.metric, (None, None))
    lower_open = bound.min is None or (low is not None and bound.min <= low)
    upper_open = bound.max is None or (high is not None and bound.max >= high)
    return lower_open and upper_open


def _describe_bounds(bounds: list[Any]) -> str:
    def edge(value: float | None) -> str:
        return "제한 없음" if value is None else f"{value:g}"

    return ", ".join(f"{b.metric} {edge(b.min)}~{edge(b.max)}" for b in bounds)


def _trivial_expectations(step: CheckStep) -> tuple[str | None, str | None]:
    """(지적문, 경고문): 모든 기대 범위가 무의미하면 지적, 일부만 그러면 경고."""
    if step.purpose == "info" or not step.expect:
        return None, None
    vacuous = [b for b in step.expect if _vacuous(b)]
    if not vacuous:
        return None, None
    if len(vacuous) == len(step.expect):
        issue = (
            f"테스트 '{step.label}'의 기대 범위({_describe_bounds(vacuous)})는 "
            "어떤 값이나 통과해서 아무것도 확인하지 못합니다. 합격과 불합격이 실제로 갈리는 범위로 다시 정하세요."
        )
        return issue, None
    return None, f"테스트 '{step.label}'의 기대 범위({_describe_bounds(vacuous)})는 어떤 값이나 통과합니다."


def _condition_mismatches(step: CheckStep, conditions: dict[str, Any]) -> list[str]:
    """재현·참고 계산이 고객이 알려 준 조건과 다른 값을 쓰면 알려 준다 (해결 확인 verify는 값을 바꾸는 것이 정상)."""
    if step.purpose == "verify" or step.tool == "calc.check_value":
        return []
    try:
        args = step.args()
    except (ValueError, TypeError):
        return []
    if isinstance(args.get("params"), dict):  # spice.run_circuit은 인자가 params 안에 있다
        args = {**args, **args["params"]}
    messages = []
    for arg, condition in ARG_TO_CONDITION.items():
        value, known = args.get(arg), conditions.get(condition)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or known is None:
            continue
        if not math.isclose(value, known, rel_tol=CONDITION_TOLERANCE):
            messages.append(
                f"테스트 '{step.label}'의 {arg}={value:g}는 고객이 알려 준 값({known:g})과 다릅니다. "
                "고객 조건과 다른 값으로 계산한 결과는 고객 답변에 쓰면 안 됩니다."
            )
    return messages


def _tests_dropped(plan: list[CheckStep], previous_codes: set[str], part_type: str) -> str | None:
    """테스트 오류로 되돌려진 재분석이 오류를 고치지 않고 테스트를 모두 뺐는지 본다.

    이 부품 종류에 쓸 수 있는 도구가 표(TOOL_PART_TYPES)에 있을 때만 지적한다. 센서처럼 맞는 도구가 없거나
    부품 종류를 모르면, 처음부터 테스트가 없는 문의와 같아서 경고만 한다.
    """
    if not previous_codes & TEST_TROUBLE_CODES or not part_type or any(s.purpose != "info" for s in plan):
        return None
    tools = sorted(tool for tool, types in TOOL_PART_TYPES.items() if part_type in types)
    if not tools:
        return None
    return (
        "이전 분석의 테스트가 오류로 되돌려졌는데 이번에는 테스트를 모두 뺐습니다. 테스트를 지우지 말고 오류를 고쳐 "
        f"다시 넣으세요 (인자, expect의 지표 이름 확인). {part_type} 부품에 쓸 수 있는 도구: {', '.join(tools)}."
    )


def _cross_check(plan: list[CheckStep], report: CheckReport) -> list[str]:
    """같은 조건의 연산증폭기 계산과 시뮬레이션의 이득(gain_at_freq)을 대조해 알려 준다 (경고 목록에 넣는다).

    두 결과가 허용 오차 안이면 서로 다른 방법으로 확인됐다는 뜻이고, 벗어나면 모델 가정이 다르다는 신호다.
    테스트 계획과 결과는 같은 순서라 짝을 지을 수 없으면(길이가 다르면) 대조하지 않는다.
    """
    if len(plan) != len(report.steps):
        return []
    calc_runs: list[tuple[dict[str, Any], float]] = []
    spice_runs: list[tuple[dict[str, Any], float]] = []
    for step, result in zip(plan, report.steps, strict=True):
        gain = result.outputs.get("gain_at_freq")
        if result.status not in ("pass", "fail") or isinstance(gain, bool) or not isinstance(gain, (int, float)):
            continue
        try:
            args = step.args()
        except (ValueError, TypeError):
            continue
        if step.tool == "calc.opamp_closed_loop_gain":
            calc_runs.append((args, float(gain)))
        elif step.tool == "spice.run_circuit" and args.get("circuit") == "opamp_ac":
            spice_runs.append((args.get("params") or {}, float(gain)))
    messages: list[str] = []
    for calc_args, calc_gain in calc_runs:
        for spice_args, spice_gain in spice_runs:
            if not all(_close(calc_args.get(k), spice_args.get(k)) for k in CROSS_CHECK_KEYS):
                continue
            diff = abs(calc_gain - spice_gain) / max(abs(calc_gain), 1e-12)
            if diff <= CROSS_CHECK_TOLERANCE:
                text = f"교차 확인: 계산과 시뮬레이션의 이득이 {diff * 100:.2g} % 이내로 일치합니다 ({calc_gain:.4g}배)"
            else:
                text = (
                    f"교차 확인 불일치: 계산 이득 {calc_gain:.4g}배, 시뮬레이션 {spice_gain:.4g}배 "
                    f"(차이 {diff * 100:.2g} %). 입력값이나 모델 가정을 확인하세요"
                )
            if text not in messages:
                messages.append(text)
    return messages


def _close(a: Any, b: Any) -> bool:
    numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b))
    return numbers and math.isclose(a, b, rel_tol=1e-6)


def _composition(steps: list[CheckStep]) -> str | None:
    """승인자가 검증의 성격을 알 수 있도록 테스트 구성을 알려 준다 (참고 계산 제외)."""
    counted = [s for s in steps if s.purpose != "info"]
    if not counted:
        return None
    simulation = sum(1 for s in counted if s.tool.startswith("spice."))
    check_value = sum(1 for s in counted if s.tool == "calc.check_value")
    formula = len(counted) - simulation - check_value
    text = f"테스트 구성(참고 계산 제외): 공식 계산 {formula}, 시뮬레이션 {simulation}, 값 대조 {check_value}"
    if formula + simulation == 0:
        text += " — 값 대조만으로는 원인을 검증했다고 보기 어렵습니다"
    return text


def run_qa(
    intake: IntakeResult,
    research: dict[str, Any],
    diagnosis: Diagnosis,
    report: CheckReport,
    previous_codes: Iterable[str] = (),
) -> QAReview:
    """previous_codes: 바로 앞 시도의 검수 지적 코드 (재분석일 때만. 첫 시도는 비워 둔다)."""
    issues: list[str] = []
    codes: list[str] = []
    warnings: list[str] = []

    def add(code: str, message: str) -> None:
        codes.append(code)
        issues.append(message)

    if not diagnosis.hypotheses:
        add("no_hypothesis", "원인 가설이 없습니다.")
    elif not diagnosis.hypotheses[0].evidence:
        add("no_evidence", "1순위 가설을 뒷받침하는 문서 근거가 없습니다.")
    if diagnosis.solution is None:
        add("no_solution", "해결책이 없습니다.")

    for step in report.steps:
        if step.status == "error":
            add("test_error", f"테스트 실행 오류 — {step.label}: {step.notes}")
        elif step.status == "fail" and step.purpose == "reproduce":
            add(
                "reproduce_fail",
                f"1순위 가설이 증상을 재현하지 못했습니다 — {step.label}: {step.notes}{_direction_hint(step)}",
            )
        elif step.status == "fail":
            add("verify_fail", f"해결책이 목표를 만족하지 못했습니다 — {step.label}: {step.notes}{_direction_hint(step)}")
        elif step.status == "skipped":
            warnings.append(f"테스트 생략 — {step.label}: {step.notes}")
    if not report.steps:
        warnings.append("자동 테스트가 없습니다. 엔지니어가 직접 확인해야 합니다.")

    plan = diagnosis.solution.test_plan if diagnosis.solution else []
    part_type = str((research.get("part_card") or {}).get("type", "")).lower()
    for step in plan:
        if edge_hit := _self_referential(step):
            value, edge = edge_hit
            if step.purpose == "reproduce":
                # 재현 테스트가 항상 통과하면 가설이 맞다는 거짓 증거가 되므로 재분석을 요구한다
                add(
                    "self_referential_test",
                    f"테스트 '{step.label}'는 합격 범위 경계({edge:g})가 검사한 값({value:g})과 같아 항상 통과합니다. "
                    "실제 조건에서 값을 가져오고 경계는 데이터시트 한계로 정하세요.",
                )
            else:
                # 해결 확인은 설계값이 한계와 같은 경우가 흔하다. 재분석해도 고쳐지지 않아 경고만 한다
                warnings.append(
                    f"테스트 '{step.label}'는 합격 범위 경계({edge:g})가 검사한 값({value:g})과 같아 항상 통과합니다. "
                    "해결책이 통한다는 증거가 아니라 설계값 확인입니다."
                )
        if message := _mismatch(step, part_type):
            add("test_mismatch", message)
        issue, warning = _trivial_expectations(step)
        if issue:
            add("trivial_test", issue)
        if warning:
            warnings.append(warning)
        warnings.extend(_condition_mismatches(step, intake.conditions.known()))
    if message := _tests_dropped(plan, set(previous_codes), part_type):
        add("tests_dropped", message)
    if composition := _composition(plan):
        warnings.append(composition)
    warnings.extend(_cross_check(plan, report))

    _check_ratings(intake, research, diagnosis, add, warnings)
    if intake.missing_info:
        warnings.append("고객에게 추가로 받을 정보: " + ", ".join(intake.missing_info))
    return QAReview(passed=not codes, issues=issues, issue_codes=sorted(set(codes)), warnings=warnings)
