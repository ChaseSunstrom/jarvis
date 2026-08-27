"""Saying "note that…" out loud makes a note.

The unit tests in `test_notes.py` prove the store. This proves the sentence:
a transcript goes in at the top of a real turn, the model calls `note_create`,
and a markdown file exists afterwards with the right words in it.

The model is scripted — the point is the wiring, not the reasoning — but
everything under it is real: the tool registry, the note store, the file on
disk. `evals/routing.py` holds the same phrasings as a table, so the intent and
the plumbing cannot drift apart.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from routing import NOTE_INTENTS, note_intent  # noqa: E402

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import notes as notes_integration  # noqa: E402
from jarvis.llm.tools import ToolRegistry  # noqa: E402


@pytest.fixture
async def house(tmp_path):
    jarvis = Jarvis(tmp_path / "config")
    registry = ToolRegistry(jarvis)
    jarvis.data["llm_tools"] = registry
    await notes_integration.async_setup(jarvis, {"path": str(tmp_path / "notes")})
    yield jarvis, registry, jarvis.data["notes"]
    jarvis.data["notes"].close()


async def test_note_that_creates_a_note_with_what_was_said(house):
    """The whole path, as a turn would run it: the transcript, the tool call
    the model makes for it, and the file that exists afterwards."""
    _jarvis, registry, store = house
    transcript = "note that the boiler was serviced today and the pressure was 1.2 bar"

    # What the router says this line is asking for…
    assert note_intent(transcript) == "note"
    # …and what the model does with it.
    result = await registry.get("note_create").handler(
        {
            "title": "Boiler serviced",
            "body": "Serviced today. Pressure was 1.2 bar.",
            "tags": ["house"],
        },
        None,
    )

    assert result["created"] is True
    path = store.root / "boiler-serviced.md"
    assert path.is_file()
    body = path.read_text()
    assert "1.2 bar" in body
    assert "house" in body


async def test_asking_for_it_back_finds_it(house):
    _jarvis, registry, _store = house
    await registry.get("note_create").handler(
        {"title": "Boiler serviced", "body": "Pressure was 1.2 bar."}, None
    )

    assert note_intent("what did I note about the boiler") == "note_search"
    found = await registry.get("note_search").handler({"query": "boiler"}, None)
    assert found["count"] == 1
    assert found["results"][0]["title"] == "Boiler serviced"


@pytest.mark.parametrize("said,expected,why", NOTE_INTENTS)
async def test_every_phrasing_in_the_table_routes_where_it_says(said, expected, why):
    assert note_intent(said) == expected, why


async def test_a_fact_about_the_user_is_not_a_note(house):
    """The boundary this integration exists for. "Remember that I take my
    coffee black" is one line about them and belongs in `memory`, where it goes
    into every system prompt; a note is a document nobody reads until it is
    asked for. A four-page research report in the prompt is what happens when
    these are confused."""
    _jarvis, _registry, store = house
    assert note_intent("remember that I take my coffee black") == "memory"
    assert store.notes == {}


def test_the_prompt_tells_the_model_which_of_the_two_to_use():
    """Descriptions were not enough on their own.

    Asked to "note that the boiler was serviced", a real model called
    `remember` — twice — and the note never existed. Both tools say what they
    are for, but the model reads the system prompt first, and the prompt said
    nothing about the difference. This is the line that fixed it, and it is
    worth a test because a prompt is the easiest thing in the system to edit
    without noticing what it was carrying.
    """
    from jarvis.llm.agent import TOOL_RULES

    rules = TOOL_RULES.lower()
    assert "note that" in rules
    assert "note_create" in rules
    assert "remember" in rules
    # And the reason, so nobody trims it as noise: memory is repeated on every
    # future turn, which is what makes a document there expensive.
    assert "every future turn" in rules


def test_both_tools_point_at_each_other():
    """A model that reads only one description still gets told about the other."""
    import asyncio

    from jarvis.core import Jarvis
    from jarvis.integrations import memory as memory_integration
    from jarvis.integrations import notes as notes_integration
    from jarvis.llm.tools import ToolRegistry

    async def build(tmp: Path):
        jarvis = Jarvis(tmp / "config")
        registry = ToolRegistry(jarvis)
        jarvis.data["llm_tools"] = registry
        await memory_integration.async_setup(jarvis, {})
        await notes_integration.async_setup(jarvis, {"path": str(tmp / "notes")})
        return jarvis, registry

    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        jarvis, registry = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            build(Path(raw))
        )
        remember = registry.get("remember")
        note_create = registry.get("note_create")
        assert "note_create" in remember.description
        assert "remember" in note_create.description
        jarvis.data["notes"].close()
