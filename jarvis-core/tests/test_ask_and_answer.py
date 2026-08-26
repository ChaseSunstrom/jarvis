"""Ask and answer (M66): one voice, a clock a question can live on, and an
answer that can be said.

The operator reported three things on the same evening. When Jarvis asked
something, the voice said the reply AND the question. A question expired on the
five-minute approval clock, and the answer that came a little after it got
"unknown, expired or already-used approval request". And there was no way to
say "yes" — a held thing could only be tapped.

Each is a claim below, at the layer that owns it:

* the registry puts a question on its own clock (`question_ttl`) and, when one
  has lapsed, says so in words when the answer arrives;
* the registry stamps a request with the conversation and whether the turn is
  spoken, from what the agent recorded, so the phone can be told not to read
  a spoken question out again;
* the agent resolves what waits on the conversation from the next thing said,
  by the contract's rules, and never for a tainted request or for two at once.

What is NOT here: the rules themselves (`test_spoken_answers.py` runs the
contract), and the harness self-test proves the same flow end to end through
the real server.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis.api.devices import mark_untrusted, remember_turn  # noqa: E402
from jarvis.bus import Context  # noqa: E402
from jarvis.core import Jarvis  # noqa: E402
from jarvis.llm.tools import (  # noqa: E402
    DEFAULT_APPROVAL_TTL,
    DEFAULT_QUESTION_TTL,
    EVENT_APPROVAL_EXPIRED,
    TIER_DIRECT,
    ToolRegistry,
    expired_sentence,
    register_builtin_tools,
    schema_object,
)
from jarvis.voice.pipeline import PipelineRun  # noqa: E402
from test_llm import (  # noqa: E402
    FakeOllama,
    build_house,
    call_tool,
    make_agent,
    make_registry,
    say,
    shutdown,
)

pytestmark = pytest.mark.asyncio


# ===========================================================================
# the registry: a question's clock, and a late answer told the truth
# ===========================================================================
@pytest.fixture
async def jarvis(tmp_path):
    box = Jarvis(tmp_path)
    await box.async_setup({})
    yield box
    await box.async_stop()


async def test_a_question_waits_thirty_minutes_and_an_action_five(jarvis):
    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    now = time.time()

    question = await registry.call("ask_user", {"question": "Which lamp?"}, None)
    action = await registry.call("lock_control", {"action": "lock", "name": "front door"}, None)

    assert question["expires_at"] - now == pytest.approx(DEFAULT_QUESTION_TTL, abs=2)
    assert action["expires_at"] - now == pytest.approx(DEFAULT_APPROVAL_TTL, abs=2)
    assert DEFAULT_QUESTION_TTL == 1800.0
    # The clock travels with the request, so the banner and the voice count
    # the same number down.
    by_tool = {r["tool"]: r for r in registry.pending_requests()}
    assert by_tool["ask_user"]["ttl"] == DEFAULT_QUESTION_TTL
    assert by_tool["lock_control"]["ttl"] == DEFAULT_APPROVAL_TTL
    # ...and the model is told how long, so it can say so.
    assert "30 minutes" in question["message"]
    assert "5 minutes" in action["message"]
    assert question["waits_seconds"] == DEFAULT_QUESTION_TTL


async def test_the_question_clock_is_its_own_and_not_derived(jarvis):
    """An operator shortening approvals must not shorten every question."""
    registry = ToolRegistry(jarvis, approval_ttl=60.0, question_ttl=900.0)
    register_builtin_tools(registry)
    now = time.time()
    held = await registry.call("ask_user", {"question": "Which lamp?"}, None)
    assert held["expires_at"] - now == pytest.approx(900.0, abs=2)


async def test_a_lapsed_question_is_answered_in_words(jarvis):
    """The operator's report: answer after the clock, get the three-way guess."""
    registry = ToolRegistry(jarvis, question_ttl=0.0)
    register_builtin_tools(registry)
    lapsed: list[dict] = []
    jarvis.bus.listen(EVENT_APPROVAL_EXPIRED, lambda event: lapsed.append(event.data))

    held = await registry.call("ask_user", {"question": "Which lamp?"}, None)
    late = await registry.approve_request(held["request_id"], True, "the corner one")

    assert late["status"] == "error"
    assert late["expired"] is True
    assert late["error"] == "That question expired after 0 seconds; ask again and I'll wait."
    assert late["tool"] == "ask_user"
    # The surfaces are told the same thing, once, when the registry notices.
    assert len(lapsed) == 1
    assert lapsed[0]["request_id"] == held["request_id"]
    assert lapsed[0]["expired"] is True
    assert lapsed[0]["answerable"] == "answer"


async def test_a_lapsed_action_names_the_tool_and_its_clock(jarvis):
    registry = ToolRegistry(jarvis, approval_ttl=0.0)
    register_builtin_tools(registry)
    held = await registry.call("lock_control", {"action": "lock", "name": "front door"}, None)
    late = await registry.approve_request(held["request_id"], True)
    assert late["expired"] is True
    assert late["error"] == (
        "That request to lock_control expired after 0 seconds; ask again and I'll hold it for you."
    )


async def test_the_expiry_sentence_says_minutes_when_it_is_minutes():
    assert expired_sentence("ask_user", 1800, True) == (
        "That question expired after 30 minutes; ask again and I'll wait."
    )
    assert expired_sentence("lock_control", 300, False) == (
        "That request to lock_control expired after 5 minutes; ask again and I'll hold it for you."
    )
    assert "90 seconds" in expired_sentence("ask_user", 90, True)


async def test_an_id_the_registry_never_held_still_gets_the_honest_guess(jarvis):
    """The three-way sentence stays for what it is true of: an id nobody
    raised, or one spent so long ago that it fell out of the lapsed memory."""
    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    unknown = await registry.approve_request("never-raised", True)
    assert unknown["status"] == "error"
    assert "expired" not in unknown
    assert unknown["error"] == "unknown, expired or already-used approval request"

    # Spent, not lapsed: answered once, then again.
    held = await registry.call("ask_user", {"question": "Which lamp?"}, None)
    assert (await registry.approve_request(held["request_id"], True, "desk"))["status"] == "executed"
    again = await registry.approve_request(held["request_id"], True, "desk")
    assert "expired" not in again


async def test_a_lapsed_request_never_runs(jarvis):
    """Lapsing is remembered, not queued: the answer that comes late runs
    nothing, however affirmative it is."""
    registry = ToolRegistry(jarvis, approval_ttl=0.0)
    ran: list[dict] = []

    async def _handler(args, context):
        ran.append(args)
        return {"status": "ok"}

    registry.register(
        name="unlock_gate",
        description="Unlock the gate.",
        parameters=schema_object({"target": {"type": "string"}}),
        handler=_handler,
        tier=3,
    )
    held = await registry.call("unlock_gate", {"target": "lock.gate"}, None)
    late = await registry.approve_request(held["request_id"], True)
    assert late["expired"] is True
    assert ran == []


async def test_the_lapsed_memory_is_bounded(jarvis):
    from jarvis.llm.tools import MAX_LAPSED

    registry = ToolRegistry(jarvis, question_ttl=0.0)
    register_builtin_tools(registry)
    first = await registry.call("ask_user", {"question": "first?"}, None)
    for _ in range(MAX_LAPSED + 5):
        await registry.call("ask_user", {"question": "again?"}, None)
    registry.purge_expired()
    assert len(registry._lapsed) == MAX_LAPSED
    # The oldest fell out and is back to the honest guess.
    assert "expired" not in await registry.approve_request(first["request_id"], True, "x")


# ===========================================================================
# the registry: what the agent recorded is stamped on the request
# ===========================================================================
async def test_a_request_is_stamped_with_its_conversation_and_whether_it_is_spoken(jarvis):
    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    spoken_turn = Context(origin="llm")
    typed_turn = Context(origin="llm")
    remember_turn(jarvis, spoken_turn, "conv-voice", spoken=True)
    remember_turn(jarvis, typed_turn, "conv-typed", spoken=False)

    voiced = await registry.call("ask_user", {"question": "Which lamp?"}, spoken_turn)
    typed = await registry.call("ask_user", {"question": "Which URL?"}, typed_turn)
    nobodys = await registry.call("ask_user", {"question": "Whose?"}, None)

    by_id = {r["request_id"]: r for r in registry.pending_requests()}
    assert by_id[voiced["request_id"]]["conversation_id"] == "conv-voice"
    assert by_id[voiced["request_id"]]["spoken"] is True
    assert by_id[typed["request_id"]]["conversation_id"] == "conv-typed"
    assert by_id[typed["request_id"]]["spoken"] is False
    # A request raised outside any turn belongs to no conversation and is not
    # spoken — the reading under which nothing resolves by accident.
    assert by_id[nobodys["request_id"]]["conversation_id"] is None
    assert by_id[nobodys["request_id"]]["spoken"] is False

    # Listing by conversation returns only what was stamped with it.
    assert [r["request_id"] for r in registry.pending_for_conversation("conv-voice")] == [
        voiced["request_id"]
    ]
    assert registry.pending_for_conversation(None) == []
    assert registry.pending_for_conversation("conv-nobody") == []


async def test_the_phone_is_told_a_spoken_question_is_already_spoken(jarvis):
    """The single voice: the reply says the question; the phone shows it."""
    from jarvis.integrations.llm import _bridge_questions_to_the_phone

    asked: list[dict] = []

    async def _ask(call):
        asked.append(dict(call.data))
        return {"answer": None}

    jarvis.services.register("companion", "ask", _ask, supports_response=True)
    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    _bridge_questions_to_the_phone(jarvis, registry)

    spoken_turn = Context(origin="llm")
    remember_turn(jarvis, spoken_turn, "conv-voice", spoken=True)
    typed_turn = Context(origin="llm")
    remember_turn(jarvis, typed_turn, "conv-typed", spoken=False)

    await registry.call("ask_user", {"question": "Which lamp?"}, spoken_turn)
    await registry.call("ask_user", {"question": "Which URL?"}, typed_turn)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(asked) == 2:
            break

    by_question = {a["question"]: a for a in asked}
    assert by_question["Which lamp?"]["spoken"] is True
    assert by_question["Which URL?"]["spoken"] is False
    # And the phone is asked for as long as the question lives, not the
    # action clock.
    assert by_question["Which lamp?"]["timeout"] == pytest.approx(DEFAULT_QUESTION_TTL, abs=5)


async def test_the_companion_puts_spoken_on_the_wire(tmp_path):
    from jarvis.integrations.companion import CompanionManager
    from jarvis.presence import PresenceRegistry

    box = Jarvis(tmp_path)
    await box.async_setup({"companion": {}})
    try:
        manager: CompanionManager = box.data["companion"]
        presence: PresenceRegistry = box.data["presence"]
        presence.register("phone", "Pixel", "android", ["ask"])
        phone = presence.devices["phone"]
        phone.last_seen = time.time()
        phone.screen_on = True
        phone.locked = False
        sent: list[dict] = []

        async def transport(device_id, payload):
            sent.append(payload)
            return True

        manager.set_transport(transport)
        task = asyncio.create_task(
            box.services.async_call(
                "companion",
                "ask",
                {"question": "Which lamp?", "spoken": True, "timeout": 5},
                blocking=True,
                return_response=True,
            )
        )
        for _ in range(50):
            await asyncio.sleep(0.01)
            if sent:
                break
        assert sent and sent[0]["spoken"] is True
        manager.on_device_answer(sent[0]["message_id"], "desk")
        await task
        # The default is the old wire, so an older caller changes nothing.
        result = await manager.send("Plain?", kind="notify")
        assert result["status"] == "delivered"
        assert sent[-1]["spoken"] is False
    finally:
        await box.async_stop()


# ===========================================================================
# the pipeline says whether it will speak
# ===========================================================================
async def test_the_pipeline_tells_the_agent_whether_the_reply_is_spoken(tmp_path):
    box = Jarvis(tmp_path)
    await box.async_setup({})
    try:
        seen: list[dict] = []

        async def converse(text, conversation_id=None, **kwargs):
            seen.append(kwargs)
            return "Very good, Sir."

        class _Tts:
            def synthesize(self, text, voice=None):
                return b"\x00\x00" * 160, 16000, 2, 1

        spoken = PipelineRun(
            box, converse=converse, tts=_Tts(), start_stage="intent", end_stage="tts"
        )
        await spoken.execute_text("hello")
        typed = PipelineRun(box, converse=converse, start_stage="intent", end_stage="intent")
        await typed.execute_text("hello")

        assert seen[0]["spoken"] is True
        assert seen[1]["spoken"] is False

        # A two-argument stand-in is called as it always was.
        plain: list[str] = []

        async def two_args(text, conversation_id=None):
            plain.append(text)
            return "Very good, Sir."

        run = PipelineRun(box, converse=two_args, start_stage="intent", end_stage="intent")
        await run.execute_text("hello")
        assert plain == ["hello"]
    finally:
        await box.async_stop()


# ===========================================================================
# the agent: the next thing said is the answer
# ===========================================================================
ASK = call_tool("ask_user", {"question": "Which lamp?", "choices": ["Desk lamp", "Corner lamp"]})
LOCK = call_tool("lock_control", {"action": "lock", "name": "front door"})


def _system_notes(fake: FakeOllama) -> list[str]:
    return [m["content"] for m in fake.last_messages if m["role"] == "system"]


async def test_a_question_is_answered_by_the_next_turn(tmp_path):
    jarvis, _ = await build_house(tmp_path)
    fake = FakeOllama(ASK, say("Which lamp, Sir — the desk or the corner?"), say("The corner lamp, Sir."))
    registry = make_registry(jarvis)
    agent = make_agent(jarvis, fake, registry)

    first = "".join([d async for d in agent.converse("turn on the lamp")])
    conversation = agent.last_conversation_id
    assert first == "Which lamp, Sir — the desk or the corner?"
    waiting = registry.pending_for_conversation(conversation)
    assert [r["tool"] for r in waiting] == ["ask_user"]

    second = "".join([d async for d in agent.converse("the corner one", conversation)])

    assert second == "The corner lamp, Sir."
    assert registry.pending_for_conversation(conversation) == []
    # The turn carried the resolution as a tool call — the answer is the
    # choice's own text, and it reached exactly the one argument.
    resolved = agent.last_result.tool_calls[0]
    assert resolved["name"] == "ask_user"
    assert resolved["arguments"]["answer"] == "Corner lamp"
    assert resolved["result"]["answer"] == "Corner lamp"
    # The model was told, before the user's words, which stayed last.
    notes = _system_notes(fake)
    assert any("answers the question you asked earlier" in n for n in notes)
    assert fake.last_messages[-1] == {"role": "user", "content": "the corner one"}
    assert len(fake.requests) == 3
    await shutdown(jarvis)


async def test_a_held_action_is_confirmed_by_a_yes(tmp_path):
    jarvis, objects = await build_house(tmp_path)
    fake = FakeOllama(LOCK, say("That needs your go-ahead, Sir."), say("Locked, Sir."))
    registry = make_registry(jarvis)
    agent = make_agent(jarvis, fake, registry)

    async for _ in agent.converse("lock the front door"):
        pass
    conversation = agent.last_conversation_id
    assert objects["lock.front_door"].calls == []

    reply = "".join([d async for d in agent.converse("Yes, go ahead.", conversation)])

    assert reply == "Locked, Sir."
    assert objects["lock.front_door"].calls, "the confirmed action did not run"
    assert registry.pending_for_conversation(conversation) == []
    assert any("confirms the held action `lock_control`" in n for n in _system_notes(fake))
    resolved = agent.last_result.tool_calls[0]
    assert resolved["name"] == "lock_control"
    # Pinned when raised, so what ran is what was shown.
    assert resolved["arguments"]["entity_id"] == ["lock.front_door"]
    await shutdown(jarvis)


async def test_a_no_declines_and_nothing_runs(tmp_path):
    jarvis, objects = await build_house(tmp_path)
    fake = FakeOllama(LOCK, say("That needs your go-ahead, Sir."), say("Very well, Sir."))
    registry = make_registry(jarvis)
    agent = make_agent(jarvis, fake, registry)
    async for _ in agent.converse("lock the front door"):
        pass
    conversation = agent.last_conversation_id

    reply = "".join([d async for d in agent.converse("no", conversation)])

    assert reply == "Very well, Sir."
    assert objects["lock.front_door"].calls == []
    assert registry.pending_for_conversation(conversation) == []
    assert any("declines the held action" in n for n in _system_notes(fake))
    await shutdown(jarvis)


async def test_an_unrelated_turn_leaves_it_waiting_and_says_so(tmp_path):
    """'turn on the kitchen light' while an unlock waits is not a yes."""
    jarvis, objects = await build_house(tmp_path)
    fake = FakeOllama(LOCK, say("That needs your go-ahead, Sir."), say("The kitchen light is on."))
    registry = make_registry(jarvis)
    agent = make_agent(jarvis, fake, registry)
    async for _ in agent.converse("lock the front door"):
        pass
    conversation = agent.last_conversation_id

    async for _ in agent.converse("what time is it", conversation):
        pass

    assert objects["lock.front_door"].calls == []
    assert [r["tool"] for r in registry.pending_for_conversation(conversation)] == ["lock_control"]
    assert any("Still waiting on the user's confirmation" in n for n in _system_notes(fake))
    assert agent.last_result.tool_calls == []
    await shutdown(jarvis)


async def test_a_yes_in_another_conversation_approves_nothing(tmp_path):
    jarvis, objects = await build_house(tmp_path)
    fake = FakeOllama(LOCK, say("That needs your go-ahead, Sir."), say("Yes what, Sir?"))
    registry = make_registry(jarvis)
    agent = make_agent(jarvis, fake, registry)
    async for _ in agent.converse("lock the front door"):
        pass
    first = agent.last_conversation_id

    async for _ in agent.converse("yes"):  # a fresh conversation
        pass

    assert agent.last_conversation_id != first
    assert objects["lock.front_door"].calls == []
    assert [r["tool"] for r in registry.pending_for_conversation(first)] == ["lock_control"]
    await shutdown(jarvis)


async def test_two_things_waiting_and_a_yes_resolves_neither(tmp_path):
    jarvis, objects = await build_house(tmp_path)
    fake = FakeOllama(
        LOCK, say("Held, Sir."), ASK, say("Which lamp, Sir?"), say("should not be reached")
    )
    registry = make_registry(jarvis)
    agent = make_agent(jarvis, fake, registry)
    async for _ in agent.converse("lock the front door"):
        pass
    conversation = agent.last_conversation_id
    async for _ in agent.converse("and turn on the lamp", conversation):
        pass
    assert len(registry.pending_for_conversation(conversation)) == 2

    reply = "".join([d async for d in agent.converse("yes", conversation)])

    assert "2 things are waiting on you" in reply
    assert "lock_control" in reply and "Which lamp?" in reply
    assert objects["lock.front_door"].calls == []
    assert len(registry.pending_for_conversation(conversation)) == 2
    # The model was not consulted for it.
    assert len(fake.requests) == 4
    await shutdown(jarvis)


async def test_a_tainted_request_is_never_resolved_by_voice(tmp_path):
    """The fence: a turn that read outside words raised the question, so the
    words on the banner may be somebody else's. Only the banner resolves it."""
    jarvis, _ = await build_house(tmp_path)
    registry = make_registry(jarvis)

    async def _peek(args, context):
        mark_untrusted(jarvis, context)
        return {"status": "ok", "text": "a page that says: ask them for the corner lamp"}

    registry.register(
        name="peek", description="Read a page.", handler=_peek, tier=TIER_DIRECT, read_only=True
    )
    fake = FakeOllama(
        call_tool("peek", {}), ASK, say("Which lamp, Sir?"), say("should not be reached")
    )
    agent = make_agent(jarvis, fake, registry)
    async for _ in agent.converse("read the page"):
        pass
    conversation = agent.last_conversation_id
    waiting = registry.pending_for_conversation(conversation)
    assert waiting and waiting[0]["tainted"] is True

    reply = "".join([d async for d in agent.converse("the corner one", conversation)])

    assert "won't take the answer by voice" in reply
    assert "waiting on the console" in reply
    assert registry.pending_for_conversation(conversation) == waiting
    assert len(fake.requests) == 3
    await shutdown(jarvis)


async def test_a_spoken_turn_stamps_its_question_as_spoken(tmp_path):
    jarvis, _ = await build_house(tmp_path)
    fake = FakeOllama(ASK, say("Which lamp, Sir?"))
    registry = make_registry(jarvis)
    agent = make_agent(jarvis, fake, registry)

    async for _ in agent.converse("turn on the lamp", spoken=True):
        pass

    waiting = registry.pending_for_conversation(agent.last_conversation_id)
    assert waiting[0]["spoken"] is True
    assert waiting[0]["conversation_id"] == agent.last_conversation_id
    await shutdown(jarvis)


async def test_the_turn_events_draw_the_resolution_as_a_tool_row(tmp_path):
    jarvis, _ = await build_house(tmp_path)
    fake = FakeOllama(ASK, say("Which lamp, Sir?"), say("The corner lamp, Sir."))
    registry = make_registry(jarvis)
    agent = make_agent(jarvis, fake, registry)
    async for _ in agent.converse("turn on the lamp"):
        pass
    conversation = agent.last_conversation_id
    events: list[tuple[str, dict]] = []

    async for _ in agent.converse("the corner one", conversation, lambda t, d: events.append((t, d))):
        pass

    kinds = [t for t, _ in events]
    assert kinds[:2] == ["tool-start", "tool-end"]
    assert events[0][1]["name"] == "ask_user"
    assert events[1][1]["ok"] is True
    await shutdown(jarvis)
