"""명령줄 실행기: 키보드 입력 없이(--decision) 사람 승인 단계를 처리하는지 확인합니다."""

import builtins

import pytest

from fae_agent import cli
from fae_agent.config import PROJECT_ROOT, get_settings

SAMPLE = PROJECT_ROOT / "data" / "samples" / "complaint_ldo_overheating.txt"


def _no_input(*_):
    raise AssertionError("--decision을 줬는데 input()을 불렀습니다")


def _eof(*_):
    raise EOFError


def test_decision_approve_without_keyboard(capsys, monkeypatch):
    monkeypatch.setattr(builtins, "input", _no_input)
    assert cli.main(["run", "--file", str(SAMPLE), "--decision", "a"]) == 0
    out = capsys.readouterr().out
    assert "[--decision] approve" in out
    assert "상태: answered" in out


def test_decision_revise_then_approve(capsys, monkeypatch):
    monkeypatch.setattr(builtins, "input", _no_input)
    assert cli.main(["run", "--file", str(SAMPLE), "--decision", "r,a", "--comment", "더 짧게"]) == 0
    out = capsys.readouterr().out
    assert "[--decision] revise (더 짧게)" in out
    assert "[--decision] approve" in out
    assert "상태: answered" in out


def test_decision_reject(capsys, monkeypatch):
    monkeypatch.setattr(builtins, "input", _no_input)
    assert cli.main(["run", "--file", str(SAMPLE), "--decision", "x"]) == 0
    assert "상태: rejected" in capsys.readouterr().out


def test_decisions_run_out_then_wait(capsys, monkeypatch):
    monkeypatch.setattr(builtins, "input", _no_input)
    assert cli.main(["run", "--file", str(SAMPLE), "--decision", "r"]) == 0
    assert "사람 승인 대기" in capsys.readouterr().out


def test_no_keyboard_stops_with_guide(capsys, monkeypatch):
    monkeypatch.setattr(builtins, "input", _eof)
    assert cli.main(["run", "--file", str(SAMPLE)]) == 0
    out = capsys.readouterr().out
    assert "--decision a" in out
    assert "사람 승인 대기" in out


def test_wrong_decision_code():
    with pytest.raises(SystemExit):
        cli.main(["run", "--file", str(SAMPLE), "--decision", "y"])


def _llm_mode(monkeypatch):
    monkeypatch.setenv("FAE_MODE", "llm")
    get_settings.cache_clear()


def test_llm_mode_refuses_run_and_eval_without_allow_flag(capsys, monkeypatch):
    """요금이 나가는 명령은 --allow-llm이 없으면 그래프를 만들기 전에 거부한다."""
    from fae_agent import graph

    def forbidden(*_, **__):
        raise AssertionError("거부해야 하는데 그래프를 만들었습니다")

    _llm_mode(monkeypatch)
    monkeypatch.setattr(graph, "build_graph", forbidden)
    assert cli.main(["run", "--file", str(SAMPLE), "--decision", "a"]) == 2
    assert cli.main(["run", "--text", "문의"]) == 2
    assert cli.main(["eval"]) == 2
    err = capsys.readouterr().err
    assert err.count("요금이 나갑니다") == 3 and "--allow-llm" in err and "FAE_MODE" in err


def test_llm_mode_runs_when_allowed_and_other_commands_are_not_blocked(monkeypatch):
    _llm_mode(monkeypatch)
    called = []
    monkeypatch.setattr(cli, "cmd_run", lambda args: called.append(args.allow_llm) or 0)
    monkeypatch.setattr(cli, "cmd_eval", lambda args: called.append(args.allow_llm) or 0)
    assert cli.main(["run", "--text", "문의", "--allow-llm"]) == 0
    assert cli.main(["eval", "--allow-llm"]) == 0
    assert called == [True, True]
    assert cli.main(["tickets"]) == 0  # 모델을 부르지 않는 명령은 막지 않는다


def test_mock_mode_needs_no_flag(capsys, monkeypatch):
    monkeypatch.setattr(builtins, "input", _no_input)
    assert cli.main(["run", "--file", str(SAMPLE), "--decision", "a"]) == 0
    assert "요금이 나갑니다" not in capsys.readouterr().err
    assert cli.main(["run", "--file", str(SAMPLE), "--decision", "a", "--allow-llm"]) == 0  # 붙여도 무해
