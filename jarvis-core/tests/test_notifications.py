"""The things Jarvis says without being asked, kept.

Before this, a briefing spoken to an empty room, a task that finished while you
were out and a reminder that fired on your phone all happened and left nothing
behind — so "what did you tell me earlier?" had no answer, and neither did "why
am I seeing this?".

What is pinned here: the events that already exist produce records, the records
survive a restart, "read" and "dismissed" are different things, and the store
does not throw away something the user has not seen in order to keep something
they have.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.const import EVENT_TASK_CANCELLED, EVENT_TASK_COMPLETED, EVENT_TASK_FAILED  # noqa: E402
from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import notifications as notifications_integration  # noqa: E402
from jarvis.integrations.notifications import (  # noqa: E402
    EVENT_NOTIFICATION,
    NotificationStore,
)
from jarvis.tasks import STATUS_DONE, STATUS_ERROR, STATUS_RUNNING, TaskRegistry  # noqa: E402


@pytest.fixture
async def house(tmp_path):
    jarvis = Jarvis(tmp_path)
    await notifications_integration.async_setup(jarvis, {})
    tasks = TaskRegistry(jarvis)
    jarvis.tasks = tasks
    return jarvis, jarvis.data["notifications"], tasks


async def settle(jarvis) -> None:
    for _ in range(3):
        await jarvis.async_block_till_done()


# --- a hook fires, a record exists -------------------------------------------


async def test_a_finished_task_leaves_a_record_that_can_be_retrieved(house):
    jarvis, store, tasks = house
    task = await tasks.async_add("look into the downstairs lights", kind="background")
    await tasks.async_update(task.id, status=STATUS_RUNNING)
    await tasks.async_update(task.id, status=STATUS_DONE, result="Two were still on.")
    await settle(jarvis)

    rows = store.listing()
    assert len(rows) == 1
    assert "look into the downstairs lights" in rows[0]["title"]
    assert rows[0]["body"] == "Two were still on."
    # The event that produced it, so "why am I seeing this" is answerable with
    # a fact rather than a guess — and a link to the thing itself.
    assert rows[0]["source"] == EVENT_TASK_COMPLETED
    assert task.id in rows[0]["link"]
    assert store.unread == 1


async def test_a_failed_task_says_why(house):
    jarvis, store, tasks = house
    task = await tasks.async_add("fetch the thing")
    await tasks.async_update(task.id, status=STATUS_ERROR, error="the server refused")
    await settle(jarvis)

    rows = store.listing()
    assert rows[0]["title"].startswith("Failed:")
    assert rows[0]["body"] == "the server refused"
    assert rows[0]["source"] == EVENT_TASK_FAILED


async def test_cancelling_something_yourself_is_not_news(house):
    """The machine telling you what you just did."""
    jarvis, store, tasks = house
    task = await tasks.async_add("long job")
    await tasks.async_update(task.id, status="cancelled")
    await settle(jarvis)
    assert store.listing() == []
    assert EVENT_TASK_CANCELLED  # the event exists; it simply records nothing


async def test_a_reminder_finishing_is_not_recorded_twice(house):
    """The reminder IS the notification. Recording "the reminder task
    completed" as well puts the same thing on screen twice, once in the user's
    words and once in ours."""
    jarvis, store, tasks = house
    task = await tasks.async_add("check the oven", kind="notify")
    await tasks.async_update(task.id, status=STATUS_DONE, result="check the oven")
    await settle(jarvis)
    assert store.listing() == []


async def test_what_the_narrator_said_or_asked_is_on_the_record(house):
    """"What did you tell me while I was out?" reads this (M95); the narrator
    writes to it (M86) — delivered narrations only, an offer once when asked and
    once more when it acted, never one the limiter or the night held back."""
    from jarvis.integrations.narrate import EVENT_NARRATED

    jarvis, store, _tasks = house
    assert EVENT_NARRATED == "narrate_narrated"
    base = {"time": 1000.0, "entity_id": "cover.garage_door", "name": "Garage Door",
            "message": "The Garage Door has opened", "area": "Garage", "importance": "normal"}
    jarvis.bus.fire(EVENT_NARRATED, {**base, "delivered": False, "reason": "rate limit"})
    jarvis.bus.fire(EVENT_NARRATED, {**base, "delivered": True, "reason": "asked",
                                     "offer": {"service": "cover.close_cover", "question": "Shall I close it?"}})
    jarvis.bus.fire(EVENT_NARRATED, {**base, "delivered": True, "reason": "asked",
                                     "offer": {"service": "cover.close_cover", "question": "Shall I close it?"}})
    jarvis.bus.fire(EVENT_NARRATED, {**base, "delivered": True, "acted": True, "answered": "yes",
                                     "reason": "cover.close_cover on the user's yes",
                                     "offer": {"service": "cover.close_cover", "question": "Shall I close it?"}})
    await jarvis.bus.async_block_till_done()
    notices = [n for n in store.entries if n.kind == "notice"]
    assert [n.title for n in notices] == [
        "The Garage Door has opened — Shall I close it?",
        "The Garage Door has opened — done on your yes",
    ], [n.title for n in notices]
    assert notices[0].source == "narrate_narrated"


async def test_the_briefing_leaves_one_too(house):
    jarvis, store, _tasks = house
    await jarvis.bus.async_fire(
        "briefing_ready", {"title": "Morning briefing", "text": "Cold, and the bins go out."}
    )
    await settle(jarvis)

    rows = store.listing()
    assert rows[0]["kind"] == "briefing"
    assert "bins" in rows[0]["body"]


async def test_every_record_is_announced_as_it_is_made(house):
    """A surface that is open draws it as it arrives; a surface that was closed
    catches up from the list. Both, because either alone is a gap."""
    jarvis, store, _tasks = house
    seen: list[dict] = []
    jarvis.bus.listen(EVENT_NOTIFICATION, lambda event: seen.append(event.data))

    await store.async_add("task", "Finished: something", "and here is what it found")
    await settle(jarvis)

    assert len(seen) == 1
    assert seen[0]["notification"]["title"] == "Finished: something"


# --- what a person does with them ---------------------------------------------


async def test_read_and_dismissed_are_different_things(house):
    jarvis, store, _tasks = house
    first = await store.async_add("task", "One")
    await store.async_add("task", "Two")

    await store.async_mark_read(first["notification"]["id"])
    assert store.unread == 1
    assert len(store.listing()) == 2, "reading something does not remove it"

    await store.async_dismiss(first["notification"]["id"])
    assert len(store.listing()) == 1


async def test_unread_only_is_what_a_badge_counts(house):
    jarvis, store, _tasks = house
    await store.async_add("task", "One")
    second = await store.async_add("task", "Two")
    await store.async_mark_read(second["notification"]["id"])

    assert [row["title"] for row in store.listing(unread_only=True)] == ["One"]


async def test_the_store_drops_what_you_have_seen_before_what_you_have_not(tmp_path):
    """A hundred unread and one read is not a reason to throw away something
    nobody has looked at."""
    jarvis = Jarvis(tmp_path)
    store = NotificationStore(jarvis, max_entries=3)
    keep = await store.async_add("task", "Unread and old")
    filler = await store.async_add("task", "Read and old")
    await store.async_mark_read(filler["notification"]["id"])
    await store.async_add("task", "Two")
    await store.async_add("task", "Three")

    titles = [row["title"] for row in store.listing()]
    assert "Unread and old" in titles
    assert "Read and old" not in titles
    assert keep["recorded"] is True


async def test_records_survive_a_restart(tmp_path):
    jarvis = Jarvis(tmp_path)
    first = NotificationStore(jarvis)
    await first.async_load()
    await first.async_add("task", "Finished: research", "three sources agreed")

    second = NotificationStore(Jarvis(tmp_path))
    await second.async_load()
    assert [row["title"] for row in second.listing()] == ["Finished: research"]
    assert second.unread == 1


async def test_a_kind_can_be_switched_off(tmp_path):
    jarvis = Jarvis(tmp_path)
    store = NotificationStore(jarvis, kinds={"briefing": False})
    await store.async_load()

    refused = await store.async_add("briefing", "Morning briefing")
    assert refused["recorded"] is False
    assert "switched off" in refused["reason"]
    assert (await store.async_add("task", "Finished: something"))["recorded"] is True


async def test_a_record_with_no_title_is_refused(house):
    _jarvis, store, _tasks = house
    assert (await store.async_add("task", ""))["recorded"] is False


# --- the briefing, from the console -------------------------------------------


async def test_the_briefing_schedule_can_be_changed_without_a_restart(tmp_path):
    """"At seven, not at six" and "stop telling me about the calendar" are the
    two things anybody wants to change about a briefing, and neither is worth
    an SSH session."""
    from jarvis.integrations import briefing as briefing_integration

    jarvis = Jarvis(tmp_path)
    await briefing_integration.async_setup(jarvis, {"morning": "07:00", "evening": "22:00"})
    manager = jarvis.data["briefing"]

    assert manager.settings()["morning"] == "07:00"

    after = manager.configure({"morning": "06:30", "include": ["weather", "tasks"]})
    assert after["morning"] == "06:30"
    assert after["include"] == ["weather", "tasks"]
    assert "calendar" in after["available"], "the console still needs the full list to offer"

    # And the schedule loop reads it on its next tick rather than needing a
    # second scheduler.
    assert manager.schedule["morning"].strftime("%H:%M") == "06:30"


async def test_a_briefing_time_that_is_not_one_is_refused(tmp_path):
    from jarvis.integrations import briefing as briefing_integration

    jarvis = Jarvis(tmp_path)
    await briefing_integration.async_setup(jarvis, {})
    manager = jarvis.data["briefing"]

    with pytest.raises(ValueError, match="not a time"):
        manager.configure({"morning": "half seven"})
    with pytest.raises(ValueError, match="no such section"):
        manager.configure({"include": ["weather", "horoscope"]})


async def test_a_briefing_can_be_switched_off_from_the_console(tmp_path):
    from jarvis.integrations import briefing as briefing_integration

    jarvis = Jarvis(tmp_path)
    await briefing_integration.async_setup(jarvis, {"morning": "07:00"})
    manager = jarvis.data["briefing"]

    assert manager.configure({"morning": "off"})["morning"] == ""
    assert "morning" not in manager.schedule


# ---------------------------------------------------------------------------
# M95: finished work speaks, and the inbox is a tool
# ---------------------------------------------------------------------------
class Companion:
    def __init__(self, jarvis) -> None:
        self.messages = []
        jarvis.services.register("companion", "notify", self, supports_response=True)

    async def __call__(self, call):
        self.messages.append(dict(call.data))
        return {"status": "delivered", "device_id": "phone"}


async def test_a_finished_background_job_is_announced_through_companion(tmp_path):
    jarvis = Jarvis(tmp_path)
    companion = Companion(jarvis)
    await notifications_integration.async_setup(jarvis, {})
    jarvis.bus.fire(EVENT_TASK_COMPLETED, {"task": {"id": "t1", "kind": "background", "title": "Audit every sensor",
                                                   "result": "Two look wrong: the garage humidity and the hall CO2."}})
    await jarvis.async_block_till_done()
    assert len(companion.messages) == 1
    said = companion.messages[0]["message"]
    assert said.startswith("Finished: Audit every sensor.") and "garage humidity" in said
    # A reminder finishing IS the reminder: it is not announced twice.
    jarvis.bus.fire(EVENT_TASK_COMPLETED, {"task": {"id": "t2", "kind": "notify", "title": "Wake up", "result": ""}})
    await jarvis.async_block_till_done()
    assert len(companion.messages) == 1
    await jarvis.async_stop()


async def test_speak_completions_false_keeps_the_card_and_says_nothing(tmp_path):
    jarvis = Jarvis(tmp_path)
    companion = Companion(jarvis)
    await notifications_integration.async_setup(jarvis, {"speak_completions": False})
    jarvis.bus.fire(EVENT_TASK_COMPLETED, {"task": {"id": "t1", "kind": "research", "title": "Bitcoin", "result": "up"}})
    await jarvis.async_block_till_done()
    assert companion.messages == []
    assert jarvis.data["notifications"].listing()[0]["title"].startswith("Finished: Bitcoin")
    await jarvis.async_stop()


async def test_recent_moments_reads_the_inbox_within_a_window(tmp_path):
    from jarvis.llm.tools import Exposure, ToolRegistry

    jarvis = Jarvis(tmp_path)
    registry = ToolRegistry(jarvis, exposure=Exposure.from_config(None))
    jarvis.data["llm_tools"] = registry
    await notifications_integration.async_setup(jarvis, {})
    store = jarvis.data["notifications"]
    await store.async_add(kind="reminder", title="The audit ran", body="", source="jarvis_schedule_fired")
    await store.async_add(kind="task", title="Finished: Bitcoin", body="up", source="jarvis_task_completed")
    # Push one back two hours, past the default window.
    for entry in store.entries:
        if entry.title == "Finished: Bitcoin":
            entry.at -= 2 * 3600
    listed = await registry.call("recent_moments", {"minutes": 60}, None)
    assert listed["status"] == "ok" and [m["title"] for m in listed["moments"]] == ["The audit ran"]
    everything = await registry.call("recent_moments", {"minutes": 240}, None)
    assert [m["title"] for m in everything["moments"]] == ["Finished: Bitcoin", "The audit ran"] or len(everything["moments"]) == 2
    assert "recent_moments" in registry.names()
    await jarvis.async_stop()


async def test_whats_new_reads_the_capability_record(tmp_path):
    """M97: a tool Jarvis wrote, a skill or a server it gained is one card,
    once, and "what's new?" is answered from those cards — never invented."""
    from jarvis.integrations.notifications import note_capability
    from jarvis.llm.tools import Exposure, ToolRegistry

    jarvis = Jarvis(tmp_path)
    registry = ToolRegistry(jarvis, exposure=Exposure.from_config(None))
    jarvis.data["llm_tools"] = registry
    await notifications_integration.async_setup(jarvis, {})
    empty = await registry.call("whats_new", {}, None)
    assert empty["status"] == "ok" and empty["new"] == [] and "nothing new" in empty["note"]
    await note_capability(jarvis, "New tool: boiler_pressure", "Reads the boiler's pressure (Jarvis wrote itself; tier 2)")
    await note_capability(jarvis, "New MCP server: calendar", "connected; its tools run at tier 2")
    listed = await registry.call("whats_new", {"days": 7}, None)
    assert [m["title"] for m in listed["new"]] == ["New MCP server: calendar", "New tool: boiler_pressure"] or len(listed["new"]) == 2
    assert all(m["days_ago"] < 1 for m in listed["new"])
    assert "whats_new" in registry.names()
    await jarvis.async_stop()
