"""Jarvis learns from its own mistakes (M102).

The record is the recorder's traces plus the review's own day log of bus
events; the night asks once and leaves a note and a card; "what went wrong
today?" answers from the record. Driven with a fake recorder, a fake model
and fake notes/notifications services, so what is asserted is the reading of
the record and the writing of the two documents — never the model's prose.
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import time as dt_time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations.review import (  # noqa: E402
    EVENT_GUARDED,
    EVENT_REVIEWED,
    EVENT_STOPPED,
    Review,
    parse_lessons,
)

pytestmark = pytest.mark.asyncio


class FakeAgent:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def ask_once(self, prompt: str, **_kwargs) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else '{"lessons": []}'


class FakeRecorder:
    """Two traces: one clean, one whose web_fetch could not reach the model server."""

    def __init__(self, now: float) -> None:
        self.now = now

    def listing(self, limit: int = 50, kind: str = ""):
        return [
            {"id": "t-clean", "started": self.now - 100, "errors": 0},
            {"id": "t-bad", "started": self.now - 50, "errors": 2},
            {"id": "t-old", "started": self.now - 2 * 86400, "errors": 1},
        ]

    def get(self, trace_id: str):
        if trace_id == "t-bad":
            return {"spans": [
                {"kind": "tool", "name": "web_fetch", "ok": False, "error": "could not reach the model server", "started": self.now - 49},
                {"kind": "tool", "name": "set_timer", "ok": False, "error": "no such entity", "started": self.now - 48},
                {"kind": "tool", "name": "get_state", "ok": True, "error": ""},
            ]}
        if trace_id == "t-old":
            return {"spans": [{"kind": "tool", "name": "old", "ok": False, "error": "ancient", "started": self.now - 2 * 86400}]}
        return {"spans": []}


@pytest.fixture
async def house(tmp_path):
    jarvis = Jarvis(tmp_path)
    notes: list[dict] = []
    cards: list[dict] = []
    reviewed: list[dict] = []

    async def create(call):
        notes.append(dict(call.data))
        return {"id": "note-1"}

    class Inbox:
        async def async_add(self, **row):
            cards.append(row)
            return {"recorded": True}

    await jarvis.async_setup({"review": {"at": "03:45"}})
    jarvis.services.register("notes", "create", create, supports_response=True)
    jarvis.data["notifications"] = Inbox()
    jarvis.data["observability"] = FakeRecorder(time.time())
    jarvis.bus.listen(EVENT_REVIEWED, lambda e: reviewed.append(e.data))
    yield jarvis, notes, cards, reviewed
    await jarvis.async_stop()


def test_lessons_are_parsed_from_json_and_nothing_else():
    assert parse_lessons('{"lessons": ["Say when a device is not in the house", "x"]}') == ["Say when a device is not in the house."]
    assert parse_lessons("Sure! Here are some thoughts") == []
    assert parse_lessons('prose then {"lessons": []}') == []


async def test_the_day_is_the_traces_and_the_log_together(house):
    jarvis, notes, cards, reviewed = house
    review: Review = jarvis.data["review"]
    jarvis.bus.fire(EVENT_GUARDED, {"request": "turn on the disco ball", "said": "Done, Sir."})
    jarvis.bus.fire(EVENT_STOPPED, {"seconds": 1.5, "conversation_id": "c1"})
    await asyncio.sleep(0.05)
    rows = review.day()
    kinds = [r["kind"] for r in rows]
    assert kinds.count("guard") == 1 and kinds.count("stopped") == 1
    assert kinds.count("unreachable") == 1 and kinds.count("tool-error") == 1
    assert not any("ancient" in r["detail"] for r in rows), "yesterday's trace is not today's"
    assert any("disco ball" in r["detail"] for r in rows)
    # The log survives a reload.
    again = Review(jarvis, None, store=review.store)
    await again.async_load()
    assert [r["kind"] for r in again.rows] == ["guard", "stopped"]


async def test_the_review_asks_once_and_leaves_a_note_and_a_card(house):
    jarvis, notes, cards, reviewed = house
    review: Review = jarvis.data["review"]
    agent = FakeAgent('{"lessons": ["When a tool cannot reach the model server, say so and stop rather than try another tool"]}')
    jarvis.data["llm"] = agent
    jarvis.bus.fire(EVENT_GUARDED, {"request": "lock the shed", "said": "Locked, Sir."})
    await asyncio.sleep(0.05)
    result = await review.review()
    assert result["status"] == "ok" and result["events"] == 3
    assert len(agent.prompts) == 1 and "[guard]" in agent.prompts[0] and "[unreachable]" in agent.prompts[0]
    assert result["lessons"] == ["When a tool cannot reach the model server, say so and stop rather than try another tool."]
    assert notes and notes[0]["title"].startswith("What went wrong on ") and "What I will do differently" in notes[0]["body"]
    assert cards and cards[0]["kind"] == "review" and "say so and stop" in cards[0]["body"]
    assert reviewed and reviewed[0]["lessons"] == result["lessons"]
    assert review.last["lessons"] == result["lessons"]


async def test_a_clean_day_leaves_no_card_and_says_so(tmp_path):
    jarvis = Jarvis(tmp_path)
    cards: list[dict] = []

    class Inbox:
        async def async_add(self, **row):
            cards.append(row)

    await jarvis.async_setup({"review": {}})
    jarvis.data["notifications"] = Inbox()
    jarvis.data["llm"] = FakeAgent()
    result = await jarvis.data["review"].review()
    assert result["events"] == 0 and result["reason"] == "nothing went wrong today"
    assert cards == []
    await jarvis.async_stop()


async def test_what_went_wrong_answers_from_the_record(house):
    jarvis, notes, cards, reviewed = house
    registry = jarvis.data["llm_tools"]
    tool = registry.get("what_went_wrong")
    assert tool is not None
    jarvis.bus.fire(EVENT_STOPPED, {"seconds": 2.0})
    await asyncio.sleep(0.05)
    answer = await tool.handler({}, None)
    assert answer["count"] == 3
    assert any(e["kind"] == "stopped" for e in answer["events"])
    assert "Do not add anything the record does not say" in answer["message"]


async def test_the_schedule_is_parsed_and_survives_its_first_tick(house):
    jarvis, notes, cards, reviewed = house
    review: Review = jarvis.data["review"]
    assert review.time_of_day == dt_time(3, 45)
    assert Review.parse_time_of_day("nope") is None
    review.start()
    for _ in range(3):
        await asyncio.sleep(0)
    assert review._task is not None and not review._task.done()
    await review.stop()
