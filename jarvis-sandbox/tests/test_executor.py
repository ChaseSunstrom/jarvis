"""Unit tests for the sandbox executor's job logic (run in-container tests
of the real isolation happen on hardware via scripts/egress-audit.sh)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import executor  # noqa: E402


def test_clamp_timeout():
    assert executor.clamp_timeout(None) == executor.DEFAULT_TIMEOUT
    assert executor.clamp_timeout("nan") == executor.DEFAULT_TIMEOUT
    assert executor.clamp_timeout(5) == 5
    assert executor.clamp_timeout(0) == 1
    assert executor.clamp_timeout(99999) == executor.MAX_TIMEOUT


def test_run_job_captures_output(tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "WORKSPACE", tmp_path)
    res = executor.run_job({"id": "t1", "command": "echo out; echo err >&2; exit 3"})
    assert res["exit_code"] == 3
    assert res["stdout"] == "out\n"
    assert res["stderr"] == "err\n"
    assert res["timed_out"] is False


def test_run_job_times_out_and_kills_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "WORKSPACE", tmp_path)
    res = executor.run_job({"id": "t2", "command": "sleep 30", "timeout": 1})
    assert res["timed_out"] is True
    assert res["exit_code"] == -9
    assert res["duration_s"] < 5


def test_output_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "WORKSPACE", tmp_path)
    res = executor.run_job(
        {"id": "t3", "command": "yes A | head -c 200000", "timeout": 20}
    )
    assert len(res["stdout"]) <= executor.MAX_OUTPUT


def test_write_result_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "RESULTS", tmp_path / "results")
    executor.write_result({"id": "t4", "exit_code": 0, "stdout": "", "stderr": ""})
    files = list((tmp_path / "results").glob("*.json"))
    assert [f.name for f in files] == ["t4.json"]
    assert json.loads(files[0].read_text())["id"] == "t4"


def test_env_is_minimal(tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "WORKSPACE", tmp_path)
    res = executor.run_job({"id": "t5", "command": "env | sort"})
    env_lines = dict(
        line.split("=", 1) for line in res["stdout"].splitlines() if "=" in line
    )
    # no secrets, no proxy vars, nothing inherited from the host
    assert set(env_lines) <= {"PATH", "HOME", "TMPDIR", "LANG", "PWD", "SHLVL", "_"}
