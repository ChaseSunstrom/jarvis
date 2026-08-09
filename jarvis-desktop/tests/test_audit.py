"""The audit log: redaction, rotation, and the promise that it never breaks a dispatch.

The log is the one place every parameter Jarvis ever acted on is written down,
so the redaction test is really a leak test. Over-redaction is fine;
under-redaction is a logged one-time code.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from jarvis_desktop.audit import AuditEntry, AuditLog, Redactor, redact_params, summarize
from jarvis_desktop.policy import ActionTier, Decision


def entry(**kwargs):
    base = dict(
        action_id="run_command",
        tier=ActionTier.CONFIRM,
        decision=Decision.ASK,
        status="ok",
        ok=True,
    )
    base.update(kwargs)
    return AuditEntry(**base)


# --- which keys are secrets -------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "access_token",
        "accessToken",
        "refresh_token",
        "password",
        "passwd",
        "pass",
        "passphrase",
        "pin",
        "sim_pin",
        "pinCode",
        "otp",
        "otp_code",
        "secret",
        "client_secret",
        "api_key",
        "apiKey",
        "APIKEY",
        "authorization",
        "Authorization",
        "cookie",
        "session_id",
        "credential",
        "credentials",
        "private_key",
        "privateKey",
        "aws_access_key_id",
        "cvv",
        "iban",
        "ssn",
        "mnemonic",
        "seed",
        "signature",
        "bearer",
        "nonce",
    ],
)
def test_secret_keys_are_recognised(key):
    assert Redactor.is_secret_key(key), f"{key} was not treated as a secret"


@pytest.mark.parametrize(
    "key",
    [
        "url",
        "path",
        "message",
        "title",
        "command",
        "spinner",
        "keyboard",
        "passenger",
        "level",
        "app",
        "filename",
        "recursive",
        "encoding",
        "x",
        "",
    ],
)
def test_ordinary_keys_are_not_treated_as_secrets(key):
    assert not Redactor.is_secret_key(key), f"{key} was wrongly redacted"


def test_camel_case_keys_are_tokenized():
    assert Redactor.tokenize("apiKey") == ["api", "key"]
    assert Redactor.tokenize("sim_pin") == ["sim", "pin"]
    assert Redactor.tokenize("HTTPToken") == ["httptoken"]
    assert Redactor.tokenize("a.b-c/d e") == ["a", "b", "c", "d", "e"]


# --- redaction of whole payloads --------------------------------------------


def test_secrets_are_masked_and_ordinary_values_survive():
    redacted = redact_params(
        {"url": "https://example.com", "token": "sk-live-abcdef", "count": 3, "ok": True}
    )
    assert redacted["token"] == Redactor.MASK
    assert redacted["url"] == "https://example.com"
    assert redacted["count"] == 3
    assert redacted["ok"] is True


def test_nested_secrets_are_masked():
    redacted = redact_params(
        {"headers": {"Authorization": "Bearer sk-live-1", "Accept": "application/json"}}
    )
    assert redacted["headers"]["Authorization"] == Redactor.MASK
    assert redacted["headers"]["Accept"] == "application/json"


def test_a_secret_list_is_masked_whole():
    """A list under a secret key is replaced outright — masking element by
    element would still leak the count and the shape."""
    redacted = redact_params({"tokens": ["a", "b", "c"], "names": ["x", "y"]})
    assert redacted["tokens"] == Redactor.MASK
    assert redacted["names"] == ["x", "y"]


def test_a_secret_nested_under_an_ordinary_list_is_masked():
    redacted = redact_params({"steps": [{"cmd": "ls"}, {"password": "hunter2"}]})
    assert redacted["steps"][0]["cmd"] == "ls"
    assert redacted["steps"][1]["password"] == Redactor.MASK


def test_long_values_are_truncated_not_dropped():
    redacted = redact_params({"body": "y" * 5000})
    assert len(redacted["body"]) < 400
    assert "(+4744 chars)" in redacted["body"]


def test_long_lists_are_summarised():
    redacted = redact_params({"items": list(range(100))})
    assert len(redacted["items"]) == Redactor.MAX_ARRAY_ITEMS + 1
    assert "+80 items" in redacted["items"][-1]


def test_deeply_nested_payloads_terminate():
    deep: dict = {"a": {}}
    node = deep["a"]
    for _ in range(50):
        node["a"] = {}
        node = node["a"]
    node["token"] = "leak"
    text = json.dumps(redact_params(deep))
    assert "leak" not in text


def test_redaction_does_not_mutate_the_original():
    params = {"token": "sk-live-1", "nested": {"password": "hunter2"}}
    redact_params(params)
    assert params["token"] == "sk-live-1"
    assert params["nested"]["password"] == "hunter2"


# --- what actually lands on disk --------------------------------------------


def test_a_written_entry_contains_no_secret(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record(
        entry(
            action_id="http_request",
            params={
                "url": "https://api.example.com/v1/x",
                "headers": {"Authorization": "Bearer sk-live-SUPERSECRET"},
                "body": "password=hunter2",
            },
        )
    )
    raw = (tmp_path / "audit.jsonl").read_text()
    assert "sk-live-SUPERSECRET" not in raw
    assert Redactor.MASK in raw
    # ...but the action and the URL are still there, or the log is useless.
    assert "http_request" in raw
    assert "api.example.com" in raw


def test_the_error_string_is_truncated(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record(entry(ok=False, status="error", error="z" * 5000))
    line = json.loads((tmp_path / "audit.jsonl").read_text().strip())
    assert len(line["error"]) < 400


def test_entries_round_trip(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record(
        entry(
            action_id="write_file",
            tier=ActionTier.NOTIFY,
            decision=Decision.ALLOW,
            command_id="c-1",
            note="policy=ALLOW_ALWAYS",
            duration_ms=12,
            source="server",
        )
    )
    (read,) = log.read()
    assert read.action_id == "write_file"
    assert read.tier == ActionTier.NOTIFY
    assert read.decision == Decision.ALLOW
    assert read.command_id == "c-1"
    assert read.duration_ms == 12


def test_read_is_newest_first(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    for index in range(5):
        log.record(entry(action_id=f"action-{index}"))
    assert [e.action_id for e in log.read()] == [f"action-{i}" for i in reversed(range(5))]
    assert [e.action_id for e in log.read(newest_first=False)][0] == "action-0"
    assert len(log.read(limit=2)) == 2


def test_a_corrupt_line_costs_one_entry_not_the_file(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.record(entry(action_id="first"))
    with path.open("a") as fh:
        fh.write("{this is not json\n")
    log.record(entry(action_id="third"))
    ids = [e.action_id for e in log.read()]
    assert ids == ["third", "first"]


# --- rotation ---------------------------------------------------------------


def test_the_live_file_is_compacted_at_the_entry_cap(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, max_entries=50)
    for index in range(400):
        log.record(entry(action_id=f"a{index}"))
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) <= 50 + AuditLog._ROTATE_SLACK
    # The newest entries survived; the oldest were dropped.
    assert "a399" in lines[-1]


def test_the_file_rotates_at_the_size_cap(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, max_entries=100000, max_bytes=4096, keep_rotations=2)
    for index in range(300):
        log.record(entry(action_id=f"a{index}", params={"filler": "x" * 200}))
    assert path.exists()
    assert path.with_suffix(path.suffix + ".1").exists()
    # Nothing beyond keep_rotations is kept.
    assert not path.with_suffix(path.suffix + ".3").exists()


def test_count_and_clear(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    for _ in range(7):
        log.record(entry())
    assert log.count() == 7
    log.clear()
    assert log.count() == 0
    assert log.read() == []


# --- it must never break a dispatch -----------------------------------------


def test_an_unwritable_path_does_not_raise(tmp_path):
    """An audit write failing must not be the reason an action does or does not run."""
    blocker = tmp_path / "blocked"
    blocker.write_text("I am a file, not a directory")
    log = AuditLog(blocker / "audit.jsonl")
    log.record(entry())  # must not raise
    assert log.read() == []


def test_unserialisable_params_do_not_raise(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")

    class Exotic:
        def __repr__(self) -> str:
            return "<exotic>"

    log.record(entry(params={"thing": Exotic(), "path": tmp_path}))
    assert len(log.read()) == 1


def test_record_async_writes_off_the_event_loop(tmp_path):
    import asyncio

    log = AuditLog(tmp_path / "audit.jsonl")
    asyncio.run(log.record_async(entry(action_id="from_async")))
    assert [e.action_id for e in log.read()] == ["from_async"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")
def test_the_log_is_not_world_readable(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.record(entry())
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def test_summarize_counts_statuses(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record(entry(status="ok"))
    log.record(entry(status="denied", ok=False))
    log.record(entry(status="denied", ok=False))
    assert summarize(log.read()) == {"ok": 1, "denied": 2}
