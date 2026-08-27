"""The kitchen timer (M97's other half): an entity that counts down on the
house's clock, answers "how long is left?" from its attributes, chimes where it
was asked for, and is cancelled by name.

Driven by a fake clock installed as `automation_clock`: sleeping advances the
wall time, so a fifteen-minute timer finishes in one `await`. What this cannot
prove is the spoken chime on a real phone — the live scenario `timer-by-voice`
waits for the inbox card on the house.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations.timer import (  # noqa: E402
    EVENT_TIMER_FINISHED,
    STATE_ACTIVE,
    STATE_FINISHED,
    STATE_IDLE,
    STATE_PAUSED,
    TimerManager,
    clean_label,
    parse_duration,
    slug,
    spoken_duration,
)


class FakeClock:
    """Time moves only when the test says so.

    A clock whose `sleep` advanced the wall time on its own (the automation
    tests' one) lets a countdown run to the end inside any real await — the
    store's file write was enough — and every assertion about "four minutes
    in" raced it. Here `sleep` waits for `advance`, so the countdown wakes
    exactly when the test moves the clock past its tick.
    """

    def __init__(self, start: datetime) -> None:
        self.current = start
        self._moved = asyncio.Event()

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current = self.current + timedelta(seconds=seconds)
        self._moved.set()

    async def sleep(self, seconds: float) -> None:
        wake_at = self.current + timedelta(seconds=seconds)
        while self.current < wake_at:
            self._moved.clear()
            await self._moved.wait()


@pytest.fixture
async def house(tmp_path):
    jarvis = Jarvis(tmp_path)
    clock = FakeClock(datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc))
    jarvis.data["automation_clock"] = clock
    said: list[dict] = []
    cards: list[dict] = []

    async def notify(call):
        said.append(dict(call.data))
        return {"delivered": True}

    async def add(call):
        cards.append(dict(call.data))
        return {"recorded": True}

    await jarvis.async_setup({"timer": {}})
    # AFTER setup: `companion` is a default integration and registers the real
    # `companion.notify` (which delivers to no device here, quietly); the fakes
    # must be the ones the chime reaches.
    jarvis.services.register("companion", "notify", notify, supports_response=True)
    jarvis.services.register("notifications", "add", add, supports_response=True)
    yield jarvis, clock, said, cards
    await jarvis.async_stop()


async def settle(n: int = 6) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def until(condition, seconds: float = 3.0) -> None:
    """The chime runs after a real file write (the store), so it is waited for."""
    deadline = asyncio.get_running_loop().time() + seconds
    while not condition() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)


async def tick(clock: FakeClock, seconds: float) -> None:
    """Move the clock in ten-second steps, letting the countdown wake each time."""
    left = float(seconds)
    while left > 0:
        step = min(10.0, left)
        clock.advance(step)
        left -= step
        await settle(4)


# --- the arithmetic -----------------------------------------------------------


def test_durations_are_read_in_the_shapes_a_tool_argument_arrives_in():
    assert parse_duration(90) == 90
    assert parse_duration("90") == 90
    assert parse_duration("10m") == 600
    assert parse_duration("1h30m") == 5400
    assert parse_duration("10:00") == 600
    assert parse_duration("1:02:03") == 3723
    assert parse_duration("15 seconds") == 15
    assert parse_duration("2 minutes") == 120
    assert parse_duration("ten minutes") is None  # words are the model's to turn into numbers
    assert parse_duration(0) is None and parse_duration(True) is None and parse_duration("") is None


def test_spoken_durations_read_like_a_person():
    assert spoken_duration(15) == "15 seconds"
    assert spoken_duration(60) == "a minute"
    assert spoken_duration(90) == "a minute and 30 seconds"
    assert spoken_duration(600) == "10 minutes"
    assert spoken_duration(3600) == "an hour"
    assert spoken_duration(5400) == "an hour and 30 minutes"
    assert spoken_duration(0) == "no time"


def test_the_label_becomes_the_object_id():
    assert clean_label("The pasta timer.") == "pasta"
    assert slug("the pasta") == "pasta"
    assert slug("Pasta timer") == "pasta"
    assert slug("My Tea!") == "tea"
    assert slug("") == "timer"


# --- the entity -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_timer_is_an_entity_that_counts_down_and_chimes_where_it_was_asked(house):
    jarvis, clock, said, cards = house
    manager: TimerManager = jarvis.data["timer"]
    fired = []
    jarvis.bus.listen(EVENT_TIMER_FINISHED, lambda e: fired.append(e.data))
    result = await manager.async_start("15m", "the pasta timer", {"id": "phone-1", "name": "Kitchen phone"}, "console-1")
    assert result["status"] == "ok" and result["timer"]["entity_id"] == "timer.pasta"
    assert result["timer"]["label"] == "pasta"
    assert result["message"].startswith("Set: the pasta timer, 15 minutes")
    state = jarvis.states.get("timer.pasta")
    assert state.state == STATE_ACTIVE
    assert state.attributes["remaining"] == 900 and state.attributes["remaining_spoken"] == "15 minutes"
    assert state.attributes["device"] == "Kitchen phone"

    # "How long is left?" is read, not computed by a model.
    await tick(clock, 14 * 60 + 30)
    assert manager.status("pasta")["message"] == "The pasta timer has 30 seconds left."
    assert jarvis.states.get("timer.pasta").attributes["remaining"] == 30
    assert jarvis.states.get("timer.pasta").state == STATE_ACTIVE

    await tick(clock, 30)
    await until(lambda: said)
    assert jarvis.states.get("timer.pasta").state == STATE_FINISHED
    assert fired and fired[0]["label"] == "pasta"
    assert cards and cards[0]["title"] == "The pasta timer is done." and cards[0]["kind"] == "reminder"
    assert said and said[0]["message"] == "The pasta timer is done."
    assert said[0]["device_id"] == "phone-1" and said[0]["kind"] == "say"
    assert said[0]["conversation_id"] == "console-1"


@pytest.mark.asyncio
async def test_pause_resume_cancel_and_snooze(house):
    jarvis, clock, said, cards = house
    manager: TimerManager = jarvis.data["timer"]
    await manager.async_start(600, "tea")
    await tick(clock, 240)
    paused = await manager.async_pause("tea")
    assert paused["status"] == "ok" and jarvis.states.get("timer.tea").state == STATE_PAUSED
    assert jarvis.states.get("timer.tea").attributes["remaining"] == 360
    await tick(clock, 3600)  # paused time does not count
    assert jarvis.states.get("timer.tea").attributes["remaining"] == 360
    resumed = await manager.async_resume("the tea timer")
    assert resumed["status"] == "ok" and jarvis.states.get("timer.tea").state == STATE_ACTIVE
    assert (await manager.async_resume("tea"))["status"] == "error"
    cancelled = await manager.async_cancel("tea")
    assert cancelled["status"] == "ok" and cancelled["message"] == "Cancelled the tea timer."
    assert jarvis.states.get("timer.tea").state == STATE_IDLE
    assert (await manager.async_cancel("tea"))["message"] == "The tea timer was not running."
    assert not said

    # A finished timer snoozes: it counts again, five minutes by default.
    await manager.async_start(5, "egg")
    await tick(clock, 5)
    await settle(10)
    assert jarvis.states.get("timer.egg").state == STATE_FINISHED
    snoozed = await manager.async_snooze("egg")
    assert snoozed["status"] == "ok" and jarvis.states.get("timer.egg").state == STATE_ACTIVE
    assert jarvis.states.get("timer.egg").attributes["remaining"] == 300


@pytest.mark.asyncio
async def test_names_resolve_and_the_only_running_timer_needs_no_name(house):
    jarvis, clock, said, cards = house
    manager: TimerManager = jarvis.data["timer"]
    assert manager.status()["message"] == "No timer is running."
    await manager.async_start(120, "pasta")
    assert manager.status()["message"] == "The pasta timer has 2 minutes left."
    assert (await manager.async_pause(""))["status"] == "ok"  # one timer: that one
    await manager.async_start(60, "tea")
    missing = await manager.async_cancel("")
    assert missing["status"] == "error" and "no single timer" in missing["error"]
    wrong = await manager.async_cancel("coffee")
    assert "no timer called 'coffee'" in wrong["error"] and "pasta" in wrong["error"] and "tea" in wrong["error"]
    assert manager.find("timer.pasta") is manager.find("The Pasta Timer")


@pytest.mark.asyncio
async def test_a_bad_duration_is_refused_before_anything_starts(house):
    jarvis, clock, said, cards = house
    manager: TimerManager = jarvis.data["timer"]
    assert (await manager.async_start("soon", "x"))["status"] == "error"
    assert (await manager.async_start(0, "x"))["status"] == "error"
    assert "24 hours" in (await manager.async_start(30 * 3600, "x"))["error"]
    assert jarvis.states.get("timer.x") is None


@pytest.mark.asyncio
async def test_starting_the_same_label_again_replaces_the_countdown(house):
    jarvis, clock, said, cards = house
    manager: TimerManager = jarvis.data["timer"]
    await manager.async_start(600, "pasta")
    again = await manager.async_start(60, "pasta")
    assert again["replaced"] is True and "replaces it" in again["message"]
    assert jarvis.states.get("timer.pasta").attributes["remaining"] == 60


@pytest.mark.asyncio
async def test_the_services_and_the_tool_are_the_same_door(house):
    jarvis, clock, said, cards = house
    answer = await jarvis.services.async_call("timer", "start", {"duration": "10m", "label": "bread"}, blocking=True, return_response=True)
    assert answer["status"] == "ok" and jarvis.states.get("timer.bread").state == STATE_ACTIVE
    answer = await jarvis.services.async_call("timer", "cancel", {"name": "bread"}, blocking=True, return_response=True)
    assert answer["status"] == "ok" and jarvis.states.get("timer.bread").state == STATE_IDLE
    for name in ("start", "pause", "resume", "cancel", "snooze"):
        assert jarvis.services.has_service("timer", name)


@pytest.mark.asyncio
async def test_an_active_timer_does_not_chime_late_after_a_restart(tmp_path):
    jarvis = Jarvis(tmp_path)
    cards: list[dict] = []

    async def add(call):
        cards.append(dict(call.data))
        return {"recorded": True}

    jarvis.services.register("notifications", "add", add, supports_response=True)
    await jarvis.async_setup({"timer": {}})
    manager: TimerManager = jarvis.data["timer"]
    await manager.async_start(3600, "roast")
    await jarvis.async_stop()

    jarvis2 = Jarvis(tmp_path)
    jarvis2.services.register("notifications", "add", add, supports_response=True)
    await jarvis2.async_setup({"timer": {}})
    state = jarvis2.states.get("timer.roast")
    assert state is not None and state.state == STATE_FINISHED
    assert cards and "interrupted when Jarvis restarted" in cards[-1]["title"]
    await jarvis2.async_stop()
