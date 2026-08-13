"""Reasoning tokens: generated at full cost, then deleted.

## Why this exists

`ThinkStripper` removes `<think>…</think>` from the output stream, so the block
never reaches the HUD or the TTS. That is correct — nobody wants the model's
deliberation read aloud in a British accent.

What was missing is the other half. `_Round.stream()` called
`client.chat(model, messages, tools, stream, options)` and passed **no `think`
argument**, so the model was never told not to produce one. Every spoken turn
paid for a paragraph of reasoning that was generated, streamed, matched against
two tag literals, and thrown away. On a voice path that cost is silence the user
hears while waiting for the first word.

Three states, not two: `True`, `False`, and unset. Unset must leave the field
out of the request entirely, because that is what every install had before the
key existed and coercing it to `False` would be a silent behaviour change for
everyone who never opted in.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations.llm import _tristate  # noqa: E402
from jarvis.llm.agent import ConversationAgent  # noqa: E402
from jarvis.llm.tools import Exposure, ToolRegistry  # noqa: E402


class _RecordingClient:
    """Captures the kwargs the agent sends, and answers with nothing."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return _EmptyStream()


class _EmptyStream:
    result = type("R", (), {"tool_calls": [], "content": "", "thinking": ""})()

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def aclose(self):
        return None


@pytest.fixture
def jarvis(tmp_path):
    return Jarvis(tmp_path)


def _agent(jarvis: Jarvis, client: _RecordingClient, **kw) -> ConversationAgent:
    return ConversationAgent(
        jarvis, client, ToolRegistry(jarvis, exposure=Exposure()), **kw
    )


async def test_a_turn_says_whether_to_think(jarvis):
    """The regression: `think` was never sent at all."""
    client = _RecordingClient()
    agent = _agent(jarvis, client, think=False)

    async for _ in agent.converse("turn the lamp on"):
        pass

    assert client.calls, "the agent never called the model"
    assert client.calls[0]["think"] is False


async def test_thinking_can_be_asked_for(jarvis):
    """Deliberation earns its keep where the work is planning, not chatting."""
    client = _RecordingClient()
    agent = _agent(jarvis, client, think=True)

    async for _ in agent.converse("design me an automation"):
        pass

    assert client.calls[0]["think"] is True


async def test_unset_leaves_the_models_own_default_alone(jarvis):
    """An install that never set `llm: think:` must behave exactly as before.

    `None` is a third state and has to survive as one all the way to the wire:
    both clients only add the field to the payload when it is not None.
    """
    client = _RecordingClient()
    agent = _agent(jarvis, client)

    async for _ in agent.converse("hello"):
        pass

    assert client.calls[0]["think"] is None


@pytest.mark.parametrize(
    "given,expected",
    [
        (None, None),
        (True, True),
        (False, False),
        ("true", True),
        ("yes", True),
        ("on", True),
        ("1", True),
        ("false", False),
        ("off", False),
        ("0", False),
        ("sometimes", None),  # not a boolean: leave it unset, do not guess
        ("", None),
    ],
)
def test_the_config_value_is_read_as_three_states(given, expected):
    assert _tristate(given) is expected


def test_the_shipped_config_turns_it_off_for_conversation():
    """A voice assistant is the case where this costs the most and buys least.

    If somebody removes this, they should have to mean it — the whole point of
    the key is that the default install stops paying for tokens it deletes.
    """
    text = (
        Path(__file__).resolve().parents[1] / "config/configuration.yaml"
    ).read_text(encoding="utf-8")
    # Read as text rather than through the loader: the loader resolves
    # `!env_var` tags against the environment, and this needs one key.
    llm_block = text.split("\nllm:", 1)[1].split("\n\n#", 1)[0]
    assert "think: false" in llm_block, (
        "the shipped config no longer disables thinking on the conversation "
        "path; see this test's docstring before changing it"
    )


# ---------------------------------------------------------------------------
# retrying a round, but only while nothing has been said
# ---------------------------------------------------------------------------
class _FlakyClient:
    """Fails the first N attempts, then answers."""

    def __init__(self, failures: int, before_failing: str = "") -> None:
        self.failures = failures
        self.before_failing = before_failing
        self.attempts = 0

    def chat(self, **kwargs):
        self.attempts += 1
        if self.attempts <= self.failures:
            return _FailingStream(self.before_failing)
        return _TextStream("Good evening, Sir.")


class _FailingStream:
    result = type("R", (), {"tool_calls": [], "content": "", "thinking": ""})()

    def __init__(self, said: str = "") -> None:
        self._said = said

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        from jarvis.llm.ollama import OllamaError

        # Optionally speak first, which is what makes the retry unsafe.
        if self._said:
            yield self._said
        raise OllamaError("connection reset")

    async def aclose(self):
        return None


class _TextStream:
    result = type("R", (), {"tool_calls": [], "content": "", "thinking": ""})()

    def __init__(self, text: str) -> None:
        self._text = text

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        yield self._text

    async def aclose(self):
        return None


async def test_a_blip_before_the_first_word_is_retried(jarvis):
    """A model server restarting used to cost a whole turn and an apology.

    One `OllamaError` ended it: no backoff, no second attempt. A container
    still warming up or a socket closed by a keep-alive timeout is a blip of a
    few hundred milliseconds.
    """
    client = _FlakyClient(failures=1)
    agent = _agent(jarvis, client)
    agent.retry_backoff = 0.0  # the delay is not what is under test

    said = [delta async for delta in agent.converse("hello")]

    assert client.attempts == 2
    assert "".join(said) == "Good evening, Sir."


async def test_a_failure_mid_sentence_is_not_replayed(jarvis):
    """Retrying after a token has been spoken would repeat it.

    On a voice path the user hears the sentence start again, which is worse
    than the apology. `emitted` is what gates the retry, and this is the case
    that makes the gate necessary rather than cautious.
    """
    client = _FlakyClient(failures=1, before_failing="Good ev")
    agent = _agent(jarvis, client)
    agent.retry_backoff = 0.0

    said = "".join([delta async for delta in agent.converse("hello")])

    assert client.attempts == 1, "the turn was restarted after speaking"
    assert said.startswith("Good ev")
    assert "Good evGood" not in said


async def test_a_model_that_is_genuinely_down_still_gives_up(jarvis):
    """Two attempts, not indefinite.

    A server that is actually off must not turn into a thirty-second wait for
    the same apology.
    """
    client = _FlakyClient(failures=99)
    agent = _agent(jarvis, client)
    agent.retry_backoff = 0.0

    said = "".join([delta async for delta in agent.converse("hello")])

    assert client.attempts == agent.max_attempts
    assert "couldn't reach" in said
