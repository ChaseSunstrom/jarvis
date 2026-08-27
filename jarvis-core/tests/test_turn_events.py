"""Showing the working: tool calls and reasoning, while the turn is running.

A turn that called five tools and thought for nine seconds used to be a
spinner. Three things had to be true for a chat surface to narrate it instead,
and each of them is a claim below:

1. **The agent reports mid-turn.** `converse(on_event=...)` fires as calls
   start and finish and as reasoning arrives — not a summary afterwards, when
   the interesting part is over.
2. **Reasoning is captured without being spoken.** Both wire shapes — inline
   `<think>` and a `reasoning_content` field — reach the same listener, and
   neither reaches the text the TTS says.
3. **The pipeline re-emits them onto the run.** A websocket client sees tool
   rows interleaved with its own text deltas, correlated to its own run,
   without subscribing to the whole house's event bus.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from jarvis.llm.agent import (
    TURN_EVENT_THINKING,
    TURN_EVENT_TOOL_NARRATED,
    TURN_EVENT_TOOL_END,
    TURN_EVENT_TOOL_START,
    ThinkStripper,
)
from jarvis.llm.ollama import OllamaClient
from jarvis.llm.openai_compat import OpenAICompatClient
from jarvis.voice.pipeline import (
    EVENT_INTENT_THINKING,
    EVENT_INTENT_TOOL_NARRATED,
    EVENT_INTENT_TOOL_END,
    EVENT_INTENT_TOOL_START,
    MAX_THINKING_FRAME_CHARS,
    PipelineRun,
)


# --- ThinkStripper: the inline `<think>` shape -----------------------------
def test_the_stripper_hands_over_what_it_removes() -> None:
    seen: list[str] = []
    stripper = ThinkStripper(seen.append)

    visible = stripper.feed("<think>which lamp did they mean</think>Done, Sir.")

    assert visible == "Done, Sir."
    assert "".join(seen) == "which lamp did they mean"


def test_reasoning_split_across_deltas_is_reassembled() -> None:
    """Tokens do not respect tag boundaries, which is the whole difficulty."""
    seen: list[str] = []
    stripper = ThinkStripper(seen.append)

    out = "".join(
        stripper.feed(piece)
        for piece in ["<th", "ink>the ", "kitchen", " lamp</thi", "nk>", "Done."]
    )

    assert out == "Done."
    assert "".join(seen) == "the kitchen lamp"


def test_an_unterminated_think_block_still_reports_what_it_saw() -> None:
    """A model cut off mid-thought: the reasoning so far is real, and the text
    it never got to is not."""
    seen: list[str] = []
    stripper = ThinkStripper(seen.append)

    assert stripper.feed("<think>halfway through a th") == ""
    assert stripper.flush() == ""
    assert "".join(seen).startswith("halfway through a th")


def test_a_stripper_with_no_listener_behaves_exactly_as_before() -> None:
    stripper = ThinkStripper()
    assert stripper.feed("<think>x</think>hello") == "hello"


def test_a_listener_that_throws_cannot_break_the_stream() -> None:
    def _explode(_: str) -> None:
        raise RuntimeError("the console has a rendering bug")

    stripper = ThinkStripper(_explode)
    assert stripper.feed("<think>x</think>hello") == "hello"


# --- the clients: the `thinking` / `reasoning_content` field shape ---------
def _ndjson(*lines: str) -> httpx.MockTransport:
    body = "\n".join(lines).encode()
    return httpx.MockTransport(lambda request: httpx.Response(200, content=body))


@pytest.mark.asyncio
async def test_ollama_reasoning_is_pushed_not_only_accumulated() -> None:
    """The iterator only ticks on a *content* delta, so a model that is
    thinking produces nothing to iterate. Without the push, a surface sees
    silence for the whole of it."""
    client = OllamaClient(
        transport=_ndjson(
            '{"message": {"role": "assistant", "thinking": "let me check "}}',
            '{"message": {"thinking": "the door sensor"}}',
            '{"message": {"content": "It is shut, Sir."}, "done": true}',
        )
    )
    stream = client.chat("m", [{"role": "user", "content": "hi"}])
    seen: list[str] = []
    stream.on_thinking = seen.append

    text = "".join([delta async for delta in stream])

    assert text == "It is shut, Sir."
    assert "".join(seen) == "let me check the door sensor"
    # Still accumulated on the result, for the archive.
    assert stream.result.thinking == "let me check the door sensor"
    await client.aclose()


def _sse(*chunks: str) -> httpx.MockTransport:
    body = ("\n".join(f"data: {c}" for c in chunks) + "\ndata: [DONE]\n").encode()
    return httpx.MockTransport(lambda request: httpx.Response(200, content=body))


@pytest.mark.asyncio
async def test_openai_reasoning_content_reaches_the_same_listener() -> None:
    """vLLM's shape and Ollama's shape must be one event for the client."""
    client = OpenAICompatClient(
        url="http://x/v1",
        transport=_sse(
            '{"choices": [{"delta": {"reasoning_content": "weighing it up"}}]}',
            '{"choices": [{"delta": {"content": "Right away, Sir."}}]}',
        ),
    )
    stream = client.chat("m", [{"role": "user", "content": "hi"}])
    seen: list[str] = []
    stream.on_thinking = seen.append

    text = "".join([delta async for delta in stream])

    assert text == "Right away, Sir."
    assert "".join(seen) == "weighing it up"
    await client.aclose()


@pytest.mark.asyncio
async def test_reasoning_never_appears_in_the_spoken_text() -> None:
    """The claim the TTS depends on."""
    client = OllamaClient(
        transport=_ndjson(
            '{"message": {"content": "<think>not this</think>only this"}, "done": true}'
        )
    )
    stream = client.chat("m", [{"role": "user", "content": "hi"}])
    stripper = ThinkStripper()

    spoken = "".join([stripper.feed(delta) async for delta in stream]) + stripper.flush()

    assert spoken == "only this"
    await client.aclose()


# --- the pipeline: re-emitting onto one run --------------------------------
def _run(events: list[tuple[str, dict]], converse) -> PipelineRun:
    run = PipelineRun(converse=converse, start_stage="intent", end_stage="intent")

    async def _cb(event_type: str, data: dict) -> None:
        events.append((event_type, data))

    run._event_cb = _cb
    return run


@pytest.mark.asyncio
async def test_an_agent_that_takes_on_event_gets_one() -> None:
    captured: list = []

    async def _agent(text, conversation_id=None, on_event=None):
        captured.append(on_event)
        yield "hello"

    events: list[tuple[str, dict]] = []
    run = _run(events, _agent)
    await run.execute(None, run._event_cb, text="hi")

    assert captured and callable(captured[0])


@pytest.mark.asyncio
async def test_an_agent_that_does_not_take_on_event_is_called_as_before() -> None:
    """Every stand-in agent — the service bridge, the no-agent reply, a test's
    two-line coroutine — takes two arguments. Passing a third would break all
    of them for a feature only the real agent implements."""
    seen: list[tuple] = []

    async def _old_agent(text, conversation_id=None):
        seen.append((text, conversation_id))
        return "fine"

    events: list[tuple[str, dict]] = []
    run = _run(events, _old_agent)
    await run.execute(None, run._event_cb, text="hi")

    assert seen == [("hi", run.conversation_id)]
    assert run.response_text == "fine"


@pytest.mark.asyncio
async def test_tool_events_land_on_the_run_in_order() -> None:
    async def _agent(text, conversation_id=None, on_event=None):
        on_event(TURN_EVENT_TOOL_START, {"name": "turn_on", "index": 0, "total": 1})
        yield ""
        on_event(
            TURN_EVENT_TOOL_END,
            {"name": "turn_on", "ok": True, "duration_ms": 12, "index": 0, "total": 1},
        )
        yield "Done, Sir."

    events: list[tuple[str, dict]] = []
    run = _run(events, _agent)
    await run.execute(None, run._event_cb, text="lights on")

    types = [name for name, _ in events]
    assert types == [
        "run-start",
        "intent-start",
        EVENT_INTENT_TOOL_START,
        EVENT_INTENT_TOOL_END,
        "intent-progress",
        "intent-end",
        "run-end",
    ]
    payload = dict(events)[EVENT_INTENT_TOOL_END]
    assert payload["name"] == "turn_on"
    assert payload["ok"] is True


@pytest.mark.asyncio
async def test_tool_rows_arrive_before_the_model_starts_speaking() -> None:
    """A turn that spends nine seconds in tools produces no content delta at
    all. A queue only flushed alongside one would hold the rows back until
    exactly the moment they stop being interesting."""
    started = asyncio.Event()

    async def _agent(text, conversation_id=None, on_event=None):
        on_event(TURN_EVENT_TOOL_START, {"name": "slow_tool"})
        yield ""  # the agent tick that carries no text
        started.set()
        yield "done"

    events: list[tuple[str, dict]] = []
    run = _run(events, _agent)
    await run.execute(None, run._event_cb, text="go")

    types = [name for name, _ in events]
    assert types.index(EVENT_INTENT_TOOL_START) < types.index("intent-progress")


@pytest.mark.asyncio
async def test_reasoning_slices_are_coalesced_into_paragraphs() -> None:
    """One websocket frame per token, between "thinking" and "answered", is
    thousands of writes to render one collapsed block."""

    async def _agent(text, conversation_id=None, on_event=None):
        for word in "a model thinking one token at a time".split():
            on_event(TURN_EVENT_THINKING, {"delta": word + " "})
        yield "Quite."

    events: list[tuple[str, dict]] = []
    run = _run(events, _agent)
    await run.execute(None, run._event_cb, text="hm")

    thinking = [data for name, data in events if name == EVENT_INTENT_THINKING]
    assert len(thinking) == 1
    assert thinking[0]["delta"] == "a model thinking one token at a time "


@pytest.mark.asyncio
async def test_a_very_long_reasoning_block_is_split_across_frames() -> None:
    async def _agent(text, conversation_id=None, on_event=None):
        on_event(TURN_EVENT_THINKING, {"delta": "z" * (MAX_THINKING_FRAME_CHARS * 3)})
        yield "Right."

    events: list[tuple[str, dict]] = []
    run = _run(events, _agent)
    await run.execute(None, run._event_cb, text="hm")

    frames = [data for name, data in events if name == EVENT_INTENT_THINKING]
    assert len(frames) >= 1
    assert sum(len(f["delta"]) for f in frames) == MAX_THINKING_FRAME_CHARS * 3


@pytest.mark.asyncio
async def test_reasoning_reaches_the_client_before_the_tool_it_led_to() -> None:
    """Otherwise the transcript reads as though it decided first and thought
    afterwards."""

    async def _agent(text, conversation_id=None, on_event=None):
        on_event(TURN_EVENT_THINKING, {"delta": "the lab strip, then"})
        on_event(TURN_EVENT_TOOL_START, {"name": "turn_on"})
        yield "Done."

    events: list[tuple[str, dict]] = []
    run = _run(events, _agent)
    await run.execute(None, run._event_cb, text="lights")

    types = [name for name, _ in events]
    assert types.index(EVENT_INTENT_THINKING) < types.index(EVENT_INTENT_TOOL_START)


@pytest.mark.asyncio
async def test_events_reported_after_the_last_delta_are_not_lost() -> None:
    async def _agent(text, conversation_id=None, on_event=None):
        yield "Done."
        on_event(TURN_EVENT_TOOL_END, {"name": "late", "ok": True})

    events: list[tuple[str, dict]] = []
    run = _run(events, _agent)
    await run.execute(None, run._event_cb, text="go")

    assert EVENT_INTENT_TOOL_END in [name for name, _ in events]


@pytest.mark.asyncio
async def test_an_unknown_turn_event_is_ignored_rather_than_forwarded() -> None:
    """The pipeline's event vocabulary is a contract with every client; an
    agent cannot add to it by inventing a name."""

    async def _agent(text, conversation_id=None, on_event=None):
        on_event("something-new", {"x": 1})
        yield "ok"

    events: list[tuple[str, dict]] = []
    run = _run(events, _agent)
    await run.execute(None, run._event_cb, text="go")

    assert "something-new" not in [name for name, _ in events]


# --- the real agent, end to end -------------------------------------------
@pytest.mark.asyncio
async def test_the_real_agent_reports_its_tool_calls_to_one_turns_listener(
    tmp_path,
) -> None:
    """The whole chain: a scripted model asks for a tool, the registry runs it,
    and the caller that asked for events gets both ends of it — while the turn
    is still going."""
    from jarvis.integrations.llm import async_setup as llm_setup

    from test_llm import FakeOllama, build_house, call_tool, say

    jarvis, _objects = await build_house(tmp_path)
    jarvis.data["llm_transport"] = FakeOllama(
        call_tool("turn_on", {"name": "kitchen ceiling"}),
        say("The kitchen light is on, Sir."),
    ).transport
    assert await llm_setup(
        jarvis,
        {"model": "qwen3:8b", "expose": {"domains": ["light"]}},
    )
    agent = jarvis.data["llm"]

    events: list[tuple[str, dict]] = []
    text = "".join(
        [
            delta
            async for delta in agent.converse(
                "kitchen light on", None, lambda name, data: events.append((name, data))
            )
        ]
    )

    assert text == "The kitchen light is on, Sir."
    names = [name for name, _ in events]
    assert TURN_EVENT_TOOL_START in names
    assert TURN_EVENT_TOOL_END in names
    start = dict(events)[TURN_EVENT_TOOL_START]
    assert start["name"] == "turn_on"
    assert start["arguments"] == {"name": "kitchen ceiling"}
    end = dict(events)[TURN_EVENT_TOOL_END]
    assert end["ok"] is True
    assert end["duration_ms"] >= 0
    # And it was archived with the call, for the history list.
    assert agent.archive.get(agent.last_conversation_id).turns[-1].tool_calls[0][
        "name"
    ] == "turn_on"


@pytest.mark.asyncio
async def test_turn_event_arguments_are_bounded_like_the_bus_copy(tmp_path) -> None:
    """`arguments` is a value the model chose the size of. A tool called with a
    megabyte of text must not be pushed whole down one socket per listener to
    be drawn as a row that shows forty characters."""
    from jarvis.integrations.llm import async_setup as llm_setup

    from test_llm import FakeOllama, build_house, call_tool, say

    jarvis, _objects = await build_house(tmp_path)
    jarvis.data["llm_transport"] = FakeOllama(
        call_tool("turn_on", {"name": "kitchen ceiling", "note": "n" * 100_000}),
        say("Done, Sir."),
    ).transport
    assert await llm_setup(jarvis, {"model": "qwen3:8b", "expose": {"domains": ["light"]}})
    agent = jarvis.data["llm"]

    events: list[tuple[str, dict]] = []
    async for _ in agent.converse("go", None, lambda n, d: events.append((n, d))):
        pass

    start = dict(events)[TURN_EVENT_TOOL_START]
    assert len(start["arguments"]["note"]) < 1000


@pytest.mark.asyncio
async def test_a_turn_listener_that_throws_cannot_end_the_conversation(
    tmp_path,
) -> None:
    """A chat console with a rendering bug must not be able to stop the house
    answering."""
    from jarvis.integrations.llm import async_setup as llm_setup

    from test_llm import FakeOllama, build_house, call_tool, say

    jarvis, _objects = await build_house(tmp_path)
    jarvis.data["llm_transport"] = FakeOllama(
        call_tool("turn_on", {"name": "kitchen ceiling"}),
        say("Done, Sir."),
    ).transport
    assert await llm_setup(jarvis, {"model": "qwen3:8b", "expose": {"domains": ["light"]}})
    agent = jarvis.data["llm"]

    def _explode(name: str, data: dict) -> None:
        raise RuntimeError("the console is on fire")

    text = "".join(
        [delta async for delta in agent.converse("kitchen light on", None, _explode)]
    )

    assert text == "Done, Sir."


@pytest.mark.asyncio
async def test_no_listener_is_the_ordinary_case_and_costs_nothing(tmp_path) -> None:
    from jarvis.integrations.llm import async_setup as llm_setup

    from test_llm import FakeOllama, build_house, say

    jarvis, _objects = await build_house(tmp_path)
    jarvis.data["llm_transport"] = FakeOllama(say("Very good, Sir.")).transport
    assert await llm_setup(jarvis, {"model": "qwen3:8b"})
    agent = jarvis.data["llm"]

    assert "".join([d async for d in agent.converse("hello")]) == "Very good, Sir."


@pytest.mark.asyncio
async def test_a_text_run_needs_no_audio_and_can_stop_at_intent() -> None:
    """What the console's chat mode does: words in, words out, no microphone
    and no speaker."""

    async def _agent(text, conversation_id=None, on_event=None):
        yield f"You said {text}."

    events: list[tuple[str, dict]] = []
    run = _run(events, _agent)
    await run.execute(None, run._event_cb, text="hello")

    assert run.error is None
    assert run.response_text == "You said hello."
    assert [name for name, _ in events] == [
        "run-start",
        "intent-start",
        "intent-progress",
        "intent-end",
        "run-end",
    ]


@pytest.mark.asyncio
async def test_a_tool_row_survives_a_turn_that_thinks_before_it_speaks() -> None:
    """The eviction, and the reason it was invisible.

    `_turn_events` is a bounded deque and the intent loop used to be the only
    thing that drained it — on a comment that claimed it ran "on every tick,
    not only when there is text". It did not: `_converse_deltas` drops every
    falsy delta and the agent's rounds yield only visible text, so during
    reasoning and during tool execution the loop never ticked and nothing
    drained. Fill the queue past `MAX_QUEUED_TURN_EVENTS` before the first
    token and the deque evicts from the LEFT — taking the tool rows and the
    reasoning that preceded them, silently.

    Which is exactly the window a tool row exists to narrate: the model thinks,
    calls something slow, and says nothing until it is done.
    """
    from jarvis.voice.pipeline import MAX_QUEUED_TURN_EVENTS

    async def _agent(text, conversation_id=None, on_event=None):
        # The rows go in FIRST. `deque` evicts from the left, so a row is only
        # lost once everything queued after it has pushed it out — which is a
        # model that calls a tool and then reasons at length about the result
        # before saying a word. Nothing ticks the intent loop in between,
        # because the agent yields no visible text until the very end.
        on_event(TURN_EVENT_TOOL_START, {"name": "code_task", "round": 1})
        on_event(TURN_EVENT_TOOL_END, {"name": "code_task", "ok": True})
        for index in range(MAX_QUEUED_TURN_EVENTS + 200):
            on_event(TURN_EVENT_THINKING, {"delta": f"t{index} "})
            if index % 64 == 0:
                # A real turn awaits its socket constantly; this is where a
                # ticker gets its chance to run and the old code got none.
                await asyncio.sleep(0.01)
        yield "Started, Sir."

    events: list[tuple[str, dict]] = []
    run = _run(events, _agent)
    await run.execute(None, run._event_cb, text="write me a snake game")

    names = [name for name, _ in events]
    assert EVENT_INTENT_TOOL_START in names, (
        "the tool row was evicted before it ever reached the client"
    )
    assert EVENT_INTENT_TOOL_END in names
    assert EVENT_INTENT_THINKING in names, "the reasoning went with it"


@pytest.mark.asyncio
async def test_rows_still_precede_the_text_they_were_queued_before() -> None:
    """The ordering the in-loop drain guarantees, kept while adding the ticker.

    Reasoning that preceded a tool call has to reach the client ahead of the
    row for that call, and both ahead of the sentence that follows them, or the
    transcript reads as though the model decided first and thought after.
    """
    async def _agent(text, conversation_id=None, on_event=None):
        on_event(TURN_EVENT_THINKING, {"delta": "weighing it up"})
        on_event(TURN_EVENT_TOOL_START, {"name": "turn_on", "round": 1})
        on_event(TURN_EVENT_TOOL_END, {"name": "turn_on", "ok": True})
        yield "Done, Sir."

    events: list[tuple[str, dict]] = []
    run = _run(events, _agent)
    await run.execute(None, run._event_cb, text="lights on")

    names = [name for name, _ in events]
    assert names.index(EVENT_INTENT_THINKING) < names.index(EVENT_INTENT_TOOL_START)
    assert names.index(EVENT_INTENT_TOOL_END) < names.index("intent-progress")


@pytest.mark.asyncio
async def test_a_narrated_call_reaches_the_client_not_only_the_log() -> None:
    """The correction costs an extra round; the client should be able to say so.

    Without this the turn simply takes longer for no visible reason. The
    pipeline drops any turn event it has no mapping for (`_TURN_EVENT_NAMES`),
    so emitting the event from the agent is only half the wiring.
    """
    async def _agent(text, conversation_id=None, on_event=None):
        on_event(TURN_EVENT_TOOL_NARRATED, {"tool": "code_task", "round": 1})
        yield "Sorry, Sir — I had to do that twice."

    events: list[tuple[str, dict]] = []
    run = _run(events, _agent)
    await run.execute(None, run._event_cb, text="write me a snake game")

    narrated = [data for name, data in events if name == EVENT_INTENT_TOOL_NARRATED]
    assert narrated, "the agent reported it and the pipeline swallowed it"
    assert narrated[0]["tool"] == "code_task"
