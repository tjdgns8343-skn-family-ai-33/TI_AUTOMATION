"""명령줄 실행기: 키보드 입력 없이(--decision) 사람 승인 단계를 처리하는지 확인합니다."""

import builtins

import pytest

from fae_agent import cli
from fae_agent.config import PROJECT_ROOT

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
