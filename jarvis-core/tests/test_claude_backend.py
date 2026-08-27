"""The delegated coding backend: off, contained, and honest about what it is.

This is the one path in the project that sends code off the network, so most of
these tests are about it refusing to run — and the rest are about the fact that
switching backends changes who writes the code and nothing else.
"""

from __future__ import annotations

import json

import pytest

from jarvis.integrations.code.claude_backend import (
    ClaudeBackendError,
    build,
    parse_result,
    refuse_dangerous,
)


class FakeWorkspace:
    """A workspace that records what it was asked to run in the sandbox."""

    def __init__(self, output: str = "", code: int = 0, sandboxed: bool = True) -> None:
        self.sandboxed = sandboxed
        self.output = output
        self.code = code
        self.ran: list[str] = []

    async def run_sandboxed(self, command: str, timeout: float | None = None):
        self.ran.append(command)
        return self.code, self.output


def success(**over) -> str:
    payload = {
        "type": "result", "subtype": "success", "result": "Done: fixed the tests",
        "is_error": False, "num_turns": 7, "total_cost_usd": 0.42,
        "session_id": "abc123",
    }
    payload.update(over)
    return json.dumps(payload)


def test_the_default_is_off():
    backend = build({})
    assert backend.enabled is False
    assert backend.configured is False
    assert "off" in backend.why_not()


@pytest.mark.asyncio
async def test_it_will_not_run_without_a_key():
    """The exception to "no cloud" is deliberate, so it needs a deliberate key."""
    backend = build({"enabled": True})
    with pytest.raises(ClaudeBackendError) as err:
        await backend.run(FakeWorkspace(), "fix it")
    assert "no API key" in str(err.value)


@pytest.mark.asyncio
async def test_it_will_not_run_outside_a_sandbox():
    """The containment claim is the whole reason this is allowed to exist."""
    backend = build({"enabled": True, "api_key": "k"})
    with pytest.raises(ClaudeBackendError) as err:
        await backend.run(FakeWorkspace(sandboxed=False), "fix it")
    assert "no sandbox" in str(err.value)


@pytest.mark.asyncio
async def test_a_run_happens_inside_the_sandbox_and_nowhere_else():
    workspace = FakeWorkspace(output=success())
    backend = build({"enabled": True, "api_key": "k"})
    result = await backend.run(workspace, "fix the failing tests")
    assert result.ok is True and result.turns == 7
    (command,) = workspace.ran
    assert command.startswith("claude --print --output-format json")
    assert "fix the failing tests" in command


@pytest.mark.asyncio
async def test_the_model_flag_is_passed_when_configured():
    workspace = FakeWorkspace(output=success())
    await build({"enabled": True, "api_key": "k", "model": "sonnet"}).run(workspace, "x")
    assert "--model sonnet" in workspace.ran[0]


def test_dangerous_flags_are_refused_rather_than_honoured():
    """A backend told to skip its own permission gate is a mistake, not a choice."""
    assert refuse_dangerous(["--dangerously-skip-permissions"])
    assert refuse_dangerous([]) == ""
    built = build({"enabled": True, "api_key": "k",
                   "extra_args": ["--dangerously-skip-permissions"]})
    assert built.extra_args == ()


def test_a_result_is_parsed_into_something_a_task_can_report():
    parsed = parse_result(success())
    assert parsed.ok and parsed.text.startswith("Done") and parsed.cost_usd == 0.42
    assert parsed.as_dict()["backend"] == "claude-code"


def test_a_failure_is_a_failure():
    parsed = parse_result(success(is_error=True, result="could not do it"))
    assert parsed.ok is False and "could not do it" in parsed.error


def test_noise_around_the_json_is_tolerated():
    """A sandbox wrapper can prepend a line, and that is not a protocol error."""
    parsed = parse_result("starting container…\n" + success() + "\n")
    assert parsed.ok is True


def test_output_that_is_not_a_result_is_named_as_such():
    for raw in ("", "I am not JSON", json.dumps({"hello": "world"})):
        parsed = parse_result(raw)
        assert parsed.ok is False and parsed.error


@pytest.mark.asyncio
async def test_a_nonzero_exit_beats_a_cheerful_payload():
    """The sandbox's exit code is the one that saw what happened."""
    workspace = FakeWorkspace(output=success(), code=137)
    result = await build({"enabled": True, "api_key": "k"}).run(workspace, "x")
    assert result.ok is False and "137" in result.error


def test_the_backend_choice_falls_back_to_local_on_a_typo():
    """A typo in the setting that decides whether code leaves must not pick cloud."""
    from jarvis.integrations.code import CodeConfig

    assert CodeConfig.from_config({"backend": "claude_code"}).backend == "claude-code"
    assert CodeConfig.from_config({"backend": "clude-code"}).backend == "local"
    assert CodeConfig.from_config({}).backend == "local"


def test_a_repository_pin_beats_the_task_request():
    """"This repository is not delegated" has to mean it."""
    from jarvis.integrations.code import CodeConfig
    from jarvis.integrations.code.workspace import Repo

    cfg = CodeConfig.from_config({"backend": "claude-code"})
    pinned = Repo(name="secret", path="/tmp/secret", backend="local")
    assert cfg.backend_for(pinned, asked="claude-code") == "local"
    ordinary = Repo(name="app", path="/tmp/app")
    assert cfg.backend_for(ordinary) == "claude-code"
    # And asking for the SAFER backend is always honoured: a pin exists to stop
    # code leaving, never to force it out.
    assert cfg.backend_for(ordinary, asked="local") == "local"
