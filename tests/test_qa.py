"""검수 규칙 테스트: 테스트 자체의 질(자기 참조, 맞지 않는 도구)과 정격 판정(수치 비교)."""

import json

from fae_agent.agents.qa import rating_facts, run_qa
from fae_agent.schemas import (
    Bound,
    CheckReport,
    CheckStep,
    Conditions,
    Diagnosis,
    Evidence,
    Hypothesis,
    IntakeResult,
    Solution,
    StepResult,
)

LDO = {"type": "ldo", "abs_max": {"vin_v": 6.5}, "recommended": {"vin_min_v": 1.6, "vin_max_v": 6.0, "iout_max_a": 0.3}}
OPAMP = {"type": "opamp", "abs_max": {"vs_v": 6}, "recommended": {"vs_min_v": 2.5, "vs_max_v": 5.5}}
BUCK = {"type": "buck", "abs_max": {"vin_v": 45}, "recommended": {"vin_max_v": 40, "iout_max_a": 3.0}}
EMPTY_REPORT = CheckReport(status="pass", steps=[])


def intake(**conditions) -> IntakeResult:
    return IntakeResult(category="technical", route="fae", conditions=Conditions(**conditions))


def step(tool: str, args: dict, *bounds: Bound, purpose: str = "reproduce") -> CheckStep:
    return CheckStep(label="테스트", purpose=purpose, tool=tool, args_json=json.dumps(args), expect=list(bounds))


def diagnosis(cause: str = "출력 커패시터 조건 불만족", *steps: CheckStep) -> Diagnosis:
    hypothesis = Hypothesis(cause=cause, evidence=[Evidence(source="parts/x.yaml")])
    return Diagnosis(hypotheses=[hypothesis], solution=Solution(summary="해결", test_plan=list(steps)))


def codes(card: dict, cond: dict, diag: Diagnosis) -> set[str]:
    return set(run_qa(intake(**cond), {"part_card": card}, diag, EMPTY_REPORT).issue_codes)


def test_self_referential_edges_are_rejected():
    # T-06 사례: 값 0.3이 합격 범위 0.3~0.3의 경계
    edge = step("calc.check_value", {"value": 0.3, "max_value": 0.3}, Bound(metric="value", min=0.3, max=0.3))
    # T-05 사례: 값 0.0이 합격 범위 0.0~0.1의 경계
    zero = step("calc.check_value", {"value": 0.0}, Bound(metric="value", min=0.0, max=0.1))
    for bad in (edge, zero):
        assert "self_referential_test" in codes(LDO, {}, diagnosis("x", bad))


def test_self_referential_verify_step_is_only_a_warning():
    # H-T2 사례: "부하를 정격 이내(0.3 A)로 낮춘 경우"를 값 0.3, 범위 최대 0.3으로 확인
    verify = step("calc.check_value", {"value": 0.3}, Bound(metric="value", max=0.3), purpose="verify")
    review = run_qa(intake(), {"part_card": LDO}, diagnosis("x", verify), EMPTY_REPORT)
    assert "self_referential_test" not in review.issue_codes and review.passed
    assert any("항상 통과" in w and "설계값 확인" in w for w in review.warnings)


def test_self_referential_allows_real_comparisons_and_info_steps():
    over = step("calc.check_value", {"value": 6.8}, Bound(metric="value", min=6.5))
    margin = step("calc.check_value", {"value": 5.0}, Bound(metric="value", max=6.0), purpose="verify")
    info = step("calc.check_value", {"value": 0.3}, Bound(metric="value", min=0.3), purpose="info")
    formula = step("calc.ldo_thermal_estimate", {"vin_v": 5.5}, Bound(metric="tj_c", min=125.0))
    assert "self_referential_test" not in codes(LDO, {}, diagnosis("x", over, margin, info, formula))


def test_mismatched_tool_for_part_type_is_flagged():
    lc = step("spice.run_circuit", {"circuit": "lc_load_step", "params": {}}, Bound(metric="undershoot_pct", min=5.0))
    assert "test_mismatch" in codes(OPAMP, {}, diagnosis("x", lc))  # 연산증폭기에 LC 필터 시뮬레이션
    assert "test_mismatch" not in codes(BUCK, {}, diagnosis("x", lc))
    assert "test_mismatch" not in codes(LDO, {}, diagnosis("x", lc))
    ripple = step("calc.buck_ripple_estimate", {}, Bound(metric="ripple_v", max=0.05))
    assert "test_mismatch" in codes(LDO, {}, diagnosis("x", ripple))
    ldo_thermal = step("calc.ldo_thermal_estimate", {}, Bound(metric="tj_c", max=125.0))
    assert "test_mismatch" in codes(BUCK, {}, diagnosis("x", ldo_thermal))


def test_opamp_tools_are_limited_to_opamps():
    gain = step("calc.opamp_closed_loop_gain", {}, Bound(metric="gain_ratio", max=0.9))
    assert "test_mismatch" not in codes(OPAMP, {}, diagnosis("x", gain))
    assert "test_mismatch" in codes(LDO, {}, diagnosis("x", gain))
    assert "test_mismatch" in codes(BUCK, {}, diagnosis("x", step("calc.noninverting_gain", {})))


def test_opamp_ac_circuit_is_limited_to_opamps():
    ac = step("spice.run_circuit", {"circuit": "opamp_ac", "params": {}}, Bound(metric="gain_ratio", max=0.9))
    assert "test_mismatch" not in codes(OPAMP, {}, diagnosis("x", ac))
    assert "test_mismatch" in codes(LDO, {}, diagnosis("x", ac))
    assert "test_mismatch" in codes(BUCK, {}, diagnosis("x", ac))


OPAMP_ARGS = {"rf_ohm": 9000, "rg_ohm": 1000, "gbw_hz": 1e6, "freq_hz": 200000, "vin_pp": 0.2}


def cross_check_warnings(calc_gain: float, spice_gain: float, spice_args: dict | None = None) -> list[str]:
    plan = [
        step("calc.opamp_closed_loop_gain", OPAMP_ARGS),
        step("spice.run_circuit", {"circuit": "opamp_ac", "params": spice_args or OPAMP_ARGS}),
    ]
    results = [
        StepResult(label="t", purpose="reproduce", tool=s.tool, status="pass", outputs={"gain_at_freq": gain})
        for s, gain in zip(plan, (calc_gain, spice_gain), strict=True)
    ]
    review = run_qa(
        intake(), {"part_card": OPAMP}, diagnosis("x", *plan), CheckReport(status="pass", steps=results)
    )
    assert review.passed  # 교차 확인은 경고만 하고 재분석을 요구하지 않는다
    return [w for w in review.warnings if w.startswith("교차 확인")]


def test_cross_check_reports_agreement_between_calculation_and_simulation():
    found = cross_check_warnings(4.4721, 4.4723)  # H-A1: 계산 4.4721, ngspice 4.4723
    assert len(found) == 1 and "일치" in found[0] and "불일치" not in found[0]


def test_cross_check_flags_disagreement_and_ignores_different_conditions():
    found = cross_check_warnings(4.4721, 3.0)
    assert len(found) == 1 and "불일치" in found[0] and "4.472" in found[0] and "3" in found[0]
    # 시뮬레이션이 다른 주파수로 돌았으면 같은 조건이 아니므로 대조하지 않는다
    assert cross_check_warnings(4.4721, 3.0, {**OPAMP_ARGS, "freq_hz": 100000}) == []


def test_buck_switch_circuit_is_limited_to_bucks():
    sim = step("spice.run_circuit", {"circuit": "buck_switch", "params": {}}, Bound(metric="ripple_v", max=0.05))
    assert "test_mismatch" not in codes(BUCK, {}, diagnosis("x", sim))
    assert "test_mismatch" in codes(LDO, {}, diagnosis("x", sim))
    assert "test_mismatch" in codes(OPAMP, {}, diagnosis("x", sim))


BUCK_ARGS = {"vin_v": 12, "vout_v": 5, "l_h": 33e-6, "fsw_hz": 150e3, "cout_f": 100e-6, "esr_ohm": 0.3, "iout_a": 1.0}
CALC_OUT = {"delta_il_a": 0.589, "ripple_v": 0.1817}  # 평가 문의 L-02 조건의 연속 모드 계산
SIM_OUT = {"delta_il_a": 0.613, "ripple_v": 0.1737, "dcm": 0, "converged": True, "assumed": {"vd_v": 0.4}}


def buck_warnings(calc_out: dict, sim_out: dict, sim_args: dict | None = None) -> list[str]:
    plan = [
        step("calc.buck_ripple_estimate", BUCK_ARGS),
        step("spice.run_circuit", {"circuit": "buck_switch", "params": sim_args or BUCK_ARGS}),
    ]
    results = [
        StepResult(label="t", purpose="reproduce", tool=s.tool, status="pass", outputs=out)
        for s, out in zip(plan, (calc_out, sim_out), strict=True)
    ]
    review = run_qa(intake(), {"part_card": BUCK}, diagnosis("x", *plan), CheckReport(status="pass", steps=results))
    assert review.passed  # 교차 확인과 불연속 안내는 경고만 하고 재분석을 요구하지 않는다
    return review.warnings


def test_buck_cross_check_reports_agreement_within_tolerance():
    found = [w for w in buck_warnings(CALC_OUT, SIM_OUT) if w.startswith("교차 확인")]
    assert len(found) == 1 and "일치" in found[0] and "불일치" not in found[0]
    assert "0.589 A" in found[0] and "0.613 A" in found[0] and "182 mV" in found[0]  # 유효숫자 3자리로 적는다


def test_buck_cross_check_flags_large_gaps_and_names_the_diode_drop():
    low_vout = {"delta_il_a": 0.268, "ripple_v": 0.0029}  # 28 V → 1.2 V: 다이오드 강하가 커서 전류 리플이 33 % 크다
    calc = {"delta_il_a": 0.202, "ripple_v": 0.0030}
    found = [w for w in buck_warnings(calc, {**SIM_OUT, **low_vout}) if w.startswith("교차 확인")]
    assert len(found) == 1 and "불일치" in found[0] and "다이오드" in found[0]


def test_buck_cross_check_flags_discontinuous_conduction_even_when_numbers_agree():
    # L-03 사례: 계산과 시뮬레이션의 전류 리플은 거의 같지만 인덕터 전류가 0까지 내려간다
    agreeing = {"delta_il_a": 4.13, "ripple_v": 0.40}
    found = [w for w in buck_warnings({"delta_il_a": 4.137, "ripple_v": 0.429}, {**SIM_OUT, **agreeing, "dcm": 1})
             if w.startswith("교차 확인")]  # fmt: skip
    assert len(found) == 1 and "불일치" in found[0] and "불연속" in found[0] and "시뮬레이션 값을 쓰세요" in found[0]


def test_buck_cross_check_ignores_different_conditions():
    other = {**BUCK_ARGS, "l_h": 10e-6}
    assert [w for w in buck_warnings(CALC_OUT, SIM_OUT, other) if w.startswith("교차 확인")] == []
    no_esr = {k: v for k, v in BUCK_ARGS.items() if k != "esr_ohm"}  # 시뮬레이션에 ESR이 없으면 짝지을 수 없다
    assert [w for w in buck_warnings(CALC_OUT, SIM_OUT, no_esr) if w.startswith("교차 확인")] == []


def test_buck_notes_cover_dcm_calculation_nonconvergence_and_assumptions():
    calc_dcm = {**CALC_OUT, "dcm": 1, "iout_crit_a": 0.295}
    plan = [step("calc.buck_ripple_estimate", BUCK_ARGS)]
    report = CheckReport(
        status="pass", steps=[StepResult(label="t", purpose="reproduce", tool=plan[0].tool, status="pass", outputs=calc_dcm)]
    )
    review = run_qa(intake(), {"part_card": BUCK}, diagnosis("x", *plan), report)
    assert any("불연속 전도 영역" in w and "0.295 A" in w for w in review.warnings)  # 계산만 있을 때

    unconverged = {**SIM_OUT, "converged": False, "vout_error_pct": 3.2}
    warnings = buck_warnings(CALC_OUT, unconverged)
    assert any("수렴하지 않았습니다" in w and "3.2" in w for w in warnings)
    assert any("가정했습니다" in w and "vd_v=0.4" in w for w in warnings)
    given = {**BUCK_ARGS, "vd_v": 0.5}  # 고객이 준 값은 가정이 아니다
    assert not any("가정했습니다" in w for w in buck_warnings(CALC_OUT, SIM_OUT, given))


SYNC_BUCK = {**BUCK, "typical": {"rdson_high_ohm": 0.09, "rdson_low_ohm": 0.04}}  # 동기 벅 카드 (TPS62130A 같은 부품)


def assumption_warnings(card: dict, steps: list[CheckStep], **conditions) -> list[str]:
    review = run_qa(intake(**conditions), {"part_card": card}, diagnosis("x", *steps), EMPTY_REPORT)
    assert review.passed  # 가정 경고는 재분석을 요구하지 않는다
    return [w for w in review.warnings if "esr_ohm에 0" in w or "동기 벅" in w]


def test_zero_esr_in_buck_tests_is_warned_when_the_customer_gave_no_esr():
    ripple = step("calc.buck_ripple_estimate", {**BUCK_ARGS, "esr_ohm": 0.0}, purpose="info")
    omitted = step("calc.buck_ripple_estimate", {k: v for k, v in BUCK_ARGS.items() if k != "esr_ohm"}, purpose="info")
    sim = step("spice.run_circuit", {"circuit": "buck_switch", "params": {**BUCK_ARGS, "esr_ohm": 0}}, purpose="verify")
    found = assumption_warnings(BUCK, [ripple, omitted, sim])
    assert len(found) == 1 and "ESR을 무시" in found[0]  # 같은 문구는 한 번만


def test_esr_warning_is_skipped_when_the_customer_gave_esr_or_a_real_value_is_used():
    ripple = step("calc.buck_ripple_estimate", {**BUCK_ARGS, "esr_ohm": 0.005})
    assert assumption_warnings(BUCK, [ripple]) == []
    zero = step("calc.buck_ripple_estimate", {**BUCK_ARGS, "esr_ohm": 0.0})
    assert assumption_warnings(BUCK, [zero], esr_ohm=0.0) == []  # 고객이 ESR을 알려 준 경우는 다른 규칙이 다룬다


def test_synchronous_buck_simulation_without_vd_is_warned():
    plain = step("spice.run_circuit", {"circuit": "buck_switch", "params": {**BUCK_ARGS, "esr_ohm": 0.005}})
    found = assumption_warnings(SYNC_BUCK, [plain])
    assert len(found) == 1 and "vd_v를 지정하지 않아" in found[0] and "rdson_low_ohm" in found[0]
    with_vd = step(
        "spice.run_circuit", {"circuit": "buck_switch", "params": {**BUCK_ARGS, "esr_ohm": 0.005, "vd_v": 0.11}}
    )
    assert assumption_warnings(SYNC_BUCK, [with_vd]) == []
    assert assumption_warnings(BUCK, [plain]) == []  # 비동기 벅은 기본 다이오드 강하가 맞다


def failing(tool: str, purpose: str, notes: str) -> StepResult:
    return StepResult(label="확인", purpose=purpose, tool=tool, status="fail", notes=notes)


def test_failed_check_value_gets_a_direction_hint_but_other_tools_do_not():
    # N-A1·N-L3 사례: 값 대조의 합격 범위 방향을 틀려 재현 테스트가 3번 실패했다
    plan = diagnosis("x", step("calc.check_value", {"value": 9.0}, Bound(metric="value", max=5.0)))
    for purpose, keyword in (("reproduce", "증상이 나타나는 쪽"), ("verify", "정상 쪽")):
        report = CheckReport(status="fail", steps=[failing("calc.check_value", purpose, "value=9 > 5")])
        review = run_qa(intake(), {"part_card": LDO}, plan, report)
        assert keyword in review.issues[0] and "방향이 맞는데도 실패하면" in review.issues[0]
    other = CheckReport(status="fail", steps=[failing("calc.ldo_thermal_estimate", "reproduce", "tj_c=90 < 125")])
    assert "증상이 나타나는 쪽" not in run_qa(intake(), {"part_card": LDO}, plan, other).issues[0]


def test_direction_rule_of_the_prompt_example_matches_check_value_semantics():
    """프롬프트 예: 큰 값이 문제면 min=한계, 작은 값이 문제면 max=한계일 때 재현이 통과하고, 반대 방향이면 실패한다."""
    from fae_agent.agents.checker import evaluate
    from fae_agent.tools.calc import check_value

    def status(value: float, **bound) -> str:
        check = step("calc.check_value", {"value": value}, Bound(metric="value", **bound))
        return evaluate(check, check_value(value))[0]

    assert status(7.5, min=5.5) == "pass" and status(7.5, max=5.5) == "fail"  # 7.5 V가 상한 5.5 V를 넘어 문제
    assert status(0.9, max=1.5) == "pass" and status(0.9, min=1.5) == "fail"  # 0.9 V가 하한 1.5 V보다 낮아 문제


def test_vacuous_expectations_are_flagged():
    # N-A2 사례: 비율 지표를 0~1 전체로 둔 재현 테스트는 어떤 값이나 통과한다
    for bound in (
        Bound(metric="gain_ratio", min=0.0, max=1.0),
        Bound(metric="gain_ratio", max=1.0),
        Bound(metric="duty", min=0.0),
        Bound(metric="tj_c"),  # min·max가 모두 없음
    ):
        assert "trivial_test" in codes(OPAMP, {}, diagnosis("x", step("calc.opamp_closed_loop_gain", {}, bound)))


def test_meaningful_expectations_are_not_flagged_as_vacuous():
    real = [
        Bound(metric="gain_ratio", max=0.9),  # 설정 이득의 90 % 이하
        Bound(metric="gain_ratio", min=0.95, max=1.0),
        Bound(metric="vout_pp", min=0.0, max=6.0),  # 자연 범위를 모르는 지표는 한쪽이라도 값이 있으면 의미 있음
        Bound(metric="tj_c", min=125.0),
    ]
    for bound in real:
        assert "trivial_test" not in codes(OPAMP, {}, diagnosis("x", step("calc.opamp_closed_loop_gain", {}, bound)))
    info = step("calc.noninverting_gain", {"rf_ohm": 9e3, "rg_ohm": 1e3}, purpose="info")  # 참고 계산은 expect가 없어도 된다
    assert "trivial_test" not in codes(OPAMP, {}, diagnosis("x", info))


def test_partly_vacuous_step_only_warns():
    # N-A2 사례: 한 단계 안에서 gain_ratio 0~1(무의미)과 vout_pp 0~6(의미 있음)이 섞임
    bounds = (Bound(metric="gain_ratio", min=0.0, max=1.0), Bound(metric="vout_pp", max=6.0))
    mixed = step("calc.opamp_closed_loop_gain", {}, *bounds)
    review = run_qa(intake(), {"part_card": OPAMP}, diagnosis("x", mixed), EMPTY_REPORT)
    assert "trivial_test" not in review.issue_codes and review.passed
    assert any("어떤 값이나 통과" in w and "gain_ratio 0~1" in w for w in review.warnings)


def mismatch_warnings(plan_step: CheckStep, **conditions) -> list[str]:
    review = run_qa(intake(**conditions), {"part_card": BUCK}, diagnosis("x", plan_step), EMPTY_REPORT)
    return [w for w in review.warnings if "고객이 알려 준 값" in w]


def test_reference_calculation_with_other_conditions_than_the_customer_gave_is_warned():
    # N-L3 사례: 고객은 입력 28 V인데 참고 계산을 12 V로 만들어 답변에 실었다
    ripple = {"vin_v": 12.0, "vout_v": 1.2, "l_h": 1e-6, "fsw_hz": 570e3, "cout_f": 22e-6}
    for purpose in ("info", "reproduce"):
        found = mismatch_warnings(step("calc.buck_ripple_estimate", ripple, purpose=purpose), vin_v=28.0, vout_v=1.2)
        assert len(found) == 1 and "vin_v=12" in found[0] and "(28)" in found[0] and "고객 답변에 쓰면 안 됩니다" in found[0]
    # 시뮬레이션은 인자가 params 안에 있다
    circuit = step("spice.run_circuit", {"circuit": "lc_load_step", "params": {"vout_v": 5.0}}, purpose="reproduce")
    assert len(mismatch_warnings(circuit, vout_v=3.3)) == 1


def test_condition_mismatch_is_not_warned_for_verify_check_value_or_matching_values():
    ripple = {"vin_v": 12.0, "vout_v": 1.2}
    changed = step("calc.buck_ripple_estimate", ripple, purpose="verify")  # 해결책은 값을 바꾼다
    assert mismatch_warnings(changed, vin_v=28.0) == []
    assert mismatch_warnings(step("calc.check_value", {"value": 12.0}), vin_v=28.0) == []
    assert mismatch_warnings(step("calc.buck_ripple_estimate", ripple), vin_v=12.05) == []  # 오차 1 % 이내
    assert mismatch_warnings(step("calc.buck_ripple_estimate", ripple)) == []  # 고객이 값을 주지 않았으면 비교하지 않는다


def test_dropping_all_tests_after_a_test_error_is_flagged():
    # H-A1 사례: 첫 시도의 테스트가 test_error로 되돌려졌는데 재분석이 테스트를 모두 뺐다
    for previous in (["test_error"], ["tests_dropped"], ["no_evidence", "test_error"]):
        review = run_qa(intake(), {"part_card": OPAMP}, diagnosis("x"), EMPTY_REPORT, previous_codes=previous)
        assert "tests_dropped" in review.issue_codes and not review.passed
        assert "opamp_closed_loop_gain" in review.issues[0]  # 쓸 수 있는 도구를 알려 준다
    info_only = diagnosis("x", step("calc.noninverting_gain", {"rf_ohm": 9e3, "rg_ohm": 1e3}, purpose="info"))
    review = run_qa(intake(), {"part_card": OPAMP}, info_only, EMPTY_REPORT, previous_codes=["test_error"])
    assert "tests_dropped" in review.issue_codes  # 참고 계산만 남긴 것도 테스트를 뺀 것과 같다


def test_dropping_tests_is_allowed_when_it_is_not_a_reaction_to_an_error():
    empty = diagnosis("x")
    # 첫 시도(이전 지적 없음)나 다른 이유로 되돌려진 재분석은 테스트가 없어도 경고만 한다
    for previous in ((), ["no_evidence"], ["rating_not_addressed"]):
        review = run_qa(intake(), {"part_card": OPAMP}, empty, EMPTY_REPORT, previous_codes=previous)
        assert "tests_dropped" not in review.issue_codes
    kept = diagnosis("x", step("calc.opamp_closed_loop_gain", {}, Bound(metric="gain_ratio", max=0.9)))
    review = run_qa(intake(), {"part_card": OPAMP}, kept, EMPTY_REPORT, previous_codes=["test_error"])
    assert "tests_dropped" not in review.issue_codes  # 테스트를 고쳐서 유지한 경우


def test_dropping_tests_is_not_flagged_without_an_applicable_tool():
    empty = diagnosis("x")
    sensor = {"type": "sensor"}  # 표에 쓸 수 있는 도구가 없는 부품 종류
    for card in (sensor, {}):  # 부품 종류를 모르는 경우도 지적하지 않는다
        review = run_qa(intake(), {"part_card": card}, empty, EMPTY_REPORT, previous_codes=["test_error"])
        assert "tests_dropped" not in review.issue_codes


def test_unknown_part_type_is_not_checked():
    lc = step("spice.run_circuit", {"circuit": "lc_load_step", "params": {}}, Bound(metric="undershoot_pct", min=5.0))
    assert "test_mismatch" not in codes({}, {}, diagnosis("x", lc))


def test_rating_claim_without_exceeding_is_rejected():
    # T-06 사례: 입력 3.4 V, 부하 0.3 A는 한계 이내인데 제목이 '정격 초과'
    assert "rating_false_claim" in codes(LDO, {"vin_v": 3.4, "iout_a": 0.3}, diagnosis("정격 초과"))
    # T-01 반복 3 사례: 입력 5.5 V, 부하 0.3 A도 한계 이내
    assert "rating_false_claim" in codes(LDO, {"vin_v": 5.5, "iout_a": 0.3}, diagnosis("정격 초과"))


def test_rating_claim_is_accepted_when_value_really_exceeds():
    found = codes(LDO, {"vin_v": 6.8}, diagnosis("정격 초과"))
    assert "rating_false_claim" not in found and "rating_not_addressed" not in found
    recommended_only = codes(LDO, {"vin_v": 6.2}, diagnosis("정격 초과"))  # 권장 최대 6.0 V만 넘음
    assert "rating_false_claim" not in recommended_only


def test_rating_claim_is_not_judged_without_checkable_conditions():
    # 부품 카드에 한계가 없거나 고객 값이 없으면 판단할 수 없으므로 지적하지 않는다
    assert "rating_false_claim" not in codes({}, {"vin_v": 3.4}, diagnosis("정격 초과"))
    assert "rating_false_claim" not in codes(LDO, {}, diagnosis("정격 초과"))


def test_missing_rating_hypothesis_is_flagged_including_supply_alias():
    assert "rating_not_addressed" in codes(LDO, {"vin_v": 6.8}, diagnosis("LDO 발열 과다"))
    # 연산증폭기 카드는 vs_v를 쓴다: 고객이 12 V 전원을 vin_v로 알려도 절대 최대 6 V와 비교
    assert "rating_not_addressed" in codes(OPAMP, {"vin_v": 12.0}, diagnosis("출력 스윙 한계"))
    assert "rating_not_addressed" not in codes(OPAMP, {"vin_v": 12.0}, diagnosis("공급 전압 정격 초과"))


def test_recommended_range_violations_are_warnings_not_failures():
    review = run_qa(intake(vin_v=6.2, iout_a=0.4), {"part_card": LDO}, diagnosis("발열"), EMPTY_REPORT)
    assert review.passed
    assert any("권장 최대 6 V" in w for w in review.warnings) and any("권장 최대 0.3 A" in w for w in review.warnings)


def test_rating_facts_lists_what_was_checked():
    facts = rating_facts(intake(vin_v=3.4, iout_a=0.3), LDO)
    assert facts["within"] == ["입력 전압", "부하 전류"] and not facts["abs_over"] and not facts["rec_over"]


def test_test_composition_is_reported_to_reviewers():
    only_values = diagnosis("x", step("calc.check_value", {"value": 6.8}, Bound(metric="value", min=6.5)))
    review = run_qa(intake(), {"part_card": LDO}, only_values, EMPTY_REPORT)
    assert any("공식 계산 0, 시뮬레이션 0, 값 대조 1" in w and "값 대조만으로" in w for w in review.warnings)
    mixed = diagnosis(
        "x",
        step("calc.ldo_thermal_estimate", {}, Bound(metric="tj_c", min=125.0)),
        step("calc.check_value", {"value": 6.8}, Bound(metric="value", min=6.5)),
    )
    review = run_qa(intake(), {"part_card": LDO}, mixed, EMPTY_REPORT)
    assert any("공식 계산 1, 시뮬레이션 0, 값 대조 1" in w and "값 대조만으로" not in w for w in review.warnings)
