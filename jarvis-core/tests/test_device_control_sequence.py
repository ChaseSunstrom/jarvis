"""A plan of device actions, run in order, with state carried between them.

Three steps where the second needs the first's answer and the third exists to
say whether the second worked is the shape of every real desktop automation —
and a model asked to do it with three independent tool calls has to carry that
state in its own context, correctly, every time.

What is pinned here is the four properties that make `run_sequence` worth
having over a loop: state really is carried, a failure really does stop the
rest, a step's tier is still the device's, and `verify` is checked before the
next step rather than after everything.
"""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.integrations.device_control import (
    MAX_SEQUENCE_STEPS,
    DeviceControl,
    _resolve_params,
    _verify,
)

pytestmark = pytest.mark.asyncio


class FakeControl(DeviceControl):
    """A manager whose `run` is a script, so the sequencing is what is tested."""

    def __init__(self, script: list[dict[str, Any]]) -> None:  # noqa: D107
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def run(self, device=None, action=None, params=None, reason="", tier=None,
                  timeout=None, context=None) -> dict[str, Any]:
        self.calls.append({"device": device, "action": action, "params": dict(params or {})})
        return self.script.pop(0) if self.script else {"status": "ok", "result": {}}


def ok(result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": "ok", "result": result or {}, "tier": 1}


async def test_a_step_can_use_what_an_earlier_one_produced():
    manager = FakeControl([ok({"id": "win-7"}), ok({})])
    outcome = await manager.run_sequence(
        [
            {"device": "desk", "action": "ui_find", "params": {"title": "Notes"}, "save": "window"},
            {"device": "desk", "action": "ui_type", "params": {"target": "{window.id}", "text": "hi"}},
        ]
    )
    assert outcome["status"] == "ok"
    assert manager.calls[1]["params"] == {"target": "win-7", "text": "hi"}
    assert outcome["completed"] == 2


async def test_a_placeholder_nothing_saved_is_an_error_not_a_literal():
    """Otherwise `{window.id}` reaches a device as five characters of nonsense."""
    manager = FakeControl([ok({})])
    outcome = await manager.run_sequence(
        [{"device": "desk", "action": "ui_type", "params": {"target": "{window.id}"}}]
    )
    assert outcome["status"] == "error"
    assert "no earlier step saved" in outcome["steps"][0]["error"]
    assert manager.calls == []


async def test_a_failed_step_stops_the_rest_and_says_which():
    manager = FakeControl([ok({}), {"status": "denied", "message": "the user said no"}])
    outcome = await manager.run_sequence(
        [
            {"device": "desk", "action": "ui_find"},
            {"device": "desk", "action": "ui_type"},
            {"device": "desk", "action": "ui_read"},
        ]
    )
    assert outcome["status"] == "denied"
    assert outcome["failed_step"] == 2
    assert outcome["completed"] == 1
    # The third never ran, and says so rather than being absent — "it half
    # worked" is the thing the user needs to know.
    assert outcome["steps"][2]["status"] == "skipped"
    assert len(manager.calls) == 2
    assert "do not say it is done" in outcome["message"]


async def test_an_approval_ends_the_sequence_rather_than_continuing_without_it():
    manager = FakeControl([{"status": "approval_required", "request_id": "abc"}, ok({})])
    outcome = await manager.run_sequence(
        [{"device": "desk", "action": "unlock"}, {"device": "desk", "action": "ui_type"}]
    )
    assert outcome["status"] == "approval_required"
    # The second step must NOT have run: a sequence that carried on past a held
    # action would be a way to smuggle one past a prompt.
    assert len(manager.calls) == 1


async def test_verify_runs_before_the_next_step():
    manager = FakeControl([ok({"text": "nothing here"}), ok({})])
    outcome = await manager.run_sequence(
        [
            {"device": "desk", "action": "ui_read", "verify": {"contains": "hello"}},
            {"device": "desk", "action": "ui_type"},
        ]
    )
    assert outcome["status"] == "error"
    assert outcome["steps"][0]["verified"] is False
    assert len(manager.calls) == 1


async def test_a_verified_step_says_so():
    manager = FakeControl([ok({"text": "hello there"})])
    outcome = await manager.run_sequence(
        [{"device": "desk", "action": "ui_read", "verify": {"contains": "hello"}}]
    )
    assert outcome["status"] == "ok"
    assert outcome["steps"][0]["verified"] is True


async def test_every_step_is_watched_as_it_happens():
    """The task UI draws these; a plan that reported only at the end would be a
    spinner for the length of the automation."""
    seen: list[int] = []
    manager = FakeControl([ok({}), ok({})])
    await manager.run_sequence(
        [{"device": "d", "action": "a"}, {"device": "d", "action": "b"}],
        on_step=lambda record: seen.append(record["step"]),
    )
    assert seen == [1, 2]


async def test_an_empty_or_enormous_plan_is_refused():
    manager = FakeControl([])
    assert (await manager.run_sequence([]))["status"] == "error"
    long_plan = [{"device": "d", "action": "a"}] * (MAX_SEQUENCE_STEPS + 1)
    outcome = await manager.run_sequence(long_plan)
    assert outcome["status"] == "error"
    assert str(MAX_SEQUENCE_STEPS) in outcome["error"]


# --- the two helpers ----------------------------------------------------------


def test_a_placeholder_is_the_whole_value_or_nothing():
    """Substituting INTO a string would make `"rm -rf {dir}"` constructible."""
    saved = {"window": {"id": "win-7"}}
    assert _resolve_params({"a": "{window.id}"}, saved) == {"a": "win-7"}
    # Not interpolated: the literal survives, and the device gets a parameter
    # that is plainly wrong rather than one that is subtly dangerous.
    assert _resolve_params({"a": "id={window.id}"}, saved) == {"a": "id={window.id}"}


def test_a_placeholder_can_take_a_whole_saved_result():
    assert _resolve_params({"a": "{window}"}, {"window": {"id": 1}}) == {"a": {"id": 1}}


def test_verify_reports_why_rather_than_just_false():
    assert _verify({"text": "hello"}, {"contains": "hello"}) == ""
    assert "contain" in _verify({"text": "hello"}, {"contains": "goodbye"})
    assert "still contains" in _verify({"text": "hello"}, {"absent": "hello"})
    assert "expected ok=True" in _verify({"ok": False}, {"equals": {"ok": True}})
