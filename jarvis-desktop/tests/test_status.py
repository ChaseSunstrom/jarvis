"""The status surface: is the agent up, and is it talking to the server?

Nothing here starts an agent. What is pinned is the contract between the two
halves — the daemon writes one small file, a second terminal reads it — and the
one property that makes the answer trustworthy: **a file is not a running
process.** An agent that is killed cannot clean up after itself, so a status
file on its own means "an agent ran here". Staleness and the pid probe are what
turn that into "an agent is running here", and a status command that cannot tell
the difference is worse than no status command, because it is confidently wrong
exactly when something has gone wrong.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time

import pytest

from jarvis_desktop.__main__ import main
from jarvis_desktop.audit import AuditEntry, AuditLog
from jarvis_desktop.policy import ActionTier, Decision
from jarvis_desktop.status import (
    STATUS_INTERVAL_S,
    StatusFile,
    StatusSnapshot,
    StatusWriter,
    process_alive,
    render,
)


def snapshot(**overrides) -> StatusSnapshot:
    base = dict(
        pid=os.getpid(),
        version="0.1.0",
        device_id="desktop-test",
        device_name="workshop",
        server_url="ws://jarvis.lan:8080/api/websocket",
        consent_backend="tk-dialog",
        action_count=21,
        connected=True,
        started_at=time.time() - 90,
        updated_at=time.time(),
    )
    base.update(overrides)
    return StatusSnapshot(**base)


# --- the file ---------------------------------------------------------------


def test_a_snapshot_survives_a_round_trip(tmp_path):
    store = StatusFile(tmp_path / "status.json")
    assert store.write(snapshot()) is True
    read = store.read()
    assert read is not None
    assert read.device_name == "workshop"
    assert read.connected is True
    assert read.action_count == 21
    assert read.consent_backend == "tk-dialog"


def test_a_missing_file_reads_as_no_agent(tmp_path):
    assert StatusFile(tmp_path / "nothing.json").read() is None


def test_a_corrupt_file_reads_as_no_agent_rather_than_raising(tmp_path):
    """`status` is what someone runs when something is already wrong. A
    traceback there answers nothing."""
    path = tmp_path / "status.json"
    path.write_text("{ not json")
    assert StatusFile(path).read() is None
    path.write_text('"a string, not an object"')
    assert StatusFile(path).read() is None


def test_unknown_and_missing_fields_degrade_instead_of_crashing(tmp_path):
    """The file is written by one version of the agent and read by whichever
    one the user happens to invoke."""
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"pid": "not a number", "future_field": {"x": 1}}))
    read = StatusFile(path).read()
    assert read is not None
    assert read.pid == 0
    assert read.device_name == ""


def test_the_file_is_not_world_readable(tmp_path):
    if os.name == "nt":  # pragma: no cover - POSIX permissions only
        pytest.skip("POSIX permissions only")
    import stat

    path = tmp_path / "status.json"
    StatusFile(path).write(snapshot())
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def test_an_unwritable_state_dir_is_reported_not_raised(tmp_path):
    """Telemetry about the agent must never be able to stop the agent."""
    blocked = tmp_path / "file-not-a-dir"
    blocked.write_text("in the way")
    assert StatusFile(blocked / "status.json").write(snapshot()) is False


# --- a file is not a process ------------------------------------------------


def test_a_fresh_snapshot_is_not_stale():
    assert snapshot(updated_at=time.time()).stale() is False


def test_a_snapshot_nobody_has_refreshed_is_stale():
    old = snapshot(updated_at=time.time() - 600, interval_s=STATUS_INTERVAL_S)
    assert old.stale() is True
    assert old.age_s() > 500


def test_staleness_allows_a_few_missed_ticks():
    """One slow poll — a laptop coming back from suspend, a loaded box — is not
    a dead agent."""
    assert snapshot(updated_at=time.time() - STATUS_INTERVAL_S).stale() is False


def test_the_pid_probe_knows_this_process_is_alive():
    if os.name == "nt":  # pragma: no cover
        pytest.skip("the probe is POSIX-only by design; see status.process_alive")
    assert process_alive(os.getpid()) is True


def a_reaped_pid() -> int:
    """A pid that is certainly gone: a child we started and waited for."""
    child = subprocess.Popen([sys.executable, "-c", ""])
    child.wait()
    return child.pid


def test_the_pid_probe_knows_a_finished_process_is_gone():
    if os.name == "nt":  # pragma: no cover
        pytest.skip("the probe is POSIX-only by design; see status.process_alive")
    assert process_alive(a_reaped_pid()) is False


def test_the_pid_probe_refuses_nonsense_rather_than_guessing():
    assert process_alive(0) is None
    assert process_alive(-1) is None


# --- what it says -----------------------------------------------------------


def audit_entry(**overrides) -> AuditEntry:
    base = dict(
        action_id="run_command",
        tier=ActionTier.CONFIRM,
        decision=Decision.DENY,
        status="denied",
        ok=False,
        timestamp=time.time(),
    )
    base.update(overrides)
    return AuditEntry(**base)


def test_no_status_file_says_no_agent_and_how_to_start_one(tmp_path):
    text = render(None, [], path=tmp_path / "status.json")
    assert "no agent is running" in text
    assert "jarvis_desktop run" in text


def test_a_live_agent_reports_its_connection_not_just_its_existence():
    """"Up" and "talking to the server" are different states, and reporting the
    second as the first is how people end up debugging the wrong end."""
    assert "running, connected" in render(snapshot(connected=True))
    text = render(snapshot(connected=False))
    assert "NOT connected" in text


def test_a_stale_file_is_never_reported_as_a_running_agent():
    text = render(snapshot(updated_at=time.time() - 3600))
    assert "STALE" in text
    assert "running, connected" not in text


def test_a_file_left_behind_by_a_dead_process_says_so():
    if os.name == "nt":  # pragma: no cover
        pytest.skip("the pid probe is POSIX-only by design")
    text = render(snapshot(pid=a_reaped_pid(), updated_at=time.time()))
    assert "NOT RUNNING" in text


def test_the_kill_switches_are_reported_because_they_explain_everything_else():
    """A user whose commands are all being denied needs to see PANIC here, not
    go looking for it in a different subcommand."""
    text = render(snapshot(), panic=True, automation_enabled=False)
    assert "PANIC IS ON" in text
    assert "automation is switched off" in text


def test_recent_actions_are_listed_with_what_happened_to_them():
    text = render(
        snapshot(),
        [audit_entry(action_id="write_file", status="ok"), audit_entry()],
    )
    assert "write_file" in text
    assert "run_command" in text
    assert "denied" in text


def test_nothing_having_run_is_said_rather_than_shown_as_a_blank():
    assert "nothing has run yet" in render(snapshot(), [])


# --- the daemon's half ------------------------------------------------------


async def test_the_writer_stamps_the_live_pid_and_a_fresh_time(tmp_path):
    path = tmp_path / "status.json"
    writer = StatusWriter(path, lambda: StatusSnapshot(device_name="workshop"))

    assert writer.tick() is True

    stored = StatusFile(path).read()
    assert stored is not None
    assert stored.device_name == "workshop"
    assert stored.pid == os.getpid()
    assert stored.stale() is False
    assert stored.uptime_s() >= 0.0


async def test_a_probe_that_raises_does_not_take_the_agent_down(tmp_path):
    def explode() -> StatusSnapshot:
        raise RuntimeError("the channel is gone")

    writer = StatusWriter(tmp_path / "status.json", explode)
    assert writer.tick() is False
    assert writer.writes == 0


async def test_the_writer_keeps_the_file_fresh_while_it_runs(tmp_path):
    path = tmp_path / "status.json"
    connected = [False]
    writer = StatusWriter(
        path,
        lambda: StatusSnapshot(connected=connected[0]),
        interval_s=1.0,
    )
    await writer.start()
    try:
        for _ in range(100):
            await asyncio.sleep(0.01)
            if writer.writes:
                break
        assert StatusFile(path).read().connected is False

        # ...and it follows the agent's state rather than freezing at startup.
        connected[0] = True
        writer.tick()
        assert StatusFile(path).read().connected is True
    finally:
        await writer.stop()


async def test_a_clean_shutdown_leaves_no_file_claiming_the_agent_is_up(tmp_path):
    path = tmp_path / "status.json"
    writer = StatusWriter(path, lambda: StatusSnapshot(), interval_s=1.0)
    await writer.start()
    for _ in range(100):
        await asyncio.sleep(0.01)
        if path.exists():
            break
    assert path.exists()

    await writer.stop()
    assert not path.exists(), "a stopped agent left a status file behind"


# --- the CLI ----------------------------------------------------------------


def cli(tmp_path, *args) -> int:
    config = tmp_path / "config.json"
    if not config.exists():
        config.write_text(
            json.dumps(
                {
                    "state_dir": str(tmp_path / "state"),
                    "file_roots": [str(tmp_path / "ws")],
                    "server_url": "ws://jarvis.lan:8080",
                }
            )
        )
    return main(["-c", str(config), "-q", *args])


def write_status(tmp_path, **overrides) -> None:
    StatusFile(tmp_path / "state" / "status.json").write(snapshot(**overrides))


def test_status_with_no_agent_exits_nonzero(tmp_path, capsys):
    """So it can be used as a health check, which is why people want it."""
    assert cli(tmp_path, "status") == 1
    assert "no agent is running" in capsys.readouterr().out


def test_status_reports_a_running_agent_and_exits_zero(tmp_path, capsys):
    write_status(tmp_path)
    assert cli(tmp_path, "status") == 0
    out = capsys.readouterr().out
    assert "running, connected" in out
    assert "workshop" in out
    assert "tk-dialog" in out


def test_status_treats_a_stale_file_as_no_agent(tmp_path, capsys):
    write_status(tmp_path, updated_at=time.time() - 3600)
    assert cli(tmp_path, "status") == 1
    assert "STALE" in capsys.readouterr().out


def test_status_shows_what_was_just_refused(tmp_path, capsys):
    write_status(tmp_path)
    AuditLog(tmp_path / "state" / "audit.jsonl").record(
        audit_entry(action_id="run_command", status="denied")
    )
    assert cli(tmp_path, "status") == 0
    out = capsys.readouterr().out
    assert "run_command" in out
    assert "denied" in out


def test_status_json_is_machine_readable(tmp_path, capsys):
    write_status(tmp_path)
    assert cli(tmp_path, "status", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is True
    assert payload["status"]["device_name"] == "workshop"
    assert payload["recent"] == []


def test_status_json_says_so_when_there_is_no_agent(tmp_path, capsys):
    assert cli(tmp_path, "status", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is False
    assert payload["status"] is None


def test_watch_re_reads_the_file_rather_than_printing_one_snapshot_twice(
    tmp_path, capsys, monkeypatch
):
    """The whole point of --watch is that it follows the agent. Driven by
    replacing the sleep, so the loop under test is the real one."""
    write_status(tmp_path, connected=False)

    rounds = [0]

    def fake_sleep(_seconds: float) -> None:
        rounds[0] += 1
        if rounds[0] == 1:
            write_status(tmp_path, connected=True)  # the agent connects
        else:
            raise KeyboardInterrupt  # ...and the user hits ^C

    monkeypatch.setattr(time, "sleep", fake_sleep)
    assert cli(tmp_path, "status", "--watch") == 0

    out = capsys.readouterr().out
    assert "NOT connected" in out
    assert "running, connected" in out


def test_doctor_reports_whether_an_agent_is_running(tmp_path, capsys):
    """`doctor` is about the machine and `status` is about the process, but the
    first question anybody has in front of doctor's output is which one they
    are looking at."""
    assert cli(tmp_path, "doctor") == 0
    assert "no agent running" in capsys.readouterr().out

    write_status(tmp_path)
    assert cli(tmp_path, "doctor") == 0
    assert f"pid {os.getpid()}" in capsys.readouterr().out
