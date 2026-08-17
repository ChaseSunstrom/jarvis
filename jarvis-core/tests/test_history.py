"""Conversation history: the durable half of the assistant's memory.

`ConversationStore` is what the model is told and forgets in fifteen minutes.
`ConversationArchive` is what a person can scroll back through, and these are
the claims the chat console rests on:

* a finished turn is recorded, with the tool calls it made and the reasoning
  it did, and survives a restart;
* reopening a conversation the TTL has already dropped gives the model its
  history back rather than silently starting a new one under the old id;
* a tool *result* never lands in the archive — only whether it worked — so a
  transcript cannot become a store of everything the house ever read;
* deleting a conversation deletes both halves.
"""

from __future__ import annotations

import json

import pytest

from jarvis.llm.agent import ConversationAgent, ConversationResult
from jarvis.llm.history import (
    MAX_FIELD_CHARS,
    MAX_TITLE_CHARS,
    ArchivedConversation,
    ArchivedTurn,
    ConversationArchive,
    summarise_tool_call,
)
from jarvis.llm.memory import ConversationStore
from jarvis.store import Store


# --- the archive itself ----------------------------------------------------
def test_a_recorded_turn_becomes_a_listable_conversation() -> None:
    archive = ConversationArchive()
    archive.record("c1", user_text="is the back door shut?", assistant_text="It is, Sir.")

    rows = archive.listing()
    assert len(rows) == 1
    assert rows[0]["id"] == "c1"
    # The title is the first thing that was said, which is what a sidebar row
    # has to be: nobody names a conversation before having it.
    assert rows[0]["title"] == "is the back door shut?"
    assert rows[0]["turns"] == 2
    assert rows[0]["preview"] == "It is, Sir."


def test_the_listing_carries_no_message_bodies() -> None:
    """A hundred rows to draw a sidebar must not ship a hundred transcripts."""
    archive = ConversationArchive()
    archive.record("c1", user_text="x" * 5000, assistant_text="y" * 5000)

    row = archive.listing()[0]
    assert "turns" in row and isinstance(row["turns"], int)
    # `preview` is the one excerpt, and it is bounded.
    assert len(json.dumps(row)) < 600


def test_an_empty_turn_is_not_recorded() -> None:
    """A history full of blank rows is worse than a short one."""
    archive = ConversationArchive()
    assert archive.record("c1", user_text="", assistant_text="") is None
    assert archive.record("", user_text="hello") is None
    assert archive.listing() == []


def test_conversations_are_listed_newest_first() -> None:
    archive = ConversationArchive()
    for index in range(3):
        archive.record(f"c{index}", user_text=f"turn {index}", assistant_text="mm")
        archive.get(f"c{index}").last_active = 100.0 + index

    assert [row["id"] for row in archive.listing()] == ["c2", "c1", "c0"]


def test_the_archive_evicts_the_oldest_beyond_its_cap() -> None:
    archive = ConversationArchive(max_conversations=3)
    for index in range(6):
        archive.record(f"c{index}", user_text="hi", assistant_text="hello")
        archive.get(f"c{index}").last_active = float(index)
    archive._evict()

    assert len(archive) == 3
    assert {row["id"] for row in archive.listing()} == {"c3", "c4", "c5"}


def test_turns_are_capped_per_conversation() -> None:
    archive = ConversationArchive(max_turns=4)
    for index in range(10):
        archive.record("c1", user_text=f"q{index}", assistant_text=f"a{index}")

    conversation = archive.get("c1")
    assert len(conversation.turns) == 4
    # The tail is what is kept: scrolling back wants the recent end.
    assert conversation.turns[-1].content == "a9"


def test_a_conversation_can_be_renamed_and_keeps_the_name() -> None:
    archive = ConversationArchive()
    archive.record("c1", user_text="turn the lamp down", assistant_text="Done.")

    assert archive.rename("c1", "  evening   lighting  ") is True
    assert archive.listing()[0]["title"] == "evening lighting"
    assert archive.rename("nope", "x") is False

    # A later turn does not overwrite a name the user chose.
    archive.record("c1", user_text="and the other one", assistant_text="Done.")
    assert archive.listing()[0]["title"] == "evening lighting"


def test_a_title_is_bounded() -> None:
    archive = ConversationArchive()
    archive.record("c1", user_text="w" * 500, assistant_text="mm")
    assert len(archive.listing()[0]["title"]) <= MAX_TITLE_CHARS


def test_stored_text_is_bounded_and_stripped_of_control_characters() -> None:
    archive = ConversationArchive()
    archive.record("c1", user_text="a" * (MAX_FIELD_CHARS * 2), assistant_text="ok\x1b[31m\n")

    turns = archive.get("c1").turns
    assert len(turns[0].content) <= MAX_FIELD_CHARS
    # Newlines survive — a transcript has paragraphs. An escape byte does not.
    assert "\x1b" not in turns[1].content
    assert turns[1].content.endswith("\n")


# --- tool calls ------------------------------------------------------------
def test_a_tool_result_is_reduced_to_whether_it_worked() -> None:
    """The one field that could turn a transcript into a document store.

    A tool result holds whatever the tool read — a fetched page, a document
    body, the text off a camera frame. What a person scrolling back wants is
    which tools ran and whether they worked, so that is all this keeps.
    """
    summary = summarise_tool_call(
        {
            "name": "web_fetch",
            "arguments": {"url": "https://example.invalid/x"},
            "result": {"status": "ok", "content": "SECRET PAGE BODY" * 500},
        }
    )

    assert summary["name"] == "web_fetch"
    assert summary["ok"] is True
    assert summary["status"] == "ok"
    assert "SECRET PAGE BODY" not in json.dumps(summary)


def test_a_failed_tool_call_keeps_its_reason() -> None:
    summary = summarise_tool_call(
        {"name": "turn_on", "arguments": {}, "result": {"status": "error", "error": "no such entity"}}
    )
    assert summary["ok"] is False
    assert summary["error"] == "no such entity"


def test_an_approval_gated_call_is_not_reported_as_success() -> None:
    summary = summarise_tool_call(
        {"name": "unlock", "arguments": {}, "result": {"status": "denied"}}
    )
    assert summary["ok"] is False


def test_tool_arguments_are_bounded() -> None:
    summary = summarise_tool_call(
        {"name": "search", "arguments": {"q": "z" * 4000}, "result": {"status": "ok"}}
    )
    assert len(summary["arguments"]["q"]) <= 301


def test_the_number_of_recorded_tool_calls_is_capped() -> None:
    archive = ConversationArchive()
    archive.record(
        "c1",
        user_text="do everything",
        assistant_text="Done.",
        tool_calls=[
            {"name": f"t{i}", "arguments": {}, "result": {"status": "ok"}} for i in range(50)
        ],
    )
    assert len(archive.get("c1").turns[-1].tool_calls) == 12


# --- persistence -----------------------------------------------------------
@pytest.mark.asyncio
async def test_the_archive_survives_a_restart(tmp_path) -> None:
    store = Store(tmp_path, "conversations")
    archive = ConversationArchive(store=store)
    archive.record(
        "c1",
        user_text="turn on the lab lights",
        assistant_text="Done, Sir.",
        tool_calls=[{"name": "turn_on", "arguments": {"name": "lab"}, "result": {"status": "ok"}}],
        thinking="the lab lights are light.lab_strip",
    )
    await archive.async_save()

    # A brand new process, reading the same directory.
    revived = ConversationArchive(store=Store(tmp_path, "conversations"))
    assert await revived.async_load() == 1

    conversation = revived.get("c1")
    assert conversation.title == "turn on the lab lights"
    assert [t.role for t in conversation.turns] == ["user", "assistant"]
    assert conversation.turns[1].content == "Done, Sir."
    assert conversation.turns[1].thinking == "the lab lights are light.lab_strip"
    assert conversation.turns[1].tool_calls[0]["name"] == "turn_on"


@pytest.mark.asyncio
async def test_a_missing_or_corrupt_file_is_not_a_failure_to_boot(tmp_path) -> None:
    archive = ConversationArchive(store=Store(tmp_path, "conversations"))
    assert await archive.async_load() == 0  # nothing there yet

    path = tmp_path / ".storage" / "conversations.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert await ConversationArchive(store=Store(tmp_path, "conversations")).async_load() == 0


@pytest.mark.asyncio
async def test_an_unparseable_row_is_dropped_and_the_rest_survive(tmp_path) -> None:
    store = Store(tmp_path, "conversations")
    await store.save(
        {
            "conversations": [
                {"id": "good", "turns": [{"role": "user", "content": "hi"}]},
                {"no": "id"},
                "not even an object",
                {"id": "also-good", "turns": [{"role": "wat", "content": "x"}]},
            ]
        }
    )
    archive = ConversationArchive(store=Store(tmp_path, "conversations"))

    assert await archive.async_load() == 2
    assert archive.get("good").turns[0].content == "hi"
    # The row survived; its nonsense turn did not.
    assert archive.get("also-good").turns == []


def test_saving_is_scheduled_not_awaited() -> None:
    """Recording a turn must not add disk latency to a reply."""
    scheduled: list[object] = []
    archive = ConversationArchive(
        store=Store("/nonexistent", "conversations"), scheduler=scheduled.append
    )
    archive.record("c1", user_text="hi", assistant_text="hello")

    assert len(scheduled) == 1
    # Close the coroutine we never ran, so the test does not warn.
    scheduled[0].close()


def test_no_store_means_nothing_is_written(tmp_path) -> None:
    """`conversation: history: false` — the feature switched off entirely."""
    archive = ConversationArchive(store=None)
    archive.record("c1", user_text="hi", assistant_text="hello")

    assert archive.listing()[0]["id"] == "c1"  # still works in memory
    assert not (tmp_path / ".storage").exists()


# --- the agent's use of it -------------------------------------------------
class _StubTools:
    exposure = None

    def as_openai_schema(self):
        return []

    def announce(self, *args, **kwargs):
        return None


class _StubJarvis:
    def __init__(self) -> None:
        self.data = {}
        self.areas = type("A", (), {"areas": {}})()
        self.states = type("S", (), {"get": staticmethod(lambda _: None)})()
        self.config_dir = None


def _agent(archive: ConversationArchive, memory: ConversationStore) -> ConversationAgent:
    agent = ConversationAgent.__new__(ConversationAgent)
    agent.jarvis = _StubJarvis()
    agent.memory = memory
    agent.archive = archive
    agent.last_result = ConversationResult()
    agent.last_response = ""
    agent.last_conversation_id = ""
    return agent


def test_reopening_a_forgotten_conversation_restores_its_history() -> None:
    """Clicking a three-day-old conversation and typing continues *that* one.

    Without this the id resolves to an empty shell — `ConversationStore` purged
    it hours ago — and the model is told nothing about the conversation the
    user is plainly looking at.
    """
    archive = ConversationArchive()
    archive.record("old", user_text="what is the lab light called?", assistant_text="light.lab_strip, Sir.")
    memory = ConversationStore()
    agent = _agent(archive, memory)

    conversation = agent._reopen("old")

    assert [t.content for t in conversation.turns] == [
        "what is the lab light called?",
        "light.lab_strip, Sir.",
    ]


def test_reopening_only_restores_what_the_context_window_holds() -> None:
    archive = ConversationArchive()
    for index in range(30):
        archive.record("old", user_text=f"q{index}", assistant_text=f"a{index}")
    memory = ConversationStore(max_turns=6)
    agent = _agent(archive, memory)

    conversation = agent._reopen("old")

    assert len(conversation.turns) == 6
    assert conversation.turns[-1].content == "a29"


def test_a_live_conversation_is_not_re_seeded_from_the_archive() -> None:
    """Restoring on top of a conversation that is already going would duplicate
    every turn of it into the prompt."""
    archive = ConversationArchive()
    archive.record("c1", user_text="first", assistant_text="reply")
    memory = ConversationStore()
    memory.add("c1", "user", "first")
    memory.add("c1", "assistant", "reply")
    agent = _agent(archive, memory)

    assert len(agent._reopen("c1").turns) == 2


def test_a_new_conversation_is_not_reopened() -> None:
    agent = _agent(ConversationArchive(), ConversationStore())
    assert agent._reopen(None).turns == []


def test_finishing_a_turn_records_it_in_both_stores() -> None:
    archive = ConversationArchive()
    memory = ConversationStore()
    agent = _agent(archive, memory)
    result = ConversationResult(
        text="Done, Sir.",
        conversation_id="c1",
        tool_calls=[{"name": "turn_on", "arguments": {}, "result": {"status": "ok"}}],
        thinking="which lamp did they mean",
    )

    agent._finish("c1", result, user_text="lamp off")

    assert [t.content for t in memory.get("c1").turns] == ["lamp off", "Done, Sir."]
    assistant_turn = archive.get("c1").turns[-1]
    assert assistant_turn.thinking == "which lamp did they mean"
    assert assistant_turn.tool_calls[0]["name"] == "turn_on"


def test_a_broken_archive_cannot_cost_the_user_their_turn() -> None:
    class _Exploding(ConversationArchive):
        def record(self, *args, **kwargs):
            raise RuntimeError("disk on fire")

    memory = ConversationStore()
    agent = _agent(_Exploding(), memory)
    agent._finish("c1", ConversationResult(text="Done."), user_text="lamp off")

    # The model's own memory is intact, which is the part that matters.
    assert [t.content for t in memory.get("c1").turns] == ["lamp off", "Done."]


# --- shapes the console parses ---------------------------------------------
def test_the_stored_shape_round_trips() -> None:
    original = ArchivedConversation(id="c1", title="a chat", created=1.0, last_active=2.0)
    original.add(ArchivedTurn("user", "hello"))
    original.add(ArchivedTurn("assistant", "hi", thinking="brief", tool_calls=[{"name": "t"}]))

    revived = ArchivedConversation.from_dict(original.as_dict())

    assert revived is not None
    assert revived.id == "c1"
    assert revived.title == "a chat"
    assert revived.created == 1.0
    assert [t.content for t in revived.turns] == ["hello", "hi"]
    assert revived.turns[1].thinking == "brief"
    assert revived.turns[1].tool_calls == [{"name": "t"}]


def test_messages_are_prompt_shaped_and_drop_the_reasoning() -> None:
    """Replaying a model's own thoughts back to it is how a wrong turn becomes
    a conviction."""
    archive = ConversationArchive()
    archive.record(
        "c1",
        user_text="hi",
        assistant_text="Good evening.",
        thinking="they sound tired",
        tool_calls=[{"name": "t", "arguments": {}, "result": {"status": "ok"}}],
    )

    assert archive.messages("c1") == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Good evening."},
    ]


def test_messages_for_an_unknown_conversation_is_empty_not_an_error() -> None:
    assert ConversationArchive().messages("nope") == []


def test_clearing_removes_everything() -> None:
    archive = ConversationArchive()
    archive.record("c1", user_text="hi", assistant_text="hello")
    archive.record("c2", user_text="hi", assistant_text="hello")

    archive.clear()

    assert archive.listing() == []
    assert archive.remove("c1") is False
