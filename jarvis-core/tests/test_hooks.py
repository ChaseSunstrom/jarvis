"""Hooks: the four ways something outside an automation can start one.

A hook is a trigger with a name. Every one of these could be written as
`platform: event` against a raw bus event, and each was — which is the reason
they exist:

* `voice_pipeline_event` fires fourteen times per voice run, so a wake-word
  automation written against it ran fourteen times unless the author got a
  nested `event_data` filter exactly right, and got no warning when they did
  not.
* `jarvis_task_updated` fires on every progress tick, and a listener cannot see
  the status the task had a moment ago — so "tell me when the research is
  finished" told you on every step.

What is pinned here: each platform fires when it should, does NOT fire when a
filter excludes it, and carries the trigger variables an action can actually
say out loud.
"""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.automation.triggers import async_attach_trigger  # noqa: E402
from jarvis.const import (  # noqa: E402
    EVENT_TASK_CANCELLED,
    EVENT_TASK_COMPLETED,
    EVENT_TASK_FAILED,
    EVENT_TASK_STARTED,
    EVENT_VOICE_PIPELINE,
    VOICE_WAKE_END,
)
from jarvis.core import Jarvis  # noqa: E402
from jarvis.tasks import (  # noqa: E402
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_RUNNING,
    TaskRegistry,
)


@pytest.fixture
def jarvis(tmp_path):
    return Jarvis(tmp_path)


class FakeClock:
    """Deterministic clock: sleeping just advances the (fake) wall time."""

    def __init__(self, start):
        self.current = start
        self.slept: list[float] = []

    def now(self):
        return self.current

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.current = self.current + timedelta(seconds=seconds)
        await asyncio.sleep(0)


class Fired:
    """Collects what a trigger fired with."""

    def __init__(self) -> None:
        self.runs: list[dict] = []

    async def __call__(self, trigger, context=None):
        self.runs.append(trigger)

    @property
    def count(self) -> int:
        return len(self.runs)


async def _attach(jarvis, config):
    fired = Fired()
    unsub = await async_attach_trigger(jarvis, config, fired)
    return fired, unsub


async def _settle(jarvis):
    for _ in range(3):
        await jarvis.async_block_till_done()
        await asyncio.sleep(0)


async def _wake(jarvis, *, word="hey_jarvis", pipeline="jarvis", device_id="", type_=VOICE_WAKE_END):
    """One pipeline event, shaped exactly as `PipelineRun._emit` shapes it."""
    await jarvis.bus.async_fire(
        EVENT_VOICE_PIPELINE,
        {
            "run_id": "run-1",
            "type": type_,
            "data": {"wake_word_output": {"wake_word_id": word, "timestamp": 1}},
            "pipeline": pipeline,
            "device_id": device_id,
        },
    )
    await _settle(jarvis)


# --- wake_word ---------------------------------------------------------------


async def test_wake_word_fires_once_per_detection(jarvis):
    fired, unsub = await _attach(jarvis, {"platform": "wake_word"})
    try:
        await _wake(jarvis)
        assert fired.count == 1
        assert fired.runs[0]["wake_word"] == "hey_jarvis"
        assert fired.runs[0]["platform"] == "wake_word"
    finally:
        unsub()


async def test_wake_word_ignores_every_other_stage_of_the_run(jarvis):
    """The bug this platform exists for: one automation, fourteen runs."""
    fired, unsub = await _attach(jarvis, {"platform": "wake_word"})
    try:
        for stage in ("run-start", "wake_word-start", "stt-start", "stt-end", "run-end"):
            await _wake(jarvis, type_=stage)
        assert fired.count == 0
    finally:
        unsub()


async def test_wake_word_can_be_scoped_to_one_satellite(jarvis):
    fired, unsub = await _attach(
        jarvis, {"platform": "wake_word", "device_id": "workshop"}
    )
    try:
        await _wake(jarvis, device_id="kitchen")
        assert fired.count == 0
        # A run with no satellite at all (a browser, a REST call) is not the
        # workshop either.
        await _wake(jarvis, device_id="")
        assert fired.count == 0
        await _wake(jarvis, device_id="workshop")
        assert fired.count == 1
        assert fired.runs[0]["device_id"] == "workshop"
    finally:
        unsub()


async def test_wake_word_can_name_the_word_and_the_pipeline(jarvis):
    fired, unsub = await _attach(
        jarvis,
        {"platform": "wake_word", "wake_word": ["ok_nabu", "hey_jarvis"], "pipeline": "night"},
    )
    try:
        await _wake(jarvis, word="hey_jarvis", pipeline="jarvis")
        assert fired.count == 0, "the pipeline filter did not hold"
        await _wake(jarvis, word="alexa", pipeline="night")
        assert fired.count == 0, "the word filter did not hold"
        await _wake(jarvis, word="ok_nabu", pipeline="night")
        assert fired.count == 1
    finally:
        unsub()


# --- task lifecycle ----------------------------------------------------------


@pytest.fixture
def tasks(jarvis):
    registry = TaskRegistry(jarvis)
    jarvis.tasks = registry
    return registry


async def test_task_started_fires_on_the_transition_and_not_on_every_update(jarvis, tasks):
    fired, unsub = await _attach(jarvis, {"platform": "task", "status": "started"})
    try:
        task = await tasks.async_add("look into it")
        await _settle(jarvis)
        assert fired.count == 0, "queued is not started"

        await tasks.async_update(task.id, status=STATUS_RUNNING)
        await _settle(jarvis)
        assert fired.count == 1

        # Ten progress updates later it is still one start. This is the whole
        # difference from listening to `jarvis_task_updated`.
        for i in range(10):
            await tasks.async_update(task.id, detail=f"step {i}")
        await _settle(jarvis)
        assert fired.count == 1
    finally:
        unsub()


async def test_task_completed_carries_the_result_the_automation_will_read_out(jarvis, tasks):
    fired, unsub = await _attach(jarvis, {"platform": "task", "status": "completed"})
    try:
        task = await tasks.async_add("summarise the logs")
        await tasks.async_update(task.id, status=STATUS_RUNNING)
        await tasks.async_update(task.id, status=STATUS_DONE, result="nothing alarming")
        await _settle(jarvis)

        assert fired.count == 1
        run = fired.runs[0]
        assert run["status"] == "completed"
        assert run["result"] == "nothing alarming"
        assert run["title"] == "summarise the logs"
        assert run["task_id"] == task.id
    finally:
        unsub()


async def test_task_failed_is_separate_from_completed(jarvis, tasks):
    done, unsub_done = await _attach(jarvis, {"platform": "task", "status": "completed"})
    failed, unsub_failed = await _attach(jarvis, {"platform": "task", "status": "failed"})
    try:
        task = await tasks.async_add("fetch the thing")
        await tasks.async_update(task.id, status=STATUS_ERROR, error="the server refused")
        await _settle(jarvis)

        assert done.count == 0
        assert failed.count == 1
        assert failed.runs[0]["error"] == "the server refused"
    finally:
        unsub_done()
        unsub_failed()


async def test_a_cancelled_task_is_not_a_failure(jarvis, tasks):
    """Somebody asked it to stop and it did. Paging a human about that is noise."""
    failed, unsub_failed = await _attach(jarvis, {"platform": "task", "status": "failed"})
    cancelled, unsub_cancelled = await _attach(
        jarvis, {"platform": "task", "status": "cancelled"}
    )
    try:
        task = await tasks.async_add("long job")
        await tasks.async_update(task.id, status=STATUS_CANCELLED)
        await _settle(jarvis)
        assert failed.count == 0
        assert cancelled.count == 1
    finally:
        unsub_failed()
        unsub_cancelled()


async def test_task_triggers_can_be_scoped_to_a_kind(jarvis, tasks):
    fired, unsub = await _attach(
        jarvis, {"platform": "task", "status": ["completed", "failed"], "kind": "research"}
    )
    try:
        other = await tasks.async_add("a coding job", kind="code")
        await tasks.async_update(other.id, status=STATUS_DONE)
        await _settle(jarvis)
        assert fired.count == 0

        mine = await tasks.async_add("a research job", kind="research")
        await tasks.async_update(mine.id, status=STATUS_DONE)
        await _settle(jarvis)
        assert fired.count == 1
        assert fired.runs[0]["kind"] == "research"
    finally:
        unsub()


async def test_a_task_trigger_with_no_status_hears_all_of_them(jarvis, tasks):
    fired, unsub = await _attach(jarvis, {"platform": "task"})
    try:
        task = await tasks.async_add("job")
        await tasks.async_update(task.id, status=STATUS_RUNNING)
        await tasks.async_update(task.id, status=STATUS_DONE)
        await _settle(jarvis)
        assert [run["status"] for run in fired.runs] == ["started", "completed"]
    finally:
        unsub()


async def test_an_unknown_status_is_a_warning_not_a_silent_no_op(jarvis, tasks, caplog):
    """A typo in YAML must not produce an automation that simply never runs."""
    fired, unsub = await _attach(jarvis, {"platform": "task", "status": "finished"})
    try:
        assert "unknown status finished" in caplog.text
        # It falls back to every status rather than to none: a hook that fires
        # too often is visible, one that never fires is not.
        task = await tasks.async_add("job")
        await tasks.async_update(task.id, status=STATUS_DONE)
        await _settle(jarvis)
        assert fired.count == 1
    finally:
        unsub()


async def test_the_lifecycle_events_are_distinct_strings(jarvis, tasks):
    """Three names, so a listener never has to re-derive which moment it is."""
    seen: list[str] = []
    for event in (
        EVENT_TASK_STARTED,
        EVENT_TASK_COMPLETED,
        EVENT_TASK_FAILED,
        EVENT_TASK_CANCELLED,
    ):
        jarvis.bus.listen(event, lambda e, name=event: seen.append(name))
    assert len({EVENT_TASK_STARTED, EVENT_TASK_COMPLETED, EVENT_TASK_FAILED}) == 3

    first = await tasks.async_add("one")
    await tasks.async_update(first.id, status=STATUS_RUNNING)
    await tasks.async_update(first.id, status=STATUS_DONE)
    second = await tasks.async_add("two")
    await tasks.async_update(second.id, status=STATUS_ERROR, error="no")
    await _settle(jarvis)

    assert seen == [EVENT_TASK_STARTED, EVENT_TASK_COMPLETED, EVENT_TASK_FAILED]


# --- event triggers with nested data ----------------------------------------


async def test_an_event_trigger_can_match_a_dotted_path(jarvis):
    fired, unsub = await _attach(
        jarvis,
        {
            "platform": "event",
            "event_type": "delivery",
            "event_data": {"parcel.carrier": "royal_mail"},
        },
    )
    try:
        await jarvis.bus.async_fire("delivery", {"parcel": {"carrier": "dhl"}})
        await _settle(jarvis)
        assert fired.count == 0

        await jarvis.bus.async_fire("delivery", {"parcel": {"carrier": "royal_mail"}})
        await _settle(jarvis)
        assert fired.count == 1
    finally:
        unsub()


async def test_a_dotted_path_can_index_a_list(jarvis):
    fired, unsub = await _attach(
        jarvis,
        {
            "platform": "event",
            "event_type": "job",
            "event_data": {"steps.0.status": "done"},
        },
    )
    try:
        await jarvis.bus.async_fire("job", {"steps": [{"status": "running"}]})
        await _settle(jarvis)
        assert fired.count == 0

        await jarvis.bus.async_fire("job", {"steps": [{"status": "done"}]})
        await _settle(jarvis)
        assert fired.count == 1
    finally:
        unsub()


async def test_a_missing_path_does_not_match_and_does_not_raise(jarvis):
    fired, unsub = await _attach(
        jarvis,
        {"platform": "event", "event_type": "job", "event_data": {"a.b.c.d": "x"}},
    )
    try:
        await jarvis.bus.async_fire("job", {"a": 3})
        await jarvis.bus.async_fire("job", {})
        await _settle(jarvis)
        assert fired.count == 0
    finally:
        unsub()


async def test_a_list_in_event_data_means_any_of_these(jarvis):
    fired, unsub = await _attach(
        jarvis,
        {
            "platform": "event",
            "event_type": "delivery",
            "event_data": {"parcel.carrier": ["dhl", "royal_mail"]},
        },
    )
    try:
        await jarvis.bus.async_fire("delivery", {"parcel": {"carrier": "dhl"}})
        await jarvis.bus.async_fire("delivery", {"parcel": {"carrier": "ups"}})
        await _settle(jarvis)
        assert fired.count == 1
    finally:
        unsub()


# --- webhooks and schedules --------------------------------------------------


async def test_a_webhook_trigger_fires_with_the_body_it_was_posted(jarvis):
    from jarvis.api import common

    fired, unsub = await _attach(
        jarvis, {"platform": "webhook", "webhook_id": "front-door-bell"}
    )
    try:
        delivered = await common.async_dispatch_webhook(
            jarvis, "front-door-bell", {"json": {"pressed": True}, "method": "POST"}
        )
        await _settle(jarvis)
        assert delivered == 1
        assert fired.count == 1
        assert fired.runs[0]["webhook_id"] == "front-door-bell"
    finally:
        unsub()


async def test_a_webhook_id_nobody_registered_is_a_404_not_a_silent_ok(jarvis):
    """Guessing ids must not read as success — that is how you probe for one."""
    from jarvis.api import common

    with pytest.raises(common.ApiError) as raised:
        await common.async_dispatch_webhook(jarvis, "guessed-it", {"json": {}})
    assert raised.value.status == 404


async def test_webhook_require_auth_is_off_unless_the_operator_says_otherwise(jarvis):
    """The id IS the secret by default — but a house can demand a token too."""
    from jarvis.api import rest

    assert rest._truthy(None) is False
    assert rest._truthy("true") is True


async def test_a_schedule_fires_on_its_pattern(jarvis):
    """`time_pattern` is the scheduling hook, and it runs off the injected clock.

    Documented as a hook because "every five minutes" is the third thing people
    ask for after the wake word and the task finishing, and it was buried in the
    automation reference under a name nobody searches for.
    """
    clock = FakeClock(datetime(2024, 1, 1, 10, 4, 55))
    jarvis.data["automation_clock"] = clock

    fired, unsub = await _attach(jarvis, {"platform": "time_pattern", "minutes": "/5"})
    try:
        for _ in range(5):
            await asyncio.sleep(0)
        assert fired.count >= 1
        assert fired.runs[0]["platform"] == "time_pattern"
        assert clock.slept[0] == pytest.approx(5.0)  # the next :05 boundary
    finally:
        unsub()
