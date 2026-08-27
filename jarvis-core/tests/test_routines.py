"""Jarvis proposes a routine (M104).

The miner is pure and is tested on synthetic days; the flow — card, question,
a yes that makes the automation through the same door the console uses, a no
remembered for thirty days — runs on a real authored store with a fake
recorder and a fake person answering.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations.routines import (  # noqa: E402
    ACTIONS,
    EVENT_PROPOSED,
    Routines,
    automation_for,
    mine,
    slot_of,
)


NOW = datetime(2026, 8, 27, 9, 0)


def at(day: int, hour: int, minute: int) -> float:
    return (NOW - timedelta(days=day)).replace(hour=hour, minute=minute, second=0).timestamp()


def row(entity: str, state: str, when: float) -> dict:
    return {"entity_id": entity, "state": state, "last_changed": when, "last_updated": when}


def kitchen_days(days=(1, 2, 3, 5, 6), minute=(31, 33, 34, 36, 38)):
    return [row("light.kitchen_lights", "off", at(d, 22, m)) for d, m in zip(days, minute)]


def test_the_same_thing_at_the_same_time_on_enough_days_is_a_candidate():
    rows = kitchen_days()
    rows += [row("light.bed_light", "on", at(d, h, 10)) for d, h in ((1, 7), (2, 9), (3, 20))]  # scattered
    rows += [row("light.kitchen_lights", "on", at(4, 18, 5))]
    found = mine(rows, now=NOW.timestamp())
    assert [c["entity_id"] for c in found] == ["light.kitchen_lights"]
    c = found[0]
    assert c["state"] == "off" and c["at"] == "22:30" and c["service"] == "light.turn_off"
    assert len(c["days"]) == 5 and c["key"] == "light.kitchen_lights|off|1350"


def test_fewer_days_than_asked_or_older_than_the_window_is_nothing():
    rows = kitchen_days(days=(1, 2))
    assert mine(rows, now=NOW.timestamp()) == []
    old = [row("light.kitchen_lights", "off", at(d, 22, 30)) for d in (20, 21, 22, 23)]
    assert mine(old, now=NOW.timestamp(), days=14) == []
    assert mine(old, now=NOW.timestamp(), days=30) != []


def test_a_thing_toggled_back_and_forth_in_the_slot_is_not_a_routine():
    rows = kitchen_days()
    # On two of those days it went ON again in the same quarter hour.
    rows += [row("light.kitchen_lights", "on", at(1, 22, 40)), row("light.kitchen_lights", "on", at(2, 22, 41))]
    assert mine(rows, now=NOW.timestamp()) == []
    # One such day is tolerated: a person changing their mind once.
    rows = kitchen_days() + [row("light.kitchen_lights", "on", at(1, 22, 40))]
    assert len(mine(rows, now=NOW.timestamp())) == 1


def test_an_unlock_and_an_automated_entity_are_never_proposed():
    rows = [row("lock.front_door_lock", "unlocked", at(d, 7, 30)) for d in (1, 2, 3, 4)]
    rows += [row("lock.front_door_lock", "locked", at(d, 23, 0)) for d in (1, 2, 3, 4)]
    found = mine(rows, now=NOW.timestamp())
    assert [c["service"] for c in found] == ["lock.lock"]
    assert ("lock", "unlocked") not in ACTIONS
    assert mine(kitchen_days(), now=NOW.timestamp(), excluded=["light.kitchen_lights"]) == []


def test_slots_and_the_automation_a_yes_makes():
    assert slot_of(22 * 60 + 44, 15) == 22 * 60 + 30
    c = {"entity_id": "light.kitchen_lights", "state": "off", "at": "22:30", "days": ["a", "b", "c"], "service": "light.turn_off"}
    auto = automation_for(c, "kitchen lights")
    assert auto["alias"] == "Kitchen lights off at 22:30"
    assert auto["trigger"] == [{"platform": "time", "at": "22:30:00"}]
    assert auto["action"] == [{"service": "light.turn_off", "target": {"entity_id": "light.kitchen_lights"}}]


class FakeRecorder:
    def __init__(self, rows):
        self.rows = rows

    async def states_between(self, entity_ids=None, start=None, end=None, limit=None):
        return list(self.rows)


@pytest.fixture
async def house(tmp_path):
    (tmp_path / "configuration.yaml").write_text("jarvis:\n  name: Test\nautomation: []\n", encoding="utf-8")
    jarvis = Jarvis(tmp_path)
    cards: list[dict] = []
    asked: list[dict] = []
    proposed: list[dict] = []
    answers: list[str] = []

    class Inbox:
        async def async_add(self, **row):
            cards.append(row)
            return {"recorded": True}

    async def ask(call):
        asked.append(dict(call.data))
        return {"answer": answers.pop(0) if answers else ""}

    await jarvis.async_setup({"automation": [], "routines": {"at": "07:05", "days": 14, "min_days": 3}})
    jarvis.services.register("companion", "ask", ask, supports_response=True)
    jarvis.data["notifications"] = Inbox()
    jarvis.data["recorder"] = FakeRecorder(kitchen_days())
    jarvis.bus.listen(EVENT_PROPOSED, lambda e: proposed.append(e.data))
    yield jarvis, cards, asked, proposed, answers
    await jarvis.async_stop()


@pytest.mark.asyncio
async def test_a_yes_makes_the_routine_through_the_automation_store(house):
    jarvis, cards, asked, proposed, answers = house
    routines: Routines = jarvis.data["routines"]
    answers.append("Yes, go ahead.")
    result = await routines.propose(now=NOW.timestamp())
    assert result["proposed"] == ["light.kitchen_lights|off|1350"] and result["made"] == result["proposed"]
    assert cards and cards[0]["kind"] == "proposal" and "shall I make that a routine" in cards[0]["body"]
    assert asked and asked[0]["options"] == ["Yes", "No"] and "22:30" in asked[0]["question"]
    assert proposed and proposed[0]["entity_id"] == "light.kitchen_lights"
    from jarvis.automation.authored import get_authored

    made = get_authored(jarvis).entries()
    assert len(made) == 1 and made[0]["alias"].endswith("off at 22:30")
    assert made[0]["action"][0]["service"] == "light.turn_off"
    # Made once: the next morning it is not put to the person again.
    again = await routines.propose(now=NOW.timestamp())
    assert again["proposed"] == []
    # And the entity is now one an automation acts on, so nothing else about it is mined either.
    assert await routines.candidates(NOW.timestamp()) == []


@pytest.mark.asyncio
async def test_a_no_is_remembered_and_nobody_home_is_not_a_no(house):
    jarvis, cards, asked, proposed, answers = house
    routines: Routines = jarvis.data["routines"]
    answers.append("No, thanks.")
    result = await routines.propose(now=NOW.timestamp())
    assert result["declined"] == ["light.kitchen_lights|off|1350"] and result["made"] == []
    assert await routines.candidates(NOW.timestamp()) == []
    # Thirty days on it may be asked again.
    routines.declined = {k: v - 31 * 86400 for k, v in routines.declined.items()}
    assert len(await routines.candidates(NOW.timestamp())) == 1
    # No answer at all (nobody there): neither made nor declined.
    answers.append("")
    result = await routines.propose(now=NOW.timestamp())
    assert result["made"] == [] and result["declined"] == [] and result["proposed"] == ["light.kitchen_lights|off|1350"]


@pytest.mark.asyncio
async def test_the_tool_lists_and_never_makes(house):
    jarvis, cards, asked, proposed, answers = house
    tool = jarvis.data["llm_tools"].get("proposed_routines")
    answer = await tool.handler({}, None)
    assert answer["count"] == 1 and "22:30" in answer["routines"][0]["says"]
    from jarvis.automation.authored import get_authored

    assert get_authored(jarvis).entries() == []
    assert asked == []


@pytest.mark.asyncio
async def test_a_draft_accepted_over_the_api_becomes_a_routine_and_a_bad_one_is_refused(house):
    jarvis, cards, asked, proposed, answers = house
    routines: Routines = jarvis.data["routines"]
    made = await routines.accept({"entity_id": "light.bed_light", "state": "off", "at": "23:00"})
    assert made["status"] == "ok" and made["automation"]["alias"] == "Bed light off at 23:00"
    refused = await routines.accept({"entity_id": "lock.front_door_lock", "state": "unlocked", "at": "07:00"})
    assert refused["status"] == "error"
    assert (await routines.accept("nope"))["status"] == "error"
