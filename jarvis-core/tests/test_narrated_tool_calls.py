"""A turn that promises work it never dispatched, and a turn that says nothing.

## The report this file comes from

A user asked their assistant, in the console's text chat, for a C++ Snake game.
The model produced a long, confident reasoning block containing lines like

    [Tool Call] -> `code_task`...
    `code_task` called.✅
    `code_task` parameters: repo: "snake_opengl", instruction: "..."

and then replied, in prose, that the coding agent was drafting it and would
report back. **Nothing had been dispatched.** No task, no diff, no branch, no
approval card, and not one line in the log.

Asked "any updates?" a turn later, the model's own reasoning said:

    "But I didn't actually call the code_task function. I should do that now."

and the reply was **completely empty** — a blank bubble under a collapsed
"REASONING · 197 words".

Two defects, and neither had any guard:

**A — the narrated call.** Tool calls are read only from the structured
`tool_calls` field (`ollama.parse_tool_calls`, `openai_compat._ToolCallBuffer`);
nothing has ever scanned `content` or `thinking`. So a scripted call is
indistinguishable, to this server, from a turn that simply chatted. It was
encouraged, too: `config/prompts/jarvis.txt` says flatly "For code, use
code_task" and is read verbatim, while the tools array is built separately from
the registry — and the prompt bounded ENTITIES ("Only the entities listed below
exist") with no equivalent sentence for tools.

**B — the empty turn.** A response that is all reasoning leaves `result.text`
empty; the round loop returns, the pipeline reports `intent-end` with
`speech: ""` and `response_type: "action_done"` — a SUCCESS — and the console
renders a settled, permanent, blank bubble. The only fallback in `converse`
was for `OllamaError`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.llm.agent import (  # noqa: E402
    TURN_EVENT_TOOL_NARRATED,
    ConversationAgent,
    narrated_tool_call,
)
from jarvis.llm.ollama import ChatResult, ToolCall  # noqa: E402
from jarvis.llm.tools import Exposure, ToolRegistry, schema_object  # noqa: E402


@pytest.fixture
def jarvis(tmp_path):
    return Jarvis(tmp_path)


class _Stream:
    """One round: some visible text, some reasoning, maybe a tool call.

    Pushes reasoning through `on_thinking` the way both real clients do — the
    agent sets that attribute on the stream (`_Round.stream`), and a fake that
    only filled `ChatResult.thinking` would never exercise the path where the
    reasoning actually reaches `ConversationResult.thinking`, which is where
    the narrated call was hiding in the reported bug.
    """

    def __init__(self, text: str = "", thinking: str = "", call=None) -> None:
        self._text = text
        self._thinking = thinking
        self.on_thinking = None
        self.result = ChatResult(
            content=text,
            role="assistant",
            thinking=thinking,
            tool_calls=[call] if call else [],
        )

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        if self._thinking and self.on_thinking is not None:
            self.on_thinking(self._thinking)
        if self._text:
            yield self._text

    async def aclose(self):
        return None


class _Scripted:
    def __init__(self, *rounds) -> None:
        self.rounds = list(rounds)
        self.messages: list[list] = []

    def chat(self, **kwargs):
        self.messages.append(list(kwargs.get("messages") or []))
        return self.rounds.pop(0) if self.rounds else _Stream("Very good, Sir.")


def _registry(jarvis: Jarvis) -> ToolRegistry:
    registry = ToolRegistry(jarvis, exposure=Exposure())

    async def _handler(args, context=None):
        return {"status": "started", "task_id": "t1"}

    registry.register(
        name="code_task",
        description="Hand a coding job to the coding agent.",
        parameters=schema_object({"repo": {"type": "string"}}, []),
        handler=_handler,
    )
    return registry


def _agent(jarvis: Jarvis, client, **kw) -> ConversationAgent:
    return ConversationAgent(jarvis, client, _registry(jarvis), **kw)


async def _say(agent, text="write me a snake game"):
    out = []
    async for delta in agent.converse(text):
        out.append(delta)
    return "".join(out)


# ---------------------------------------------------------------------------
# the detector
# ---------------------------------------------------------------------------
NAMES = ["code_task", "turn_on", "get_state"]


def test_it_spots_the_shape_that_was_actually_reported():
    said = (
        "[Tool Call] -> `code_task`...\n"
        '`code_task` parameters: repo: "snake_opengl"\n'
        "`code_task` called.✅"
    )
    assert narrated_tool_call(said, NAMES) == "code_task"


def test_it_spots_a_call_written_as_a_function():
    assert narrated_tool_call('calling code_task(repo="x")', NAMES) == "code_task"


def test_ordinary_prose_is_not_a_narrated_call():
    """The persona tells the model to report outcomes, not service names, so a
    tool name in the answer is already odd — but a false positive costs a
    round, and "the lights are on" must never cost one."""
    assert narrated_tool_call("The kitchen lights are on.", NAMES) == ""
    assert narrated_tool_call("I turned on the lamp for you, Sir.", NAMES) == ""
    assert narrated_tool_call("", NAMES) == ""


def test_an_honest_refusal_naming_the_tool_is_not_a_narrated_call():
    """"I cannot do that" must not be mistaken for "I pretended to do that"."""
    assert narrated_tool_call(
        "I can't run code_task — the orchestrator is not configured.", NAMES
    ) == ""


def test_a_name_that_is_only_part_of_a_word_does_not_match():
    assert narrated_tool_call("calling my_code_taskish helper", NAMES) == ""


def test_it_only_reports_a_tool_that_exists():
    assert narrated_tool_call("calling frobnicate(x=1)", NAMES) == ""


# ---------------------------------------------------------------------------
# A — the narrated call, end to end
# ---------------------------------------------------------------------------
async def test_a_narrated_call_gets_one_chance_to_be_made_properly(jarvis):
    """The fix for the reported bug: the job actually starts.

    Round 1 scripts the call in its reasoning and promises the work. Round 2 —
    which only happens because the narration was noticed — makes the real call.
    """
    client = _Scripted(
        _Stream(
            text="I'll have the coding agent draft that up, Sir.",
            thinking="[Tool Call] -> code_task(repo='snake'). `code_task` called.",
        ),
        _Stream(text="", call=ToolCall(name="code_task", arguments={"repo": "snake"}, id="c1")),
        _Stream(text="It is under way, Sir."),
    )
    agent = _agent(jarvis, client)
    said = await _say(agent)

    assert "under way" in said
    # The correction was put to the model in words it can act on.
    nudge = [
        m
        for round_messages in client.messages
        for m in round_messages
        if m.get("role") == "user" and "did not actually call it" in str(m.get("content"))
    ]
    assert nudge, "the model was never told it had only described the call"


async def test_the_narration_is_announced_not_merely_logged(jarvis):
    """A client showing "still working" beats one that appears to stall."""
    events: list[tuple[str, dict]] = []
    client = _Scripted(
        _Stream(text="Calling code_task now.", thinking=""),
        _Stream(text="I could not, Sir."),
    )
    agent = _agent(jarvis, client)
    async for _ in agent.converse("do it", on_event=lambda n, d: events.append((n, d))):
        pass
    assert TURN_EVENT_TOOL_NARRATED in [name for name, _ in events]


async def test_it_nudges_at_most_once(jarvis):
    """A model that narrates twice will not be argued into it a third time."""
    client = _Scripted(
        _Stream(text="Calling code_task now."),
        _Stream(text="Calling code_task now, really."),
        _Stream(text="Calling code_task now, honestly."),
    )
    agent = _agent(jarvis, client)
    await _say(agent)
    # The nudge is replayed in every later round's history, so counting
    # messages would count the same correction twice. What must not grow is
    # the number of ROUNDS: one narration, one correction, then it stops.
    assert len(client.messages) <= 3


async def test_a_real_tool_call_is_never_mistaken_for_a_narrated_one(jarvis):
    """The turn that works must not pay for this."""
    client = _Scripted(
        _Stream(text="", call=ToolCall(name="code_task", arguments={"repo": "x"}, id="c1")),
        _Stream(text="Started, Sir."),
    )
    agent = _agent(jarvis, client)
    said = await _say(agent)
    assert said.strip() == "Started, Sir."
    assert len(client.messages) == 2, "an extra corrective round was spent"


# ---------------------------------------------------------------------------
# B — the empty turn
# ---------------------------------------------------------------------------
async def test_a_thinking_only_turn_says_something(jarvis):
    """The reported symptom: "REASONING · 197 words" and a blank bubble.

    An empty answer reported as a success is worse than an error — the console
    renders a settled, permanent, empty message and the voice path is silence.
    """
    client = _Scripted(_Stream(text="", thinking="a" * 400))
    agent = _agent(jarvis, client)
    said = await _say(agent)
    assert said.strip(), "the turn produced no text at all"
    assert "ask me again" in said.lower()


async def test_the_empty_turn_is_logged_so_it_can_be_diagnosed(jarvis, caplog):
    import logging

    client = _Scripted(_Stream(text="", thinking="thinking hard"))
    agent = _agent(jarvis, client)
    with caplog.at_level(logging.WARNING):
        await _say(agent)
    assert any("no answer text" in r.message for r in caplog.records)


async def test_whitespace_is_not_an_answer(jarvis):
    """`"   "` passes a truthiness check and is still a blank bubble."""
    client = _Scripted(_Stream(text="   \n  "))
    agent = _agent(jarvis, client)
    assert (await _say(agent)).strip()


async def test_a_normal_turn_is_untouched(jarvis):
    client = _Scripted(_Stream(text="The kitchen lights are on, Sir."))
    agent = _agent(jarvis, client)
    assert (await _say(agent)).strip() == "The kitchen lights are on, Sir."


# ---------------------------------------------------------------------------
# the prompt that encouraged it
# ---------------------------------------------------------------------------
async def test_the_prompt_names_the_tools_that_actually_exist(jarvis):
    """The prose promised tools the registry might not carry.

    `config/prompts/jarvis.txt` says "For code, use code_task" whatever is
    registered, and the prompt bounded entities but not tools. This is the
    missing half of "Only the entities listed below exist".
    """
    agent = _agent(jarvis, _Scripted())
    prompt = agent.system_prompt("hello", [])
    assert "code_task" in prompt
    assert "nothing else exists" in prompt


async def test_a_tool_that_is_not_registered_is_not_promised(jarvis):
    """The actual failure: told it had `code_task`, not handed `code_task`.

    Non-vacuous on purpose. An earlier version of this test split the prompt on
    the toolbox sentence and asserted `code_task` was absent from the tail —
    which passed with the sentence REMOVED, because `split` returns the whole
    string when the separator is missing and the fallback persona happens not
    to name any tool. It was green while testing nothing.
    """
    registry = ToolRegistry(jarvis, exposure=Exposure())

    async def _handler(args, context=None):
        return {"status": "ok"}

    registry.register(
        name="get_state",
        description="Read one entity.",
        parameters=schema_object({}, []),
        handler=_handler,
    )
    agent = ConversationAgent(jarvis, _Scripted(), registry)
    prompt = agent.system_prompt("hello", [])

    marker = "The tools you have are exactly these"
    assert marker in prompt, "the toolbox sentence is missing entirely"
    toolbox = prompt.split(marker)[1].split("\n\n")[0]
    assert "get_state" in toolbox, "the registered tool is not listed"
    assert "code_task" not in toolbox, "a tool the registry does not have was promised"


async def test_the_prompt_forbids_writing_a_call_out_as_text(jarvis):
    agent = _agent(jarvis, _Scripted())
    prompt = agent.system_prompt("hello", [])
    assert "Never write one out as text" in prompt


def test_the_persona_file_names_no_tool_it_cannot_guarantee():
    """The prose that caused this.

    `config/prompts/jarvis.txt` is read VERBATIM into the system prompt with no
    reference to the registry, so any tool name written here is promised to the
    model whether or not it was handed the tool. It said "For code, use
    code_task" — and `code_task` belongs to the `orchestrator` integration,
    which a given install may not have configured at all.

    The toolbox sentence is built from the live registry and is the only place
    a tool should be named.
    """
    persona = (
        Path(__file__).resolve().parents[1] / "config" / "prompts" / "jarvis.txt"
    ).read_text(encoding="utf-8")

    import re

    from jarvis.llm.tools import ToolRegistry, register_builtin_tools

    # A BUILT-IN may be named: `register_builtin_tools` runs on every install,
    # so `get_user_context` is a promise the file can keep. What it may not
    # name is a tool some INTEGRATION registers, because whether that
    # integration is configured is the operator's choice and the file cannot
    # know. That is precisely the gap `code_task` fell through.
    builtins = ToolRegistry(jarvis=None)
    register_builtin_tools(builtins)
    always = set(builtins.names())

    integration_tools = {
        "code_task",
        "start_coding_job",
        "list_code_repositories",
        "delegate_to_agents",
        "execute_command",
        "apply_code_task",
        "code_status",
        "deep_research",
        "schedule_task",
        "web_search",
        "web_fetch",
        "web_browse",
        "read_file",
        "write_file",
        "list_files",
        "search_files",
    } - always

    named = sorted(
        name
        for name in integration_tools
        # Whole word: `light.turn_on` in a "do not say this" example is a
        # SERVICE name, not a promise of the `turn_on` tool.
        if re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", persona)
    )
    assert not named, (
        f"the persona file names {named}, which it cannot guarantee are "
        "registered — those come from integrations an install may not have. "
        "Let the toolbox sentence, built from the live registry, name the "
        "tools instead."
    )
