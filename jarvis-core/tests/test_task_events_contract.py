"""The task-event contract, as the server implements it.

`tests/contracts/task_events.json` is read by this file and by the console's
`src/lib/taskEvents.test.ts`. Neither side may change the shape alone — which is
the whole reason the table is a file rather than a comment saying "keep in
step", a form of words that has never kept anything in step.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis import tasks as tasks_module
from jarvis.tasks import TaskRegistry

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "tests/contracts/task_events.json").read_text()
)


class FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def fire(self, event: str, data: dict) -> None:
        self.events.append((event, data))


class FakeJarvis:
    def __init__(self) -> None:
        self.bus = FakeBus()


def test_the_contract_names_the_events_the_module_declares():
    declared = {
        value
        for name, value in vars(tasks_module).items()
        if name.startswith("EVENT_TASK_") and isinstance(value, str)
    }
    assert declared == set(CONTRACT["events"]), (
        "the events the server can fire and the events the contract describes "
        "have diverged; the console reads the contract"
    )


@pytest.mark.parametrize("event", sorted(CONTRACT["events"]))
def test_every_event_has_a_when_and_required_fields(event):
    spec = CONTRACT["events"][event]
    assert spec.get("when"), f"{event} does not say when it fires"
    assert spec.get("required"), f"{event} lists no required fields"


async def test_the_payloads_carry_every_required_field():
    registry = TaskRegistry(FakeJarvis())
    task = await registry.async_add("contract", kind="code")
    call = registry.tool_started(task.id, name="read_file", arguments={"path": "a"})
    registry.tool_finished(task.id, name="read_file", call_id=call, ok=True)
    registry.output(task.id, "hello")
    await registry.async_update(task.id, status="done")
    await registry.async_remove(task.id)

    seen: dict[str, dict] = {}
    for name, data in registry.jarvis.bus.events:
        seen.setdefault(name, data)

    for event, spec in CONTRACT["events"].items():
        assert event in seen, f"nothing fired {event}"
        for field in spec["required"]:
            assert field in seen[event], f"{event} is missing {field!r}"


async def test_a_lifecycle_frame_carries_the_whole_task():
    """A client that missed an update must recover from any single frame."""
    registry = TaskRegistry(FakeJarvis())
    task = await registry.async_add("contract", kind="code", steps=["one"])
    payload = registry.jarvis.bus.events[0][1]["task"]
    for field in CONTRACT["events"]["jarvis_task_added"]["task_fields"]:
        assert field in payload, f"the task payload is missing {field!r}"
    assert payload["id"] == task.id


def test_the_log_contract_matches_the_module():
    log = CONTRACT["log"]
    assert log["max_entries"] == tasks_module.MAX_LOG_ENTRIES
    assert set(log["kinds"]) >= {"status", "step", "tool", "output"}
