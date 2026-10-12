"""부품 추천 에이전트(신규 설계 상담): 요구사항(입력 범위, 출력, 전류)을 부품 카드와 수치로 비교해 후보별 통과·탈락 이유를 만든다.

판단은 코드가 한다 (정격, 드롭아웃, LDO 열, 벅의 최대 듀티·최소 온타임, 컨트롤러의 사용 가능 주파수 범위). LLM은 이 결과를 고객이 읽을 문장으로 푸는 일만 한다.
범위: 지금은 전원(LDO·벅) 추천만 한다. 비교 대상은 이 저장소에 부품 카드가 있는 실제 부품뿐이라 TI 전체 제품 검색이 아니다.
"""

from __future__ import annotations

import re
from typing import Any

from ..design_check import design_facts
from ..schemas import Conditions, IntakeResult
from ..tools.gateway import ToolGateway

SUPPORTED_TYPES = ("ldo", "buck")
LDO_DROPOUT_DEFAULT_V = 0.5  # 카드에 드롭아웃이 없을 때의 가정
DEFAULT_TA_C = 25.0  # 주변 온도를 모를 때의 가정
SYNC_BUCK_DCR_OHM = 0.05  # 인덕터 DCR을 모를 때의 가정 (100 % 듀티 드롭아웃 계산)
UNSUPPORTED = re.compile(r"연산\s*증폭|op-?amp|증폭기|충전기|배터리\s*충전|통신|센서", re.IGNORECASE)
# 용도 설명에도 쓰이는 말(센서 노드 등)과 달리, 이 말이 나오면 요구한 부품 종류 자체가 전원(LDO·벅)이 아니다
HARD_UNSUPPORTED = re.compile(r"충전\s*(?:IC|칩|기|회로)|charger|charging\s*ic|연산\s*증폭기|op-?amp", re.IGNORECASE)
PREFER_LDO = ("저잡음", "저노이즈", "low noise")
PREFER_BUCK = ("효율", "고효율")
SCOPE_NOTE = "이 시스템에 정리된 {n}종 부품 안에서만 비교한 결과이며 TI 전체 제품을 검색한 것이 아닙니다. 가격·재고·납기는 포함하지 않습니다."


def _requirements(c: Conditions) -> dict[str, Any]:
    vin_hi = c.vin_max_v or c.vin_v
    vin_lo = c.vin_min_v or c.vin_v
    return {"vin_min_v": vin_lo, "vin_max_v": vin_hi, "vout_v": c.vout_v, "iout_a": c.iout_a, "ta_c": c.ta_c}


def _ldo_dropout(card: dict[str, Any]) -> tuple[float, bool]:
    typical = card.get("typical") or {}
    value = typical.get("dropout_v_max") or typical.get("dropout_v_max_3v3_to_5v")
    return (float(value), False) if value else (LDO_DROPOUT_DEFAULT_V, True)


def _evaluate(card: dict[str, Any], req: dict[str, Any], gateway: ToolGateway, assumptions: list[str]) -> dict[str, Any]:
    typical, recommended, abs_max = card.get("typical") or {}, card.get("recommended") or {}, card.get("abs_max") or {}
    vin_lo, vin_hi, vout, iout = req["vin_min_v"], req["vin_max_v"], req["vout_v"], req["iout_a"]
    fails: list[str] = []
    warnings: list[str] = []
    facts: list[str] = []
    kind = str(card.get("type", "")).lower()
    # 입력 범위와 부하 전류: 데이터시트 한계와 수치 비교
    if recommended.get("vin_min_v") and vin_lo < recommended["vin_min_v"]:
        fails.append(f"최저 입력 {vin_lo:g} V가 권장 최소 {recommended['vin_min_v']:g} V보다 낮음")
    if abs_max.get("vin_v") and vin_hi > abs_max["vin_v"]:
        fails.append(f"최고 입력 {vin_hi:g} V가 절대 최대 {abs_max['vin_v']:g} V를 넘음")
    elif recommended.get("vin_max_v") and vin_hi > recommended["vin_max_v"]:
        fails.append(f"최고 입력 {vin_hi:g} V가 권장 최대 {recommended['vin_max_v']:g} V를 넘음")
    if recommended.get("iout_max_a"):
        if iout > recommended["iout_max_a"]:
            fails.append(f"부하 전류 {iout:g} A가 권장 최대 {recommended['iout_max_a']:g} A보다 큼")
    else:
        warnings.append("카드에 최대 출력 전류가 없음 (외부 FET·인덕터로 정해지므로 설계에서 확인)")
    out_lo = recommended.get("vout_min_v") or typical.get("output_v_min")
    out_hi = recommended.get("vout_max_v") or typical.get("output_v_max")
    if out_lo and out_hi and not out_lo <= vout <= out_hi:  # 입력·전류가 맞아도 장치가 만들 수 있는 출력 범위를 벗어나면 안 된다
        fails.append(f"출력 {vout:g} V가 장치의 출력 범위 {out_lo:g}~{out_hi:g} V를 벗어남")
    step_down = not (vout >= vin_lo and kind in SUPPORTED_TYPES)
    if not step_down:
        fails.append(f"출력 {vout:g} V가 최저 입력 {vin_lo:g} V 이상이라 강압(LDO·벅)으로는 만들 수 없음")
    if kind == "ldo" and step_down:
        dropout, assumed = _ldo_dropout(card)
        if assumed:
            assumptions.append(f"{card['part_number']}: 카드에 드롭아웃이 없어 {LDO_DROPOUT_DEFAULT_V:g} V로 가정")
        if vin_lo - vout < dropout:
            fails.append(f"최저 입력과 출력의 차 {vin_lo - vout:.3g} V가 드롭아웃 약 {dropout:g} V보다 작음")
        theta = (card.get("thermal") or {}).get("theta_ja_c_per_w")
        ta = req["ta_c"] if req["ta_c"] is not None else DEFAULT_TA_C
        if req["ta_c"] is None:
            assumptions.append(f"주변 온도를 몰라 {DEFAULT_TA_C:g} °C로 가정")
        if theta:
            power = (vin_hi - vout) * iout
            tj = ta + power * float(theta)
            limit = recommended.get("tj_max_c", 125)
            facts.append(f"LDO 소모전력 ({vin_hi:g} − {vout:g}) × {iout:g} = {power:.3g} W, 접합 약 {tj:.3g} °C (θJA {theta:g} °C/W, 주변 {ta:g} °C), 효율 {vout / vin_hi * 100:.3g} %")
            if tj > limit:
                fails.append(f"접합 약 {tj:.3g} °C가 권장 {limit:g} °C를 넘음 (소모전력 {power:.3g} W)")
    elif kind == "buck":
        synthetic = IntakeResult(
            category="technical",
            route="fae",
            conditions=Conditions(vin_v=vin_hi, vin_min_v=vin_lo, vout_v=vout, iout_a=iout),
        )
        if step_down:  # 출력이 입력보다 높으면 듀티·온타임 계산이 의미가 없어 건너뜀 (이미 탈락)
            for fact in design_facts(synthetic, card, gateway.call):
                if fact["kind"] not in ("max_duty", "min_on_time"):
                    continue
                if fact["status"] in ("cannot_regulate", "below_typ"):
                    fails.append(fact["text"])
                elif fact["status"] in ("marginal", "uncertain"):
                    warnings.append(fact["text"])
                else:
                    facts.append(fact["text"])
            _controller_window(typical, recommended, vin_lo, vin_hi, vout, fails, facts)
        if typical.get("vin_min_100pct_duty"):  # 동기 벅의 100 % 듀티 드롭아웃: VIN(min) = VOUT + IOUT × (RDS(on) + RL)
            rds = typical.get("rdson_high_ohm_vin3v") or typical.get("rdson_high_ohm")
            if rds:
                needed = vout + iout * (float(rds) + SYNC_BUCK_DCR_OHM)
                assumptions.append(f"{card['part_number']}: 인덕터 DCR {SYNC_BUCK_DCR_OHM:g} Ω 가정")
                text = f"100 % 듀티 드롭아웃: VIN(min) = {vout:g} + {iout:g} × ({float(rds):g} + {SYNC_BUCK_DCR_OHM:g}) = {needed:.3g} V"
                (fails if vin_lo < needed else facts).append(text if vin_lo >= needed else f"{text}이 최저 입력 {vin_lo:g} V보다 높음")
        if step_down and not (typical.get("min_on_time_s") or typical.get("max_duty")):
            warnings.append("카드에 최소 온타임·최대 듀티가 없어 낮은 출력·낮은 입력 동작은 확인하지 못함")
    verdict = "fail" if fails else "caution" if warnings else "ok"
    return {
        "part": card["part_number"],
        "type": kind,
        "verdict": verdict,
        "fails": fails,
        "warnings": warnings,
        "facts": facts,
        "source": card.get("source", ""),
    }


def _freq(hz: float) -> str:
    return f"{hz / 1e6:.3g} MHz" if hz >= 1e6 else f"{hz / 1e3:.3g} kHz"


def _controller_window(typical, recommended, vin_lo, vin_hi, vout, fails, facts) -> None:
    """스위칭 주파수를 저항으로 정하는 컨트롤러(카드에 고정 fsw가 없음): 최소 온타임·최소 오프타임이 허용하는 주파수 범위."""
    t_on, t_off = typical.get("min_on_time_s"), typical.get("min_off_time_s")
    f_lo, f_hi = recommended.get("fsw_min_hz"), recommended.get("fsw_max_hz")
    if typical.get("fsw_hz") or not (t_on and t_off and f_lo and f_hi):
        return
    upper = min(float(f_hi), (vout / vin_hi) / float(t_on), (1 - vout / vin_lo) / float(t_off))
    text = (
        f"사용 가능한 스위칭 주파수는 {_freq(float(f_lo))} 이상 약 {_freq(upper)} 이하 "
        f"(상한 = 최고 입력 {vin_hi:g} V의 최소 온타임 한계 {float(t_on) * 1e9:g} ns, 최저 입력 {vin_lo:g} V의 최소 오프타임 한계 {float(t_off) * 1e9:g} ns, 카드 상한 중 가장 낮은 값)"
    )
    (fails if upper < float(f_lo) else facts).append(f"{text}에서 범위가 없음" if upper < float(f_lo) else text)


def run_recommend(intake: IntakeResult, complaint_text: str, gateway: ToolGateway) -> dict[str, Any]:
    req = _requirements(intake.conditions)
    base = {"requirements": req, "candidates": [], "top": "", "assumptions": [], "missing": []}
    if HARD_UNSUPPORTED.search(complaint_text) or (UNSUPPORTED.search(complaint_text) and not (req["vout_v"] and req["iout_a"])):
        return {**base, "status": "unsupported", "note": "지금은 전원(LDO·벅) 추천만 지원합니다. 다른 종류는 영업·기술지원 담당자가 직접 검토합니다."}
    missing = [label for key, label in (("vin_max_v", "입력 전압(최저·최고)"), ("vout_v", "출력 전압"), ("iout_a", "부하 전류")) if req[key] is None]
    if missing:
        return {**base, "status": "need_info", "missing": missing, "note": "요구사항이 부족해 후보를 비교하지 못했습니다."}
    assumptions: list[str] = []
    names = [n for n in gateway.call("kb.list_parts")["parts"] if not n.startswith("EXAMPLE-")]
    candidates = []
    for name in names:
        card = gateway.call("kb.get_part_card", part_number=name)["card"]
        if card and str(card.get("type", "")).lower() in SUPPORTED_TYPES:
            candidates.append(_evaluate(card, req, gateway, assumptions))
    notes = (intake.request.notes or "") + " " + complaint_text
    prefer = "ldo" if any(w in notes for w in PREFER_LDO) else "buck" if any(w in notes for w in PREFER_BUCK) else ""
    order = {"ok": 0, "caution": 1, "fail": 2}
    candidates.sort(key=lambda c: (order[c["verdict"]], 0 if c["type"] == prefer else 1))
    passing = [c for c in candidates if c["verdict"] != "fail"]
    return {
        **base,
        "status": "ok" if passing else "none",
        "candidates": candidates,
        "top": passing[0]["part"] if passing else "",
        "prefer": prefer,
        "assumptions": list(dict.fromkeys(assumptions)),
        "note": SCOPE_NOTE.format(n=len(candidates)),
    }
