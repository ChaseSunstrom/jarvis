"""Watching a task run: tool calls, output, cancellation and the log.

The hole this closes: a task could say what step it was on and nothing else. A
coding job that called nine tools over four minutes showed a bar and a title;
its tool calls arrived when the job was over, and the output of its checks
arrived not at all. Chat turns had `jarvis_tool_started`/`finished` and nothing
else did, so "watch Jarvis work" meant "watch Jarvis chat".
"""

from __future__ import annotations

import pytest

from jarvis.tasks import (
    EVENT_TASK_OUTPUT,
    EVENT_TASK_TOOL_FINISHED,
    EVENT_TASK_TOOL_STARTED,
    MAX_LOG_ENTRIES,
    STATUS_CANCELLED,
    STATUS_RUNNING,
    TaskCancelled,
    TaskRegistry,
)


class FakeBus:
    """Records what was fired, in order, the way a client would receive it."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def fire(self, event: str, data: dict) -> None:
        self.events.append((event, data))

    def of(self, event: str) -> list[dict]:
        return [data for name, data in self.events if name == event]


class FakeJarvis:
    def __init__(self) -> None:
        self.bus = FakeBus()


@pytest.fixture
def registry() -> TaskRegistry:
    return TaskRegistry(FakeJarvis())


async def test_a_tool_call_is_announced_when_it_starts_not_when_it_ends(registry):
    task = await registry.async_add("Add an OFFLINE state", kind="code")
    call = registry.tool_started(task.id, name="read_file", arguments={"path": "a.svelte"})
    assert call, "a tool call needs an id its finish can quote"

    started = registry.jarvis.bus.of(EVENT_TASK_TOOL_STARTED)
    assert len(started) == 1
    assert started[0]["task_id"] == task.id
    assert started[0]["name"] == "read_file"
    assert started[0]["arguments"] == {"path": "a.svelte"}
    # Nothing has finished yet: that is the entire point.
    assert registry.jarvis.bus.of(EVENT_TASK_TOOL_FINISHED) == []

    registry.tool_finished(task.id, name="read_file", call_id=call, ok=True, duration_ms=12)
    finished = registry.jarvis.bus.of(EVENT_TASK_TOOL_FINISHED)
    assert finished[0]["call_id"] == call
    assert finished[0]["ok"] is True
    assert finished[0]["duration_ms"] == 12


async def test_a_failed_call_says_so_and_carries_the_reason(registry):
    task = await registry.async_add("job", kind="code")
    call = registry.tool_started(task.id, name="run_check")
    registry.tool_finished(
        task.id, name="run_check", call_id=call, ok=False, error="exit 1: 3 failing"
    )
    event = registry.jarvis.bus.of(EVENT_TASK_TOOL_FINISHED)[0]
    assert event["ok"] is False
    assert event["status"] == "error"
    assert "3 failing" in event["error"]


async def test_output_streams_and_is_ordered(registry):
    task = await registry.async_add("job", kind="code")
    registry.output(task.id, "line one")
    registry.output(task.id, "line two", stream="stderr")
    events = registry.jarvis.bus.of(EVENT_TASK_OUTPUT)
    assert [e["chunk"] for e in events] == ["line one", "line two"]
    assert [e["stream"] for e in events] == ["stdout", "stderr"]
    # A client must be able to tell a dropped frame from a reordered one.
    assert events[1]["seq"] > events[0]["seq"]


async def test_an_unknown_stream_is_not_taken_at_its_word(registry):
    task = await registry.async_add("job")
    registry.output(task.id, "x", stream="../../etc/passwd")
    assert registry.jarvis.bus.of(EVENT_TASK_OUTPUT)[0]["stream"] == "stdout"


async def test_output_for_a_task_that_no_longer_exists_is_dropped_not_raised(registry):
    registry.output("gone", "anything")
    assert registry.jarvis.bus.of(EVENT_TASK_OUTPUT) == []


async def test_a_worker_finds_out_it_was_cancelled(registry):
    task = await registry.async_add("job")
    registry.raise_if_cancelled(task.id)  # not cancelled: returns quietly

    await registry.async_update(task.id, status=STATUS_CANCELLED)
    with pytest.raises(TaskCancelled):
        registry.raise_if_cancelled(task.id)
    assert registry.cancelled(task.id) is True


async def test_a_forgotten_task_counts_as_cancelled(registry):
    """There is nothing left to report to, which is the same thing."""
    task = await registry.async_add("job")
    await registry.async_remove(task.id)
    with pytest.raises(TaskCancelled):
        registry.raise_if_cancelled(task.id)


async def test_the_log_replays_what_a_late_client_missed(registry):
    task = await registry.async_add("job", kind="code", steps=["read", "edit"])
    await registry.async_update(task.id, status=STATUS_RUNNING)
    call = registry.tool_started(task.id, name="read_file", arguments={"path": "a"})
    registry.tool_finished(task.id, name="read_file", call_id=call, ok=True)
    registry.output(task.id, "checking 446 files")
    await registry.async_update(task.id, step=0, step_status="done")

    kinds = [entry["kind"] for entry in registry.log_entries(task.id)]
    assert "status" in kinds and "tool" in kinds and "output" in kinds and "step" in kinds
    text = " ".join(entry["text"] for entry in registry.log_entries(task.id))
    assert "read_file" in text and "checking 446 files" in text


async def test_the_log_is_bounded_and_keeps_the_tail(registry):
    task = await registry.async_add("noisy")
    for i in range(MAX_LOG_ENTRIES + 50):
        registry.output(task.id, f"line {i}")
    entries = registry.log_entries(task.id)
    assert len(entries) == MAX_LOG_ENTRIES
    # The tail, not the head: what somebody arriving late wants is the end.
    assert entries[-1]["text"] == f"line {MAX_LOG_ENTRIES + 49}"


async def test_the_log_is_not_on_every_update_frame(registry):
    """The task payload rides on every change; the log does not ride with it.

    A 200-entry log on a frame that fires several times a second is the same
    kilobytes over and over, and the log is fetched once by the page that wants
    it (`jarvis/tasks/log`).
    """
    task = await registry.async_add("job")
    registry.output(task.id, "something")
    assert "log" not in task.as_dict()


async def test_the_log_survives_a_restart(registry, tmp_path):
    class Store:
        def __init__(self) -> None:
            self.data: dict = {}

        async def load(self) -> dict:
            return self.data

        async def save(self, data: dict) -> None:
            self.data = data

    store = Store()
    first = TaskRegistry(FakeJarvis(), store=store)
    task = await first.async_add("job", kind="code")
    first.output(task.id, "the line that matters")
    await first.async_save()

    second = TaskRegistry(FakeJarvis(), store=store)
    await second.async_load()
    replayed = second.log_entries(task.id)
    assert any("the line that matters" in entry["text"] for entry in replayed)
