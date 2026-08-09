"""Config loading, the rate-limit/backoff helpers, and the CLI's read-only paths.

Nothing here connects to anything. The CLI tests run the real ``main()`` against
a temp state directory, because the commands a worried user reaches for —
``policy panic``, ``tiers``, ``audit`` — are the ones that most need to work.
"""

from __future__ import annotations

import json

import pytest

from jarvis_desktop.__main__ import _default_to_run, main
from jarvis_desktop.config import Config, load_config, normalize_server_url
from jarvis_desktop.ratelimit import Admission, Backoff, CommandGate, TokenBucket


# --- server URLs ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("jarvis.lan", "ws://jarvis.lan/api/websocket"),
        ("jarvis.lan:8080", "ws://jarvis.lan:8080/api/websocket"),
        ("ws://jarvis.lan:8080", "ws://jarvis.lan:8080/api/websocket"),
        ("ws://jarvis.lan:8080/", "ws://jarvis.lan:8080/api/websocket"),
        ("http://jarvis.lan:8080", "ws://jarvis.lan:8080/api/websocket"),
        ("https://jarvis.example:443", "wss://jarvis.example:443/api/websocket"),
        ("wss://jarvis.lan/custom/socket", "wss://jarvis.lan/custom/socket"),
    ],
)
def test_server_urls_are_normalised(raw, expected):
    assert normalize_server_url(raw) == expected


@pytest.mark.parametrize("raw", ["ftp://jarvis.lan", "file:///etc/passwd"])
def test_bad_server_schemes_are_refused(raw):
    with pytest.raises(ValueError):
        normalize_server_url(raw)


def test_the_server_host_is_the_ssrf_exemption():
    config = Config(server_url="ws://jarvis.lan:8080/api/websocket")
    assert config.server_host == "jarvis.lan"


# --- loading ----------------------------------------------------------------


def test_config_file_env_and_overrides_stack(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "server_url": "ws://from-file:8080",
                "device_name": "from-file",
                "file_roots": [str(tmp_path / "ws")],
                "shell": {"enabled": False, "use_shell": True, "timeout_s": 12},
                "input_automation": {"enabled": True},
            }
        )
    )
    config = load_config(
        path,
        env={"JARVIS_STATE_DIR": str(tmp_path / "state"), "JARVIS_DEVICE_NAME": "from-env"},
        overrides={"server_url": "ws://from-flag:9090/api/websocket"},
    )
    assert config.server_url == "ws://from-flag:9090/api/websocket"  # flag beats env beats file
    assert config.device_name == "from-env"
    assert config.shell.enabled is False
    assert config.shell.use_shell is True
    assert config.shell.timeout_s == 12
    assert config.input_automation.enabled is True
    assert config.file_roots[0].name == "ws"


def test_a_missing_config_file_is_not_an_error(tmp_path):
    config = load_config(tmp_path / "nope.json", env={"JARVIS_STATE_DIR": str(tmp_path)})
    assert config.device_id.startswith("desktop-")
    assert config.shell.use_shell is False  # the safe default


def test_a_corrupt_config_file_is_an_error_not_a_silent_default(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ not json")
    with pytest.raises(ValueError):
        load_config(path, env={})


def test_the_device_id_is_stable_across_runs(tmp_path):
    env = {"JARVIS_STATE_DIR": str(tmp_path / "state")}
    first = load_config(tmp_path / "nope.json", env=env)
    second = load_config(tmp_path / "nope.json", env=env)
    assert first.device_id == second.device_id


def test_the_token_can_come_from_a_file(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("  secret-token\n")
    config = load_config(
        tmp_path / "nope.json",
        env={"JARVIS_TOKEN_FILE": str(token_file), "JARVIS_STATE_DIR": str(tmp_path / "s")},
    )
    assert config.token == "secret-token"


def test_capabilities_follow_the_switches(tmp_path):
    from jarvis_desktop.config import InputConfig, ShellConfig

    config = Config(
        state_dir=tmp_path,
        shell=ShellConfig(enabled=False),
        input_automation=InputConfig(enabled=False),
        clipboard_enabled=False,
    )
    caps = config.capabilities()
    assert "shell" not in caps
    assert "ui_automation" not in caps
    assert "clipboard" not in caps
    assert "files" in caps

    loud = Config(
        state_dir=tmp_path,
        shell=ShellConfig(enabled=True),
        input_automation=InputConfig(enabled=True),
    )
    assert {"shell", "ui_automation", "clipboard"} <= set(loud.capabilities())


def test_the_state_dir_is_not_world_readable(tmp_path):
    import os
    import stat

    if os.name == "nt":  # pragma: no cover
        pytest.skip("POSIX permissions only")
    config = Config(state_dir=tmp_path / "state", file_roots=(tmp_path / "ws",)).ensure_dirs()
    assert stat.S_IMODE(config.state_dir.stat().st_mode) & 0o077 == 0


# --- token bucket -----------------------------------------------------------


def test_the_bucket_allows_a_burst_then_refuses():
    bucket = TokenBucket(capacity=5, refill_per_second=1, start=0.0)
    assert all(bucket.try_acquire(0.0) for _ in range(5))
    assert not bucket.try_acquire(0.0)


def test_the_bucket_refills_at_the_configured_rate():
    bucket = TokenBucket(capacity=5, refill_per_second=2, start=0.0)
    for _ in range(5):
        bucket.try_acquire(0.0)
    assert not bucket.try_acquire(0.0)
    assert bucket.try_acquire(0.5)  # one token after half a second
    assert not bucket.try_acquire(0.5)
    assert bucket.peek(3.0) == 5  # capped at capacity


def test_a_clock_that_goes_backwards_does_not_hand_out_a_free_refill():
    bucket = TokenBucket(capacity=3, refill_per_second=1, start=100.0)
    for _ in range(3):
        bucket.try_acquire(100.0)
    assert not bucket.try_acquire(50.0), "time going backwards granted tokens"
    assert not bucket.try_acquire(50.5)


def test_wait_s_reports_the_delay():
    bucket = TokenBucket(capacity=1, refill_per_second=1, start=0.0)
    bucket.try_acquire(0.0)
    assert bucket.wait_s(0.0) == pytest.approx(1.0, abs=0.01)
    assert bucket.wait_s(2.0) == 0.0


def test_a_bucket_needs_a_positive_capacity_and_rate():
    with pytest.raises(ValueError):
        TokenBucket(capacity=0, refill_per_second=1)
    with pytest.raises(ValueError):
        TokenBucket(capacity=1, refill_per_second=0)


# --- backoff ----------------------------------------------------------------


def test_backoff_grows_and_is_capped():
    backoff = Backoff(base_s=1.0, max_s=60.0, factor=2.0)
    assert backoff.ceiling_for(0) == 1.0
    assert backoff.ceiling_for(1) == 2.0
    assert backoff.ceiling_for(3) == 8.0
    assert backoff.ceiling_for(20) == 60.0


def test_backoff_never_returns_zero():
    backoff = Backoff(base_s=1.0, max_s=60.0)
    for attempt in range(10):
        assert backoff.delay_for(attempt, 0.0) >= 1.0


def test_backoff_jitters_within_the_window():
    backoff = Backoff(base_s=1.0, max_s=60.0)
    low = backoff.delay_for(4, 0.0)
    high = backoff.delay_for(4, 0.999)
    assert low == 1.0
    assert high == pytest.approx(16.0, abs=0.1)
    assert low < backoff.delay_for(4, 0.5) < high


def test_a_successful_registration_resets_the_backoff():
    backoff = Backoff()
    for _ in range(5):
        backoff.next(0.5)
    assert backoff.attempt == 5
    backoff.reset()
    assert backoff.attempt == 0


def test_penalise_jumps_straight_to_a_long_delay():
    backoff = Backoff()
    backoff.penalise()
    assert backoff.attempt >= Backoff.PENALTY_ATTEMPT
    assert backoff.delay_for(backoff.attempt, 0.999) > 10


# --- command gate -----------------------------------------------------------


def test_the_gate_admits_once_per_command_id():
    gate = CommandGate()
    assert gate.admit("c-1", "act").accepted
    assert gate.admit("c-1", "act").kind == Admission.STILL_RUNNING
    gate.complete("c-1", {"status": "ok"})
    replay = gate.admit("c-1", "act")
    assert replay.kind == Admission.ALREADY_ANSWERED
    assert replay.reply == {"status": "ok"}


def test_the_gate_refuses_a_second_command_for_a_busy_action():
    gate = CommandGate()
    assert gate.admit("c-1", "type_text").accepted
    busy = gate.admit("c-2", "type_text")
    assert busy.kind == Admission.ACTION_BUSY
    gate.complete("c-1", {})
    assert gate.admit("c-2", "type_text").accepted


def test_the_gate_caps_concurrency():
    gate = CommandGate(max_concurrent=2)
    assert gate.admit("c-1", "a").accepted
    assert gate.admit("c-2", "b").accepted
    assert gate.admit("c-3", "c").kind == Admission.AT_CAPACITY


def test_the_gate_refuses_malformed_frames():
    gate = CommandGate()
    assert gate.admit("", "act").kind == Admission.MALFORMED
    assert gate.admit(None, "act").kind == Admission.MALFORMED
    assert gate.admit("c-1", "").kind == Admission.MALFORMED


def test_the_replay_history_is_bounded():
    gate = CommandGate(history_size=4)
    for index in range(20):
        gate.admit(f"c-{index}", f"a-{index}")
        gate.complete(f"c-{index}", {"n": index})
    assert len(gate._answered) == 4
    assert gate.admit("c-19", "a-19").kind == Admission.ALREADY_ANSWERED
    assert gate.admit("c-0", "a-0").accepted  # forgotten, so it may run again


def test_abandon_allows_a_redelivery_to_run():
    gate = CommandGate()
    gate.admit("c-1", "act")
    gate.abandon("c-1")
    assert gate.admit("c-1", "act").accepted


def test_clear_in_flight_keeps_the_history():
    gate = CommandGate()
    gate.admit("c-1", "act")
    gate.complete("c-1", {"status": "ok"})
    gate.admit("c-2", "act")
    gate.clear_in_flight()
    assert gate.in_flight == 0
    assert gate.admit("c-1", "act").kind == Admission.ALREADY_ANSWERED


# --- the CLI ----------------------------------------------------------------


def test_a_bare_invocation_means_run():
    assert _default_to_run(["--server", "x"]) == ["run", "--server", "x"]
    assert _default_to_run(["run", "--server", "x"]) == ["run", "--server", "x"]
    assert _default_to_run(["tiers"]) == ["tiers"]
    assert _default_to_run(["--help"]) == ["--help"]
    assert _default_to_run(["-v", "--server", "x"]) == ["-v", "run", "--server", "x"]
    assert _default_to_run(["-c", "cfg.json", "--server", "x"]) == [
        "-c",
        "cfg.json",
        "run",
        "--server",
        "x",
    ]


def cli(tmp_path, *args):
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


def test_tiers_prints_the_table(tmp_path, capsys):
    assert cli(tmp_path, "tiers") == 0
    out = capsys.readouterr().out
    assert "run_command" in out
    assert "CONFIRM" in out
    assert "Tier 3 CONFIRM asks every single time" in out


def test_policy_set_list_and_panic(tmp_path, capsys):
    assert cli(tmp_path, "policy", "set", "write_file", "allow_always") == 0
    assert cli(tmp_path, "policy", "list") == 0
    assert "write_file" in capsys.readouterr().out

    assert cli(tmp_path, "policy", "panic", "on") == 0
    assert "panic is now ON" in capsys.readouterr().out
    assert cli(tmp_path, "policy", "panic", "off") == 0


def test_the_cli_refuses_allow_always_for_a_tier_three_action(tmp_path, capsys):
    code = cli(tmp_path, "policy", "set", "run_command", "allow_always")
    assert code == 2
    assert "asks every time" in capsys.readouterr().err
    # ...but blocking it outright is fine.
    assert cli(tmp_path, "policy", "set", "run_command", "never") == 0


def test_the_cli_rejects_an_unknown_action(tmp_path, capsys):
    assert cli(tmp_path, "policy", "set", "not_an_action", "never") == 2
    assert "no such action" in capsys.readouterr().err


def test_audit_reads_an_empty_log(tmp_path, capsys):
    assert cli(tmp_path, "audit") == 0
    assert "nothing recorded yet" in capsys.readouterr().out


def test_cron_previews_a_schedule(tmp_path, capsys):
    assert main(["-q", "cron", "0 9 * * mon-fri", "--count", "3"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 3
    assert all("T09:00" in line for line in lines)


def test_cron_refuses_a_bad_expression(tmp_path, capsys):
    assert main(["-q", "cron", "not a cron"]) == 2
    assert "bad cron expression" in capsys.readouterr().err


def test_doctor_reports_what_works(tmp_path, capsys):
    assert cli(tmp_path, "doctor") == 0
    out = capsys.readouterr().out
    assert "consent backends" in out
    assert "deny-all" in out


def test_run_without_a_token_refuses_to_start(tmp_path, capsys):
    assert cli(tmp_path, "run") == 2


def test_global_flags_work_before_or_after_the_subcommand(tmp_path, capsys):
    """`jarvis-desktop run -q` is what people actually type."""
    assert cli(tmp_path, "tiers") == 0
    before = capsys.readouterr().out
    assert main(["-c", str(tmp_path / "config.json"), "tiers", "-q"]) == 0
    after = capsys.readouterr().out
    assert before == after


def test_verbosity_is_not_lost_when_given_before_the_subcommand(tmp_path):
    parser = __import__(
        "jarvis_desktop.__main__", fromlist=["build_parser"]
    ).build_parser()
    assert parser.parse_args(["-v", "tiers"]).verbose == 1
    assert parser.parse_args(["tiers", "-v"]).verbose == 1
    assert parser.parse_args(["tiers"]).verbose == 0
    assert parser.parse_args(["-q", "audit"]).quiet is True
    assert parser.parse_args(["audit", "-q"]).quiet is True
    assert parser.parse_args(["audit"]).quiet is False
    assert parser.parse_args(["-c", "a.json", "tiers"]).config == "a.json"
    assert parser.parse_args(["tiers", "-c", "a.json"]).config == "a.json"
    assert parser.parse_args(["tiers"]).config is None
