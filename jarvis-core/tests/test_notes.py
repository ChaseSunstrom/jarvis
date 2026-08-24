"""Notes: markdown files you own, that Jarvis can read and write.

The design claim is that the FILES are the notes and everything else is
derived. So the tests are mostly about that: a note written by Jarvis is a
markdown file a person can read; a markdown file a person drops in is a note;
deleting the index does not delete anything; and a rename does not break a
link.

The other half is the boundary with `memory`. A note is a document — as long as
it needs to be, found by searching. A memory is one line about the user, in
every system prompt. Research writing its reports into memory is what made that
distinction worth an integration.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import notes as notes_integration  # noqa: E402
from jarvis.integrations.notes import NoteStore, links_in, slugify  # noqa: E402
from jarvis.llm.tools import ToolRegistry  # noqa: E402


@pytest.fixture
async def store(tmp_path):
    jarvis = Jarvis(tmp_path / "config")
    made = NoteStore(jarvis, tmp_path / "notes")
    made.load()
    jarvis.data["notes"] = made
    yield made
    made.close()


# --- the file is the note -----------------------------------------------------


async def test_create_writes_a_markdown_file_a_person_can_read(store):
    result = store.create("Boiler serviced", "Pressure 1.2 bar cold.", ["house", "maintenance"])
    assert result["created"] is True

    path = store.root / "boiler-serviced.md"
    assert path.is_file()
    text = path.read_text()
    assert text.startswith("---")
    assert "title: Boiler serviced" in text
    assert "Pressure 1.2 bar cold." in text
    # Tags in the frontmatter, so the file carries its own metadata rather than
    # depending on a database beside it.
    assert "house" in text and "maintenance" in text


async def test_a_plain_markdown_file_dropped_in_is_a_note(store):
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "shopping.md").write_text("- milk\n- bread\n")
    store.load()

    note = store.get("shopping")
    assert note is not None
    assert note.title == "shopping"
    assert "milk" in note.body


async def test_a_broken_frontmatter_does_not_lose_the_text(store):
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "half.md").write_text("---\ntitle: [unclosed\n---\n\nThe text survives.\n")
    store.load()
    assert "The text survives." in store.get("half").body


async def test_update_and_append_keep_the_file_current(store):
    store.create("Boiler serviced", "Pressure 1.2 bar.")
    appended = store.append("Boiler serviced", "Next service due March.")
    assert appended["appended"] is True

    text = (store.root / "boiler-serviced.md").read_text()
    assert "Pressure 1.2 bar." in text
    assert "Next service due March." in text

    updated = store.update("boiler-serviced", body="Replaced entirely.", tags=["house"])
    assert updated["updated"] is True
    assert store.get("boiler-serviced").body == "Replaced entirely."
    assert "Next service due March." not in (store.root / "boiler-serviced.md").read_text()


async def test_delete_removes_the_file_and_the_note(store):
    store.create("Temporary", "gone soon")
    assert (store.root / "temporary.md").is_file()

    result = store.delete("temporary")
    assert result["deleted"] is True
    assert not (store.root / "temporary.md").exists()
    assert store.get("temporary") is None
    assert store.search("gone soon") == []


async def test_creating_the_same_title_twice_is_refused_rather_than_silently_merging(store):
    store.create("Shopping", "milk")
    again = store.create("Shopping", "bread")
    assert again["created"] is False
    assert "already exists" in again["error"]
    assert store.get("shopping").body == "milk"

    # Unless the caller says so — research re-running the same question wants
    # the newer report, not an error that strands it.
    forced = store.create("Shopping", "bread", overwrite=True)
    assert forced["created"] is True
    assert store.get("shopping").body == "bread"


# --- finding them again -------------------------------------------------------


async def test_search_finds_a_note_by_its_body(store):
    store.create("Boiler serviced", "Pressure was 1.2 bar and the flue was clear.")
    store.create("Shopping", "milk, bread, coffee")

    hits = [row["id"] for row in store.search("flue")]
    assert hits == ["boiler-serviced"]
    assert "flue" in store.search("flue")[0]["excerpt"]


async def test_search_survives_punctuation_that_fts_would_choke_on(store):
    """FTS5 has a query syntax, and a user searching for `boiler (march)` must
    get their note rather than a syntax error."""
    store.create("Boiler serviced", "Serviced in March. Pressure fine.")
    assert [row["id"] for row in store.search("boiler (march)")] == ["boiler-serviced"]


async def test_search_with_a_tag_filter_narrows_rather_than_widens(store):
    store.create("Boiler serviced", "pressure fine", ["house"])
    store.create("Boiler quote", "pressure fine", ["money"])

    assert len(store.search("pressure")) == 2
    assert [row["id"] for row in store.search("pressure", tag="money")] == ["boiler-quote"]


async def test_the_tag_filter_lists_without_a_query(store):
    store.create("A", "one", ["house"])
    store.create("B", "two", ["money"])
    assert [row["id"] for row in store.listing(tag="house")] == ["a"]


async def test_the_index_is_derived_and_can_be_deleted(store):
    store.create("Boiler serviced", "Pressure was 1.2 bar.")
    store.close()
    store.index_path.unlink(missing_ok=True)

    store.load()
    assert [row["id"] for row in store.search("1.2 bar")] == ["boiler-serviced"]


# --- links --------------------------------------------------------------------


def test_a_wiki_link_is_read_off_the_body():
    assert links_in("see [[Heating]] and [[boiler serviced]]") == ["heating", "boiler-serviced"]
    assert links_in("[[Heating|the heating note]]") == ["heating"]
    assert links_in("no links here") == []


async def test_a_link_is_resolved_and_back_linked(store):
    store.create("Heating", "Flow at 55 °C.")
    store.create("Boiler serviced", "Pressure fine. See [[Heating]].")

    assert store.get("boiler-serviced").links == ["heating"]
    assert store.get("heating").backlinks == ["boiler-serviced"]


async def test_a_link_to_a_note_that_does_not_exist_yet_is_not_an_error(store):
    """Writing the link first is how people work; the note follows."""
    store.create("Boiler serviced", "See [[heating]].")
    assert store.get("boiler-serviced").links == ["heating"]

    store.create("Heating", "Flow at 55 °C.")
    assert store.get("heating").backlinks == ["boiler-serviced"]


def test_a_title_and_its_slug_are_the_same_note():
    assert slugify("Boiler serviced!") == "boiler-serviced"
    assert slugify("  ") == "note"


# --- the tools ----------------------------------------------------------------


async def test_the_tools_are_registered_and_do_what_they_say(tmp_path):
    jarvis = Jarvis(tmp_path / "config")
    registry = ToolRegistry(jarvis)
    jarvis.data["llm_tools"] = registry
    await notes_integration.async_setup(jarvis, {"path": str(tmp_path / "notes")})

    for name in ("note_create", "note_append", "note_search"):
        assert registry.get(name) is not None, name

    created = await registry.get("note_create").handler(
        {"title": "Boiler serviced", "body": "Pressure 1.2 bar.", "tags": ["house"]}, None
    )
    assert created["created"] is True

    found = await registry.get("note_search").handler({"query": "pressure"}, None)
    assert found["count"] == 1

    # One tool for "find it" and "open it": naming a note returns its body.
    # Every tool costs context in every turn (`test_prompt_budget.py`), and
    # from the model's side both are "I want that note".
    read = await registry.get("note_search").handler({"id": "Boiler serviced"}, None)
    assert "1.2 bar" in read["note"]["body"]

    await registry.get("note_append").handler(
        {"id": "boiler-serviced", "text": "Next service in March."}, None
    )
    read = await registry.get("note_search").handler({"id": "boiler-serviced"}, None)
    assert "March" in read["note"]["body"]

    jarvis.data["notes"].close()


async def test_a_note_that_is_too_big_is_refused_rather_than_written(store):
    store.max_bytes = 100
    result = store.create("Runaway", "x" * 500)
    assert result["created"] is False
    assert not (store.root / "runaway.md").exists()
