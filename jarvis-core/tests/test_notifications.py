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
