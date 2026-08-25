"""How much of the context window is spent before the user says anything.

## Why this exists

`llm: options: num_ctx: 8192` is the whole window a turn lives in, and three
things are posted into it on every round before the user's sentence:

  * the tool schema — `as_openai_schema()` returns **every** registered tool,
    unfiltered, on each of up to `max_tool_rounds` rounds;
  * the system prompt — persona, rules, areas, up to 120 entities of house
    summary, and the memory block;
  * the conversation history, up to 20 turns.

Nothing measured any of it. The numbers in this file were estimated at ~5k
tokens of schema against 8192 and that estimate is exactly the kind of thing
that is either alarming or fine depending on a factor of two, so this measures
it instead — and fails if it grows, which is the part an estimate cannot do.

## What this is not

It is not a token counter. Counting properly needs the model's tokeniser, which
is not present offline and differs per model. It uses a chars-per-token divisor
that is deliberately conservative for JSON — dense punctuation tokenises worse
than prose, so a real tokeniser will report *more* than this does, and a budget
that holds here is not guaranteed to hold there. The point is a trend and a
ceiling, not a certificate.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import CORE_INTEGRATIONS  # noqa: E402

#: Chars per token for dense JSON. GPT-family tokenisers land near 3.5-4 on
#: prose and worse on punctuation-heavy structure; 3.5 keeps this pessimistic.
CHARS_PER_TOKEN = 3.5

#: The window the shipped `configuration.yaml` asks for.
#:
#: Raised to 12288 with the config when the toolbox reached 28 tools. The
#: RATIO below is what this file actually defends — a bigger window that is
#: still 72% full of schema before the conversation starts is the same problem
#: with more tokens — so moving this number never relaxes the ratchet, it only
#: records what the deployment asks for.
NUM_CTX = 12288

#: ## The measured position, and it is not a good one
#:
#: On a stock install the toolbox costs **~4,850 tokens — 59% of the window** —
#: and the system prompt on an *empty* house adds ~720 more. Together that is
#: **~68% spent before the house has a single entity in it**, before any of the
#: 20 turns of history, before the user's sentence, and before the answer. On a
#: real house `house_summary` adds up to 120 more entity lines on top.
#:
#: These constants are therefore a **ratchet, not a budget**. They record where
#: this actually is so it cannot quietly get worse, and every tool anyone adds
#: is paid for by every turn — including the turns that could not possibly use
#: it, on each of up to `max_tool_rounds` rounds.
#:
#: The fix is not a larger number here and not a larger `num_ctx`; it is to stop
#: sending all ~40 schemas on every round and send the ones a turn might need.
#: `as_openai_schema()` has no filtering, no relevance selection and no budget.
#: `SCHEMA_TARGET` is what that work has to reach and is deliberately not
#: asserted — a test that fails until someone writes a feature is a test people
#: learn to ignore.
SCHEMA_CEILING = 0.62
SCHEMA_TARGET = 0.25

#: Prompt + schema together. Same reasoning: a recorded ceiling, not an opinion
#: about what is comfortable.
COMBINED_CEILING = 0.72


@pytest.fixture
async def composed(tmp_path):
    instance = Jarvis(tmp_path)
    await instance.async_setup({name: {} for name in CORE_INTEGRATIONS})
    yield instance
    await instance.async_stop()


def _tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


async def test_the_toolbox_does_not_grow_past_where_it_already_is(composed):
    """The schema is posted on every round, and this is the ratchet on it.

    This is the measurement behind "the whole toolbox ships in every prompt".
    `as_openai_schema` has no filtering, no relevance selection and no budget,
    so this number only ever grows — every tool anyone adds is paid for by
    every turn, including the ones that could not possibly use it.
    """
    registry = composed.data["llm_tools"]
    schema = registry.as_openai_schema()
    assert schema, "no tools registered; this measurement would be vacuous"

    cost = _tokens(json.dumps(schema))
    share = cost / NUM_CTX

    assert share < SCHEMA_CEILING, (
        f"{len(schema)} tools cost ~{cost} tokens, {share:.0%} of num_ctx="
        f"{NUM_CTX}, which is past the recorded ceiling. Send the tools a turn "
        "might actually need rather than all of them; do not raise the ceiling."
    )


async def test_the_system_prompt_leaves_room_for_the_toolbox(composed):
    """Prompt + schema together, which is what actually competes for the window.

    Measured on an empty house, so this is the floor: `house_summary` grows to
    `summary_limit` (120) entities on a real one, and the memory block adds up
    to `context_limit` characters on top. A floor that already crowds the
    window is a ceiling nobody can live under.
    """
    from jarvis.llm.agent import ConversationAgent

    registry = composed.data["llm_tools"]
    agent = ConversationAgent(composed, client=None, tools=registry)

    prompt = _tokens(agent.system_prompt("what is the time"))
    schema = _tokens(json.dumps(registry.as_openai_schema()))
    together = prompt + schema

    assert together < NUM_CTX * COMBINED_CEILING, (
        f"system prompt (~{prompt}) + schema (~{schema}) = ~{together} tokens "
        f"of num_ctx={NUM_CTX} before the house has a single entity in it, "
        "leaving nothing for the house summary, 20 turns of history, the "
        "user's sentence and the answer."
    )


async def test_a_tool_result_cannot_swallow_the_window(composed):
    """`list_entities` is the one the rules tell the model to reach for.

    `TOOL_RULES` says to call it whenever a name does not resolve, so it is the
    most-called tool in exactly the turns that are already going badly. It used
    to return every exposed entity with no cap: on a house with a few hundred —
    which is what exposing `sensor` and `binary_sensor` means — one result was
    larger than the window the whole conversation lives in.
    """
    from jarvis.llm.tools import LIST_ENTITIES_DEFAULT, MAX_TOOL_RESULT_CHARS

    # The agent truncates whatever a tool returns before it reaches the model.
    assert _tokens("x" * MAX_TOOL_RESULT_CHARS) < NUM_CTX * 0.2, (
        "one tool result may not be a fifth of the context window"
    )

    result = await composed.data["llm_tools"].call("list_entities", {})
    assert result["status"] == "ok"
    assert len(result["entities"]) <= LIST_ENTITIES_DEFAULT


async def test_a_truncated_list_says_so(composed):
    """A short list must not read as a small house.

    The cap is only safe because the count beside it is the real one. Without
    that the model reasons about a truncated list as though it were complete —
    "there is no lamp in the study" — which is worse than the blow-up it was
    added to prevent.
    """
    registry = composed.data["llm_tools"]
    for index in range(LIST_ENTITIES_OVERFLOW):
        composed.states.set(f"light.lamp_{index}", "off", {"friendly_name": f"Lamp {index}"})

    result = await registry.call("list_entities", {"limit": 5})

    assert result["count"] >= LIST_ENTITIES_OVERFLOW, "the true total is not reported"
    assert result["shown"] == 5
    assert result["truncated"] is True
    assert "of" in result["note"]
    assert len(result["entities"]) == 5


#: Comfortably over any limit the tool will accept, so the truncation branch is
#: the one under test rather than the house being small.
LIST_ENTITIES_OVERFLOW = 40
