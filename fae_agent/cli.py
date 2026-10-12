"""명령줄 실행기.

python -m fae_agent.cli check                 # 환경 점검
python -m fae_agent.cli ingest                # 문서 색인 다시 만들기
python -m fae_agent.cli run --file data/samples/complaint_buck_oscillation.txt
python -m fae_agent.cli run --file data/samples/complaint_buck_oscillation.txt --decision a   # 입력 없이 승인
python -m fae_agent.cli eval                 # 평가셋 전체 실행 (자동 승인)
python -m fae_agent.cli tickets               # 최근 티켓 목록

FAE_MODE=llm이면 run·eval은 OpenAI 요금이 나가므로 --allow-llm을 붙여야 실행됩니다 (안 붙이면 거부).
요금 없이 확인하려면 환경변수 FAE_MODE=mock으로 실행하세요 (cmd: set FAE_MODE=mock, PowerShell: $env:FAE_MODE="mock").
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import platform
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, get_settings
from .schemas import Complaint

LLM_COST_COMMANDS = ("run", "eval")  # LLM 모드에서 모델을 부르는(요금이 나가는) 명령
LLM_REFUSAL = (
    "FAE_MODE=llm이라서 '{command}' 명령은 LLM(OpenAI) 요금이 나갑니다. 실행하지 않았습니다.\n"
    "요금이 나가도 되면 --allow-llm을 붙여 다시 실행하세요.\n"
    'FAE_MODE=mock으로 실행하면 요금이 나가지 않습니다 (cmd: set FAE_MODE=mock, PowerShell: $env:FAE_MODE="mock").'
)


def _utf8_console() -> None:
    """Windows 콘솔·리디렉션에서도 한국어가 깨지지 않게 UTF-8로 맞춥니다."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")


def load_complaint(path: Path) -> Complaint:
    """문의 파일을 읽습니다. .json은 Complaint 필드, .txt는 '키: 값' 머리말 + '---' + 본문."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return Complaint.model_validate_json(text)
    header, _, body = text.partition("\n---\n")
    if not body:
        return Complaint(body=text.strip())
    fields: dict[str, Any] = {}
    keys = {"고객": "customer", "customer": "customer", "제목": "subject", "subject": "subject", "첨부": "attachments"}
    for line in header.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() in keys:
            name = keys[key.strip().lower()]
            fields[name] = (
                [v.strip() for v in value.split(",") if v.strip()] if name == "attachments" else value.strip()
            )
    return Complaint(body=body.strip(), **fields)


def cmd_check(_: argparse.Namespace) -> int:
    from .tools.spice import find_ngspice

    settings = get_settings()
    print(f"Python {platform.python_version()} ({sys.executable})")
    ok = True
    packages = {
        "langgraph": "langgraph",
        "langchain": "langchain",
        "langchain_openai": "langchain-openai",
        "mcp": "mcp",
        "pydantic": "pydantic",
        "pydantic_settings": "pydantic-settings",
        "sklearn": "scikit-learn",
        "pdfplumber": "pdfplumber",
        "yaml": "PyYAML",
        "streamlit": "streamlit",
    }
    for module, dist in packages.items():
        try:
            importlib.import_module(module)
            print(f"  [OK] {dist} {metadata.version(dist)}")
        except ImportError:
            print(f"  [없음] {dist}  → pip install -r requirements.txt")
            ok = False
        except metadata.PackageNotFoundError:
            print(f"  [OK] {dist}")
    try:
        importlib.import_module("langchain.mcp")
        print("  [OK] langchain.mcp (TOOL_MODE=mcp 사용 가능)")
    except ImportError:
        print("  [참고] langchain.mcp 없음 — TOOL_MODE=local로는 문제없음")
    print(f".env: {'있음' if (PROJECT_ROOT / '.env').exists() else '없음 (.env.example을 복사하세요)'}")
    print(f"FAE_MODE={settings.fae_mode}  TOOL_MODE={settings.tool_mode}  AUTO_APPROVE={settings.auto_approve}")
    if settings.fae_mode == "llm":
        print("  [주의] llm 모드: run·eval은 요금이 나가므로 --allow-llm을 붙여야 실행됩니다")
        provider = settings.llm_model.split(":", 1)[0]
        key_name = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(provider)
        has_key = bool(key_name and os.environ.get(key_name))
        print(f"LLM_MODEL={settings.llm_model}  {key_name or 'API 키'}: {'있음' if has_key else '없음'}")
        ok = ok and has_key
    print(f"ngspice: {find_ngspice() or '없음 (시뮬레이션 테스트는 생략됩니다)'}")
    print(f"문서 폴더: {', '.join(str(p) for p in settings.docs_dirs)}")
    print(
        f"부품 카드: {len(list(settings.parts_dir.glob('*.y*ml')))}개, 사례 파일: {len(list(settings.cases_dir.glob('*.jsonl')))}개"
    )
    print("점검 결과: " + ("준비 완료" if ok else "빠진 항목을 설치·설정하세요"))
    return 0 if ok else 1


def cmd_ingest(_: argparse.Namespace) -> int:
    from .ingest import build_chunks
    from .tools.knowledge import clear_cache

    stats = build_chunks()
    clear_cache()
    print(f"문서 {stats['files']}개 → 조각 {stats['chunks']}개 저장: {stats['path']}")
    for error in stats["errors"]:
        print(f"  [오류] {error}")
    return 0


def _print_result(result: dict[str, Any]) -> None:
    for line in result.get("log", []):
        print(line)
    response = result.get("response") or {}
    if response:
        print("\n----- 고객 답변 초안 -----")
        print(response.get("customer_reply", ""))
        print("\n----- 내부 메모 -----")
        print(response.get("internal_note", ""))


DECISIONS = {"a": "approve", "r": "revise", "x": "reject"}


def _parse_decisions(value: str | None) -> list[str] | None:
    """--decision 값("a", "r,a" 등)을 [a, r, x] 목록으로 바꿉니다. 옵션이 없으면 None."""
    if not value:
        return None
    codes = [code.strip().lower() for code in value.split(",") if code.strip()]
    wrong = [code for code in codes if code not in DECISIONS]
    if wrong:
        raise SystemExit(f"--decision에는 a, r, x만 쓸 수 있습니다 (받은 값: {', '.join(wrong)})")
    return codes


def _ask_decision() -> tuple[str, str] | None:
    """키보드로 승인 여부를 묻습니다. 입력을 받을 수 없는 환경(Claude Code, CI 등)이면 None."""
    try:
        while True:
            choice = input("\n승인(a) / 수정 요청(r) / 반려(x) 중 선택: ").strip().lower()
            if choice in DECISIONS:
                break
            print("a, r, x 중 하나를 입력하세요.")
        comment = input("수정 요청 내용: ").strip() if choice == "r" else ""
    except EOFError:
        return None
    return DECISIONS[choice], comment


def cmd_run(args: argparse.Namespace) -> int:
    from .graph import build_graph, new_thread_id, pending_review, resume_run, start_run

    decisions = _parse_decisions(args.decision)
    complaint = load_complaint(Path(args.file)) if args.file else Complaint(body=args.text)
    graph = build_graph()
    thread_id = new_thread_id()
    result = start_run(graph, complaint, thread_id)
    printed = 0
    waiting = False
    while True:
        logs = result.get("log", [])
        for line in logs[printed:]:
            print(line)
        printed = len(logs)
        review = pending_review(result)
        if review is None:
            break
        draft = review.get("draft") or {}
        print("\n----- 고객 답변 초안 -----\n" + draft.get("customer_reply", ""))
        print("\n----- 내부 메모 -----\n" + draft.get("internal_note", ""))
        if decisions is not None:  # --decision: 입력 없이 정해진 순서대로 처리
            if not decisions:
                print("\n--decision에 준 결정을 모두 썼습니다. 사람 승인 대기 상태로 끝냅니다.")
                waiting = True
                break
            code = decisions.pop(0)
            action, comment = DECISIONS[code], (args.comment if code == "r" else "")
            print(f"\n[--decision] {action}" + (f" ({comment})" if comment else ""))
        else:
            answer = _ask_decision()
            if answer is None:
                print(
                    "\n키보드 입력을 받을 수 없는 환경입니다. 사람 승인 대기 상태로 끝냅니다.\n"
                    "결정을 옵션으로 주고 다시 실행하세요: --decision a (승인) / r,a (수정 요청 후 승인) / x (반려)"
                )
                waiting = True
                break
            action, comment = answer
        result = resume_run(graph, thread_id, action, comment)
    status = "사람 승인 대기 (이 실행을 끝내면 이어서 할 수 없음)" if waiting else result.get("status")
    print(f"\n티켓: {result.get('ticket_id')} / 상태: {status}")
    documents = result.get("documents")
    if documents:
        listed = ", ".join(documents.get("files", [])) or "; ".join(documents.get("issues", []))
        print(f"문서: {documents['status']} {documents.get('dir', '')} {listed}".rstrip())
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"결과 저장: {args.json}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """평가셋을 자동 승인 모드로 돌려 분류 정확도와 원인 적중률을 계산합니다."""
    os.environ["AUTO_APPROVE"] = "true"
    get_settings.cache_clear()
    from .graph import build_graph, new_thread_id, start_run

    path = Path(args.cases) if args.cases else get_settings().eval_path
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    graph = build_graph()
    rows, category_hits, cause_hits, cause_total = [], 0, 0, 0
    for case in cases:
        complaint = Complaint(customer=case.get("customer", "평가"), subject=case.get("subject", ""), body=case["body"])
        result = start_run(graph, complaint, new_thread_id())
        category = result["intake"]["category"]
        category_ok = category == case["expected_category"]
        category_hits += category_ok
        causes = " ".join(h["cause"] for h in (result.get("diagnosis") or {}).get("hypotheses", []))
        keywords = case.get("expected_cause_keywords", [])
        cause_ok = None
        if keywords:
            cause_total += 1
            cause_ok = any(k in causes for k in keywords)
            cause_hits += cause_ok
        rows.append(
            {
                "id": case["id"],
                "category": category,
                "category_ok": category_ok,
                "cause_hit": cause_ok,
                "qa_passed": (result.get("qa") or {}).get("passed"),
                "qa_retries": result.get("qa_retries", 0),
                "tests": (result.get("check_report") or {}).get("status"),
            }
        )
        print(
            f"{case['id']:<10} 분류 {category:<14} {'O' if category_ok else 'X'}  원인 {'-' if cause_ok is None else ('O' if cause_ok else 'X')}  재분석 {rows[-1]['qa_retries']}회"
        )
    print(f"\n분류 정확도: {category_hits}/{len(cases)}")
    if cause_total:
        print(f"원인 Top-3 적중: {cause_hits}/{cause_total}")
    out = get_settings().data_dir / "processed" / "eval_results.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"결과 저장: {out}")
    return 0


def cmd_tickets(args: argparse.Namespace) -> int:
    from .tools.tickets import get_ticket, list_tickets

    if args.id:
        print(json.dumps(get_ticket(args.id), ensure_ascii=False, indent=2))
    else:
        for t in list_tickets(args.limit)["tickets"]:
            print(f"{t['id']}  {t['created_at']}  {t['status']:<12} {t['category'] or '-':<14} {t['subject']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(prog="fae", description="반도체 고객 컴플레인 처리 에이전트")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="환경 점검").set_defaults(func=cmd_check)
    sub.add_parser("ingest", help="문서 색인 만들기").set_defaults(func=cmd_ingest)
    run = sub.add_parser("run", help="문의 한 건 처리")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="문의 파일 (.txt 또는 .json)")
    source.add_argument("--text", help="문의 본문")
    run.add_argument("--json", help="전체 결과를 저장할 JSON 경로")
    run.add_argument(
        "--decision",
        help="사람 승인을 입력 없이 처리: a=승인, r=수정 요청, x=반려. 여러 번이면 쉼표 (예: r,a). "
        "Claude Code처럼 키보드 입력을 받을 수 없는 곳에서 씁니다",
    )
    run.add_argument("--comment", default="", help="--decision의 r(수정 요청)에 쓸 내용")
    run.add_argument("--allow-llm", action="store_true", help="FAE_MODE=llm에서 요금이 나가는 것을 허용 (없으면 거부)")
    run.set_defaults(func=cmd_run)
    ev = sub.add_parser("eval", help="평가셋 실행")
    ev.add_argument("--cases", help="평가셋 JSONL (기본: data/eval/eval_cases.jsonl)")
    ev.add_argument("--allow-llm", action="store_true", help="FAE_MODE=llm에서 요금이 나가는 것을 허용 (없으면 거부)")
    ev.set_defaults(func=cmd_eval)
    tk = sub.add_parser("tickets", help="티켓 조회")
    tk.add_argument("--id", help="티켓 ID")
    tk.add_argument("--limit", type=int, default=20)
    tk.set_defaults(func=cmd_tickets)
    args = parser.parse_args(argv)
    if args.command in LLM_COST_COMMANDS and not args.allow_llm and get_settings().fae_mode == "llm":
        print(LLM_REFUSAL.format(command=args.command), file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
