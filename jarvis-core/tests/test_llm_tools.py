"""Tool calls, announced while they happen.

A turn that called five tools and took nine seconds showed a spinner and
nothing else. Tool calls are the most interesting thing a turn does and they
were the least visible: nothing in the agent told anybody a tool was running,
so no console and no overlay could draw one.

These two events are what those surfaces render, and what this file pins is the
part that makes them worth rendering — that the start is announced BEFORE the
work, that the counts are real, that a tool which answers with an error is not
drawn as a success, and that a listener falling over cannot take the house with
it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.llm.agent import ConversationAgent, ConversationResult  # noqa: E402
from jarvis.llm.ollama import ChatResult, OllamaClient, ToolCall  # noqa: E402
from jarvis.llm.tools import ToolRegistry  # noqa: E402


@pytest.fixture
async def jarvis(tmp_path):
    box = Jarvis(tmp_path)
    await box.async_setup({})
    yield box
    await box.async_stop()


def _call(name: str, arguments: dict) -> ToolCall:
    return ToolCall(name=name, arguments=arguments)


def _chat_result(calls: list[ToolCall]) -> ChatResult:
    return ChatResult(tool_calls=calls)


def _result() -> ConversationResult:
    return ConversationResult()


def _agent_with(box: Jarvis, registry: ToolRegistry) -> ConversationAgent:
    """An agent whose Ollama client is never used.

    `_execute_tool_calls` is the loop under test and it never chats — it takes
    a ChatResult that already exists and runs what is in it. A real client
    pointed at an address nothing answers on proves that by construction: if
    the loop ever reached out, these tests would hang rather than pass.
    """
    return ConversationAgent(
        box,
        client=OllamaClient(url="http://127.0.0.1:1"),
        tools=registry,
    )




# ===========================================================================
# Tool calls, announced while they happen
# ===========================================================================
# A turn that called five tools and took nine seconds showed a spinner and
# nothing else. Tool calls are the most interesting thing a turn does and were
# the least visible; these events are what a console or an overlay draws.


async def test_a_tool_call_is_announced_before_it_runs_and_after(jarvis):
    """Before matters more than after.

    A tool that takes nine seconds should be visible for nine seconds. An event
    fired only on completion is a progress indicator that appears once there is
    no longer any progress to indicate.
    """
    from jarvis.llm.tools import EVENT_TOOL_FINISHED, EVENT_TOOL_STARTED

    seen: list[tuple[str, dict]] = []
    jarvis.bus.listen(EVENT_TOOL_STARTED, lambda e: seen.append(("start", e.data)))
    jarvis.bus.listen(EVENT_TOOL_FINISHED, lambda e: seen.append(("end", e.data)))

    registry = ToolRegistry(jarvis)
    order: list[str] = []

    async def slow(args, context):
        order.append("ran")
        return {"status": "ok"}

    registry.register(name="slow", description="", handler=slow)

    agent = _agent_with(jarvis, registry)
    await agent._execute_tool_calls(
        _chat_result([_call("slow", {})]), [], None, _result()
    )

    kinds = [kind for kind, _ in seen]
    assert kinds == ["start", "end"], kinds
    assert order == ["ran"]
    start = seen[0][1]
    assert start["name"] == "slow"
    assert start["total"] == 1 and start["index"] == 0


async def test_the_progress_numbers_are_real(jarvis):
    """`index`/`total` is what makes a progress bar honest rather than decorative."""
    from jarvis.llm.tools import EVENT_TOOL_STARTED

    starts: list[dict] = []
    jarvis.bus.listen(EVENT_TOOL_STARTED, lambda e: starts.append(e.data))

    registry = ToolRegistry(jarvis)

    async def noop(args, context):
        return {"status": "ok"}

    for name in ("a", "b", "c"):
        registry.register(name=name, description="", handler=noop)

    agent = _agent_with(jarvis, registry)
    await agent._execute_tool_calls(
        _chat_result([_call("a", {}), _call("b", {}), _call("c", {})]), [], None, _result()
    )

    assert [s["index"] for s in starts] == [0, 1, 2]
    assert {s["total"] for s in starts} == {3}
    assert [s["name"] for s in starts] == ["a", "b", "c"]


async def test_a_tool_that_answers_with_an_error_is_not_reported_as_ok(jarvis):
    """`ok` is about what happened to the house, not about whether it threw.

    A tool that returns {"status": "error"} did not work. A surface that drew a
    tick next to it would be lying, and the lie would be about whether a light
    is on.
    """
    from jarvis.llm.tools import EVENT_TOOL_FINISHED

    ends: list[dict] = []
    jarvis.bus.listen(EVENT_TOOL_FINISHED, lambda e: ends.append(e.data))

    registry = ToolRegistry(jarvis)

    async def refuses(args, context):
        return {"status": "error", "error": "no such entity"}

    async def works(args, context):
        return {"status": "ok"}

    registry.register(name="refuses", description="", handler=refuses)
    registry.register(name="works", description="", handler=works)

    agent = _agent_with(jarvis, registry)
    await agent._execute_tool_calls(
        _chat_result([_call("refuses", {}), _call("works", {})]), [], None, _result()
    )

    assert [e["ok"] for e in ends] == [False, True]
    assert ends[0]["error"] == "no such entity"
    assert all(e["duration_ms"] >= 0 for e in ends)


async def test_a_listener_that_throws_does_not_break_the_tool_call(jarvis):
    """The surface is watching the work, not doing it."""
    from jarvis.llm.tools import EVENT_TOOL_STARTED

    def explode(event):
        raise RuntimeError("the console fell over")

    jarvis.bus.listen(EVENT_TOOL_STARTED, explode)

    registry = ToolRegistry(jarvis)
    ran = []

    async def works(args, context):
        ran.append(True)
        return {"status": "ok"}

    registry.register(name="works", description="", handler=works)
    agent = _agent_with(jarvis, registry)
    await agent._execute_tool_calls(_chat_result([_call("works", {})]), [], None, _result())
    assert ran == [True], "a broken listener stopped the tool from running"
