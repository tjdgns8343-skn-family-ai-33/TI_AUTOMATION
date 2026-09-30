"""Streamlit 화면: 문의 입력 → 에이전트 처리 과정 확인 → 사람 승인.

실행: streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 프로젝트 루트를 import 경로에 추가

from fae_agent.cli import load_complaint  # noqa: E402
from fae_agent.config import get_settings  # noqa: E402
from fae_agent.graph import build_graph, new_thread_id, pending_review, resume_run, start_run  # noqa: E402
from fae_agent.ingest import build_chunks  # noqa: E402
from fae_agent.schemas import Complaint  # noqa: E402
from fae_agent.tools.knowledge import clear_cache  # noqa: E402
from fae_agent.waveform import extract_responses, extract_waveforms  # noqa: E402

STATUS_ICON = {"pass": "✅", "fail": "❌", "skipped": "⏭️", "error": "⚠️"}
WAVE_COLORS = {"결과": "#1f77b4", "변경 전": "#9e9e9e", "변경 후": "#d62728", "목표 출력 전압": "#2ca02c"}


def show_waveforms(outputs: dict) -> None:
    """시뮬레이션 테스트의 출력 전압 파형을 그립니다 (수정 전·후는 한 그래프에 겹쳐서)."""
    waves = extract_waveforms(outputs)
    if not waves:
        return
    base_time = waves[0][1]["time_us"]
    columns = {name: w["v"] for name, w in waves if len(w["v"]) == len(base_time)}
    ref_v = waves[0][1].get("ref_v")
    if ref_v:
        columns["목표 출력 전압"] = [ref_v] * len(base_time)
    frame = pd.DataFrame(columns, index=pd.Index(base_time, name="시간 (µs)"))
    st.line_chart(frame, y_label="출력 전압 (V)", color=[WAVE_COLORS[name] for name in frame.columns])


def show_responses(outputs: dict) -> None:
    """연산증폭기 시뮬레이션의 주파수 응답(이득 dB, 로그 주파수 축)을 그리고 −3 dB 대역폭을 알려 줍니다."""
    for _, response in extract_responses(outputs):
        frame = pd.DataFrame({"주파수 (Hz)": response["freq_hz"], "이득 (dB)": response["gain_db"]})
        chart = (
            alt.Chart(frame)
            .mark_line()
            .encode(
                x=alt.X("주파수 (Hz):Q", scale=alt.Scale(type="log")),
                y=alt.Y("이득 (dB):Q", scale=alt.Scale(zero=False)),
            )
        )
        st.altair_chart(chart, use_container_width=True)
        marks = response.get("marks") or {}
        if marks.get("bw_hz") and marks.get("freq_hz") and marks.get("gain_db_at_freq") is not None:
            gain = 10 ** (marks["gain_db_at_freq"] / 20)
            st.caption(f"−3 dB 대역폭 {marks['bw_hz']:,.0f} Hz · {marks['freq_hz']:,.0f} Hz에서 {gain:.3g}배")


st.set_page_config(page_title="FAE Agent", page_icon="🔧", layout="wide")


@st.cache_resource
def get_graph():
    return build_graph()


settings = get_settings()
graph = get_graph()
samples = sorted((settings.data_dir / "samples").glob("*.txt"))

with st.sidebar:
    st.header("설정")
    st.write(f"모드: **{settings.fae_mode}** · 도구 호출: **{settings.tool_mode}**")
    st.caption(".env에서 FAE_MODE=llm으로 바꾸면 실제 LLM을 씁니다.")
    if st.button("문서 색인 다시 만들기"):
        stats = build_chunks()
        clear_cache()
        st.success(f"문서 {stats['files']}개 → 조각 {stats['chunks']}개")
    if samples:
        choice = st.selectbox("샘플 문의 불러오기", ["(선택 안 함)"] + [p.name for p in samples])
        if choice != "(선택 안 함)" and st.button("불러오기"):
            loaded = load_complaint(settings.data_dir / "samples" / choice)
            st.session_state.update(customer=loaded.customer, subject=loaded.subject, body=loaded.body)

st.title("반도체 고객 컴플레인 처리 에이전트")

customer = st.text_input("고객사", key="customer")
subject = st.text_input("제목", key="subject")
body = st.text_area("문의 내용", height=180, key="body")

if st.button("분석 시작", type="primary", disabled=not body.strip()):
    st.session_state.thread_id = new_thread_id()
    with st.spinner("에이전트가 처리 중입니다..."):
        complaint = Complaint(customer=customer or "고객", subject=subject, body=body)
        st.session_state.result = start_run(graph, complaint, st.session_state.thread_id)

result = st.session_state.get("result")
if result:
    st.subheader("처리 과정")
    st.code("\n".join(result.get("log", [])), language=None)

    intake = result.get("intake") or {}
    with st.expander("1. 접수·분류", expanded=True):
        st.write(
            f"분류: **{intake.get('category')}** → 경로 **{intake.get('route')}** · 긴급도 {intake.get('urgency')}"
        )
        st.write(
            f"부품: {', '.join(intake.get('part_numbers', [])) or '미상'} · 증상: {', '.join(intake.get('symptoms', [])) or '미상'}"
        )
        conditions = {k: v for k, v in (intake.get("conditions") or {}).items() if v is not None}
        if conditions:
            st.json(conditions)
        if intake.get("missing_info"):
            st.warning("추가로 받을 정보: " + ", ".join(intake["missing_info"]))

    research = result.get("research")
    if research:
        with st.expander("2. 기술 조사"):
            for e in research.get("evidence", []):
                st.markdown(f"- **{e['source']}** (점수 {e['score']}): {e['snippet'][:200]}…")
            if research.get("part_card"):
                st.json(research["part_card"])

    diagnosis = result.get("diagnosis")
    if diagnosis:
        with st.expander("3. 원인 분석·해결책", expanded=True):
            for i, h in enumerate(diagnosis.get("hypotheses", []), start=1):
                sources = ", ".join(e["source"] for e in h.get("evidence", [])) or "근거 없음"
                st.markdown(f"**{i}. {h['cause']}** — {h.get('mechanism', '')}  \n근거: {sources}")
            solution = diagnosis.get("solution") or {}
            if solution:
                st.info(solution.get("summary", ""))

    report = result.get("check_report")
    if report:
        with st.expander(f"4. 테스트 — {report.get('status')}", expanded=True):
            for s in report.get("steps", []):
                st.write(f"{STATUS_ICON.get(s['status'], '')} [{s['purpose']}] {s['label']} — {s['notes']}")
                show_waveforms(s.get("outputs") or {})
                show_responses(s.get("outputs") or {})

    qa = result.get("qa")
    if qa:
        with st.expander(
            f"5. 검수 — {'통과' if qa.get('passed') else '미통과'} (재분석 {result.get('qa_retries', 0)}회)"
        ):
            for issue in qa.get("issues", []):
                st.error(issue)
            for warning in qa.get("warnings", []):
                st.warning(warning)

    cpr = result.get("cpr")
    if cpr:
        with st.expander("품질 클레임 점검 (CPR·8D)", expanded=True):
            rating = result.get("rating_check") or {}
            if rating.get("abs_over"):
                for text in rating["abs_over"]:
                    st.warning(f"정격 점검 (알려 준 값 기준): {text}. 사용 조건 문제 가능성이 높아 불량 클레임 신뢰도가 낮습니다.")
            elif rating.get("found"):
                st.success("정격 점검: 알려 준 값은 부품 카드의 절대 최대를 넘지 않습니다. 확인한 항목: " + (", ".join(rating.get("within", [])) or "없음"))
            elif rating:
                st.info("정격 점검을 하지 못했습니다 (부품 카드 없음). 데이터시트로 직접 확인이 필요합니다.")
            for text in rating.get("rec_over", []):
                st.caption(f"권장 범위 밖: {text}")
            for item, ok in cpr["checklist"].items():
                st.write(("✅ " if ok else "⬜ ") + item)
            st.json(cpr["eight_d_draft"])

    review = pending_review(result)
    draft = (review or {}).get("draft") or result.get("response") or {}
    st.subheader("고객 답변 초안")
    draft_key = f"draft_{st.session_state.thread_id}_{len(result.get('log', []))}"  # 수정 후 새 초안이 보이도록
    st.text_area("초안", draft.get("customer_reply", ""), height=320, key=draft_key)
    with st.expander("내부 메모"):
        st.text(draft.get("internal_note", ""))

    if review is not None:
        comment = st.text_input("수정 요청 내용 (수정 요청일 때)")
        col1, col2, col3 = st.columns(3)
        decision = None
        if col1.button("승인", type="primary"):
            decision = ("approve", "")
        if col2.button("수정 요청"):
            decision = ("revise", comment)
        if col3.button("반려"):
            decision = ("reject", "")
        if decision:
            st.session_state.result = resume_run(graph, st.session_state.thread_id, *decision)
            st.rerun()
    else:
        st.success(f"처리 완료 — 티켓 {result.get('ticket_id')} / 상태 {result.get('status')}")
