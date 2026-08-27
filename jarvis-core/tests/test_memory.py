"""Memory: what it learns without being told, and what the user can take back.

`test_features.py` covers the store itself — adding, searching, forgetting,
redaction, the trust rules, the prompt block. This is the half M15 added, and
both halves of it are about the same promise: that this is the user's data.

* **Learning.** "Remember that…" works and nobody says it. The facts worth
  keeping arrive in passing, so a turn that states one is offered to a bounded
  model call — and the refusals around that call are the feature.
* **Taking it back.** Export everything, delete everything (including the
  vector sidecar), and see which notes went into the answer you just got.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import memory as memory_integration  # noqa: E402
from jarvis.integrations.memory import MemoryStore, _parse_facts  # noqa: E402


@pytest.fixture
async def store(tmp_path):
    jarvis = Jarvis(tmp_path)
    made = MemoryStore(jarvis)
    await made.async_load()
    jarvis.data["memory"] = made
    return made


class FakeAgent:
    """An agent whose `ask_once` answers with whatever the test scripted."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def ask_once(self, prompt: str, **_kwargs) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else '{"facts": []}'


# --- learning without being told ---------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "My daughter is called Mira and she is seven.",
        "I always have the lights off after eleven.",
        "I prefer the thermostat at nineteen degrees.",
        "Call me Sam, not Samuel, from now on.",
    ],
)
async def test_a_standing_fact_is_worth_a_model_call(store, said):
    assert store.worth_extracting(said) is True


@pytest.mark.parametrize(
    "said",
    [
        "Turn the kitchen light off.",           # an instruction, not a fact
        "What is the temperature outside?",      # a question
        "Thanks.",                               # too short to carry one
        "The kitchen light is on.",              # about the house, not them
    ],
)
async def test_an_ordinary_turn_is_not(store, said):
    """The gate in front of the expensive call. One model call per turn would
    double the load on a box that already takes fifteen seconds to answer."""
    assert store.worth_extracting(said) is False


async def test_a_fact_said_in_passing_is_stored_and_marked_as_extracted(store):
    agent = FakeAgent('{"facts": ["They drink tea, not coffee."]}')
    stored = await store.async_extract(
        "I always drink tea in the morning, never coffee.", agent=agent, conversation_id="c1"
    )

    assert len(stored) == 1
    entry = store.entries[-1]
    assert entry.text == "They drink tea, not coffee."
    # The audit trail: "delete everything you worked out about me yourself" is
    # a filter on this field, and the console shows it.
    assert entry.source == "extracted"
    assert "extracted" in entry.tags
    assert entry.conversation_id == "c1"


async def test_the_transcript_is_never_what_gets_stored(store):
    """A recording of somebody's home in long-term memory is not a feature."""
    agent = FakeAgent('{"facts": []}')
    said = "I always drink tea in the morning, never coffee, and my wife hates it."
    stored = await store.async_extract(said, agent=agent)

    assert stored == []
    assert store.entries == []
    # The prompt carries the sentence, because it has to be read to be judged —
    # but nothing wrote it down.
    assert said[:20] in agent.prompts[0]


async def test_a_word_turns_extraction_off_for_that_turn(store):
    agent = FakeAgent('{"facts": ["Something private."]}')
    stored = await store.async_extract(
        "Off the record, I always drink whisky before bed.", agent=agent
    )
    assert stored == []
    assert agent.prompts == [], "the model was not even asked"


async def test_extraction_is_bounded_per_turn(store):
    agent = FakeAgent(json.dumps({"facts": [f"Fact number {i}." for i in range(10)]}))
    stored = await store.async_extract(
        "I always work late on Tuesdays and my office is upstairs.", agent=agent
    )
    assert len(stored) == memory_integration.MAX_EXTRACTED_PER_TURN


async def test_extraction_goes_through_the_same_refusals_as_everything_else(store):
    """Redaction, the one-line rule and the secret check are not bypassed by a
    fact arriving from the model rather than from the user."""
    agent = FakeAgent('{"facts": ["Their API key is sk-live-0123456789abcdef0123."]}')
    await store.async_extract(
        "I always keep my API key sk-live-0123456789abcdef0123 in the drawer.", agent=agent
    )
    assert all("sk-live-0123456789abcdef0123" not in e.text for e in store.entries)


async def test_a_model_that_fails_costs_the_user_nothing(store):
    class Broken:
        async def ask_once(self, prompt: str, **_kwargs) -> str:
            raise RuntimeError("the model server is down")

    stored = await store.async_extract("I always drink tea in the morning.", agent=Broken())
    assert stored == []


def test_the_facts_are_parsed_however_the_model_wraps_them():
    assert _parse_facts('{"facts": ["a"]}') == ["a"]
    assert _parse_facts('```json\n{"facts": ["a", "b"]}\n```') == ["a", "b"]
    assert _parse_facts("There is nothing worth remembering here.") == []
    assert _parse_facts('{"facts": []}') == []


# --- taking it back -----------------------------------------------------------


async def test_export_is_everything_in_one_document(store):
    await store.async_add("The spare key is in the blue tin.", tags=["house"])
    await store.async_add("They drink tea.", source="extracted")

    payload = store.export()
    assert payload["count"] == 2
    assert {e["text"] for e in payload["entries"]} == {
        "The spare key is in the blue tin.",
        "They drink tea.",
    }
    # And the sources, because "which of these did I actually say?" is the
    # first question anybody asks of their own memory file.
    assert {e["source"] for e in payload["entries"]} == {"user", "extracted"}


async def test_export_as_markdown_is_readable(store):
    await store.async_add("The spare key is in the blue tin.", tags=["house"], pinned=True)
    payload = store.export("markdown")
    assert payload["format"] == "markdown"
    assert "spare key" in payload["text"]
    assert "# What Jarvis remembers" in payload["text"]


async def test_wipe_takes_the_vector_sidecar_with_it(store):
    """A store that reported itself empty while a semantic index still ranked
    the old text would be a promise broken in the least visible way there is."""

    class FakeVectors:
        def __init__(self) -> None:
            self.cleared = False
            self.enabled = True

        async def async_load(self):
            return None

        def is_current(self, entry_id, text):
            return True

        def prune(self, live_ids):
            return 0

        async def async_index(self, items):
            return 0

        async def async_clear(self):
            self.cleared = True

        def forget(self, entry_id):
            return None

    store.vectors = FakeVectors()
    await store.async_add("The spare key is in the blue tin.")
    result = await store.async_wipe()

    assert result["wiped"] == 1
    assert store.entries == []
    assert store.vectors.cleared is True


async def test_the_block_records_which_notes_it_used(store):
    """The answer to "why did it say that?", and the only honest one: a model
    asked to explain itself produces a plausible account of notes it may not
    have read."""
    first = await store.async_add("The spare key is in the blue tin on the shelf.")
    await store.async_add("The bins go out on Thursday.")

    block = store.get_context_block(query="where is the spare key")
    assert "blue tin" in block
    assert first["entry"]["id"] in store.last_used

    # An empty block records nothing rather than the previous turn's ids.
    store.entries = []
    assert store.get_context_block(query="anything") == ""
    assert store.last_used == []


async def test_wipe_is_refused_without_confirmation(tmp_path):
    jarvis = Jarvis(tmp_path)
    await memory_integration.async_setup(jarvis, {})
    store = jarvis.data["memory"]
    await store.async_add("Something worth keeping.")

    refused = await jarvis.services.async_call("memory", "wipe", {}, return_response=True)
    assert refused["wiped"] == 0
    assert "confirm" in refused["error"]
    assert len(store.entries) == 1

    done = await jarvis.services.async_call(
        "memory", "wipe", {"confirm": True}, return_response=True
    )
    assert done["wiped"] == 1
    assert store.entries == []


# --- M100: whose fact it is --------------------------------------------------


async def test_a_fact_is_filed_under_the_person_who_said_it(store):
    """Two people saying the same words are two facts; a typed turn is nobody's."""
    ted = await store.async_add("I take my tea with honey", person="Ted")
    chase = await store.async_add("I take my tea with honey", person="Chase")
    assert ted["stored"] and chase["stored"] and ted["entry"]["person"] == "Ted"
    assert len([e for e in store.entries if "honey" in e.text]) == 2
    house = await store.async_add("The spare key is under the mat")
    assert house["entry"]["person"] == ""
    assert [e.text for e in store.all(person="Ted")] == ["I take my tea with honey"]
    assert len(store.all(person="")) == 1
    # And it survives the file.
    await store.async_load()
    assert {e.person for e in store.entries} == {"Ted", "Chase", ""}


async def test_recall_puts_the_speakers_facts_first_and_labels_another_persons(store):
    await store.async_add("I take my tea with honey", person="Ted")
    await store.async_add("I take my coffee black", person="Chase")
    await store.async_add("The bins go out on Tuesday")
    block = store.get_context_block(person="Chase")
    lines = block.splitlines()[1:]
    assert lines[0] == "- I take my coffee black"
    assert "- The bins go out on Tuesday" in lines
    assert "- Ted: I take my tea with honey" in lines
    # Nobody recognised: nothing is anyone's, everything is labelled.
    block = store.get_context_block()
    assert "- Ted: I take my tea with honey" in block and "- Chase: I take my coffee black" in block


async def test_extraction_files_what_it_learns_under_the_speaker(store):
    stored = await store.async_extract(
        "I always take my tea with honey, and my sister Mira is vegetarian.",
        agent=FakeAgent('{"facts": ["The speaker\'s sister Mira is vegetarian"]}'),
        conversation_id="kitchen-1",
        person="Ted",
    )
    assert stored and stored[0]["entry"]["person"] == "Ted"
