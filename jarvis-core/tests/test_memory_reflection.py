"""M87 — overnight reflection: the day read once, what is new kept and said.

Extraction keeps a fact the moment it is said and never looks back; the
house held "the user's name is Chase" beside "the speaker's name is Chase"
(27 Aug 2026). The reflection asks once for what is NEW against what is
known, stores it as `learned`, never re-learns what was forgotten, and says
nothing at all when there is nothing new.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations.memory import MemoryStore  # noqa: E402
from jarvis.integrations.memory.reflect import EVENT_REFLECTED, Reflection, attach  # noqa: E402


@dataclass
class Turn:
    role: str
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class Conversation:
    id: str
    turns: list[Turn]
    last_active: float = field(default_factory=time.time)


class FakeArchive:
    def __init__(self, *conversations: Conversation) -> None:
        self.conversations = {c.id: c for c in conversations}

    def listing(self):
        return [{"id": c.id, "last_active": c.last_active} for c in self.conversations.values()]

    def get(self, cid):
        return self.conversations.get(cid)


class FakeAgent:
    def __init__(self, *answers: str, archive: FakeArchive | None = None) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.archive = archive or FakeArchive()

    async def ask_once(self, prompt: str, **_kwargs) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else '{"facts": []}'


class Notes:
    def __init__(self, jarvis) -> None:
        self.created = []
        jarvis.services.register("notes", "create", self, supports_response=True)

    async def __call__(self, call):
        self.created.append(dict(call.data))
        return {"id": "n1"}


class Inbox:
    def __init__(self) -> None:
        self.cards = []

    async def async_add(self, **fields):
        self.cards.append(fields)


@pytest.fixture
async def house(tmp_path):
    jarvis = Jarvis(tmp_path)
    memory = MemoryStore(jarvis)
    await memory.async_load()
    jarvis.data["memory"] = memory
    inbox = Inbox()
    jarvis.data["notifications"] = inbox
    notes = Notes(jarvis)
    fired = []
    jarvis.bus.listen(EVENT_REFLECTED, lambda event: fired.append(event.data))
    yield jarvis, memory, inbox, notes, fired
    await jarvis.async_stop()


def _day(*said: str) -> FakeArchive:
    now = time.time()
    return FakeArchive(
        Conversation("console-1", [Turn("user", s, now - 600 + i) for i, s in enumerate(said)] + [Turn("assistant", "Very good, Sir.", now)]),
        Conversation("channel:telegram", [Turn("user", "my bank password is hunter2", now)]),
        Conversation("background-abc", [Turn("user", "audit every sensor", now)]),
    )


async def test_the_day_is_read_once_and_what_is_new_is_kept_as_learned(house):
    jarvis, memory, inbox, notes, fired = house
    await memory.async_add("The user's name is Chase.", source="user")
    agent = FakeAgent(json.dumps({"facts": ["The user has a daughter called Mira, who is allergic to peanuts.", "The user's name is Chase."]}),
                      archive=_day("My daughter is called Mira and she is allergic to peanuts.", "What's the weather?"))
    jarvis.data["llm"] = agent
    reflection = Reflection(jarvis, memory, at=None)

    result = await reflection.reflect()

    assert len(agent.prompts) == 1, "asked once"
    prompt = agent.prompts[0]
    assert "Mira" in prompt and "The user's name is Chase." in prompt, "the known facts are in front of the model"
    assert "hunter2" not in prompt and "audit every sensor" not in prompt, "channels and background work are never read"
    assert result["learned"] == ["The user has a daughter called Mira, who is allergic to peanuts."]
    assert any("duplicate" in s["reason"] or "already" in s["reason"] or "not stored" in s["reason"] for s in result["skipped"]), result["skipped"]
    learned = [e for e in memory.entries if e.source == "learned"]
    assert len(learned) == 1 and "learned" in learned[0].tags
    assert notes.created and notes.created[0]["title"].startswith("What I learned on ")
    assert inbox.cards and inbox.cards[0]["kind"] == "reflection" and "Mira" in inbox.cards[0]["body"]
    assert fired and fired[0]["learned"] == result["learned"]


async def test_nothing_new_means_nothing_said(house):
    jarvis, memory, inbox, notes, fired = house
    agent = FakeAgent('{"facts": []}', archive=_day("Turn the lights off."))
    jarvis.data["llm"] = agent
    result = await Reflection(jarvis, memory, at=None).reflect()
    assert result["learned"] == [] and notes.created == [] and inbox.cards == []


async def test_a_quiet_day_asks_nothing(house):
    jarvis, memory, inbox, notes, fired = house
    agent = FakeAgent(archive=FakeArchive())
    jarvis.data["llm"] = agent
    result = await Reflection(jarvis, memory, at=None).reflect()
    assert result["turns"] == 0 and agent.prompts == [] and result["reason"] == "nothing said today"


async def test_what_the_user_asked_to_forget_is_never_learned_back(house):
    jarvis, memory, inbox, notes, fired = house
    added = await memory.async_add("The user's daughter Mira is allergic to peanuts.", source="user")
    await memory.async_forget(entry_id=added["entry"]["id"])
    assert memory.was_forgotten("The user's daughter Mira is allergic to peanuts.")
    agent = FakeAgent(json.dumps({"facts": ["The user's daughter Mira is allergic to peanuts."]}),
                      archive=_day("Mira, my daughter, can't have peanuts."))
    jarvis.data["llm"] = agent
    result = await Reflection(jarvis, memory, at=None).reflect()
    assert result["learned"] == []
    assert result["skipped"] == [{"fact": "The user's daughter Mira is allergic to peanuts.", "reason": "the user asked to forget this"}]
    assert not [e for e in memory.entries if e.source == "learned"]


async def test_the_forgotten_list_survives_a_reload(tmp_path):
    jarvis = Jarvis(tmp_path)
    memory = MemoryStore(jarvis)
    await memory.async_load()
    added = await memory.async_add("The user likes the hall light dim.", source="user")
    await memory.async_forget(entry_id=added["entry"]["id"])
    again = MemoryStore(jarvis)
    await again.async_load()
    assert again.was_forgotten("the user likes the hall light dim")
    await jarvis.async_stop()


async def test_attach_registers_the_service_and_the_schedule(tmp_path):
    jarvis = Jarvis(tmp_path)
    memory = MemoryStore(jarvis)
    await memory.async_load()
    jarvis.data["memory"] = memory
    jarvis.data["llm"] = FakeAgent(archive=FakeArchive())
    reflection = attach(jarvis, memory, {"reflect_at": "03:30"})
    assert reflection.at == "03:30" and jarvis.services.has_service("memory", "reflect")
    answer = await jarvis.services.async_call("memory", "reflect", {}, blocking=True, return_response=True)
    assert answer["turns"] == 0
    await reflection.stop()
    await jarvis.async_stop()
