"""Ask and answer, and the house edited by voice — end to end (M66, M69).

Through the real server, against the fakes: the scripted model asks a
question, the next turn in the same conversation answers it and the model
carries on with the answer; the model asks to remove an entity, the next turn
says yes and the entity is gone from the house; and a question that has
lapsed is told so in words rather than "unknown, expired or already-used".

Its own harness, not the session's: the last case needs a question clock
short enough to watch run out, and the removal takes a demo entity away for
good — neither is something the rest of the suite should inherit.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

#: Short enough to watch a question lapse, long enough that answering one in
#: the next REST call is nowhere near it.
QUESTION_TTL = 6.0

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def house(tmp_path_factory):
    """A harness of this module's own. Placed where CI collects work
    directories, the way `spare_work_dir` does, so its logs are readable."""
    from testing.harness import Harness

    configured = os.environ.get("JARVIS_HARNESS_WORK_DIR")
    if configured:
        work_dir = Path(configured).parent / "extra" / "ask-and-answer"
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        work_dir = tmp_path_factory.mktemp("ask-and-answer")
    harness = Harness(work_dir=str(work_dir), question_ttl=QUESTION_TTL, keep=True)
    harness.start()
    try:
        yield harness
    finally:
        harness.stop()


@pytest.fixture
async def client(house):
    from testing.harness import JarvisClient

    house.check_alive()
    connection = JarvisClient(house.base_url, house.token)
    try:
        await connection.connect()
        yield connection
    finally:
        await connection.aclose()


def _calls(reply: dict) -> list[dict]:
    return reply["response"]["data"]["tool_calls"]


async def test_a_question_is_answered_by_the_next_turn(client, house):
    """The model asks; the person answers by saying so; the model carries on.

    The second rule is scoped to the LAST message on purpose: the fake matches
    on every user message by default, and the first turn's words are still in
    the history when the second is asked.
    """
    house.set_ollama_script(
        {
            "rules": [
                {
                    "match": "the corner one",
                    "scope": "last",
                    "responses": [{"say": "The corner lamp it is, Sir."}],
                },
                {
                    "match": "which lamp",
                    "responses": [
                        {
                            "tool_calls": [
                                {
                                    "name": "ask_user",
                                    "arguments": {
                                        "question": "Which lamp did you mean?",
                                        "choices": ["Desk lamp", "Corner lamp"],
                                    },
                                }
                            ]
                        },
                        {"say": "Which lamp did you mean, Sir — the desk or the corner?"},
                    ],
                },
            ]
        }
    )
    try:
        first = await client.conversation("turn on the lamp, which lamp is up to you")
        conversation = first["conversation_id"]
        assert first["response"]["speech"]["plain"]["speech"] == (
            "Which lamp did you mean, Sir — the desk or the corner?"
        )
        held = _calls(first)[0]["result"]
        assert held["status"] == "approval_required"
        # Its own clock, the one this harness was booted with.
        assert held["waits_seconds"] == QUESTION_TTL
        pending = (await client.call_service_rest("llm", "pending_requests", return_response=True))[
            "service_response"
        ]["pending"]
        mine = [p for p in pending if p["request_id"] == held["request_id"]]
        assert mine and mine[0]["conversation_id"] == conversation
        assert mine[0]["spoken"] is False, "a typed turn is not spoken"

        second = await client.conversation("the corner one", conversation)

        assert second["response"]["speech"]["plain"]["speech"] == "The corner lamp it is, Sir."
        answered = _calls(second)[0]
        assert answered["name"] == "ask_user"
        assert answered["arguments"]["answer"] == "Corner lamp"
        assert answered["result"]["answer"] == "Corner lamp"
        pending = (await client.call_service_rest("llm", "pending_requests", return_response=True))[
            "service_response"
        ]["pending"]
        assert not [p for p in pending if p["request_id"] == held["request_id"]]
        # The model was told the answer before the user's words, which stayed last.
        messages = house.ollama_requests()[-1]["payload"]["messages"]
        assert messages[-1]["role"] == "user" and messages[-1]["content"] == "the corner one"
        assert any(
            m["role"] == "system" and "answers the question you asked earlier" in m["content"]
            for m in messages
        )
    finally:
        house.reset_ollama()


async def test_a_confirmed_removal_takes_the_entity_out_of_the_house(client, house):
    """'Remove the decorative lights' → held, pinned → 'yes' → gone."""
    assert (await client.state("switch.decorative_lights"))["entity_id"] == "switch.decorative_lights"
    house.set_ollama_script(
        {
            "rules": [
                {
                    "match": "yes, go ahead",
                    "scope": "last",
                    "responses": [{"say": "The decorative lights are gone, Sir."}],
                },
                {
                    "match": "remove the decorative lights",
                    "responses": [
                        {
                            "tool_calls": [
                                {
                                    "name": "remove_entities",
                                    "arguments": {"entity_ids": ["switch.decorative_lights"]},
                                }
                            ]
                        },
                        {"say": "That needs your go-ahead, Sir."},
                    ],
                },
            ]
        }
    )
    try:
        first = await client.conversation("remove the decorative lights")
        conversation = first["conversation_id"]
        held = _calls(first)[0]["result"]
        assert held["status"] == "approval_required"
        assert held["arguments"] == {"entity_ids": ["switch.decorative_lights"]}
        assert (await client.state("switch.decorative_lights"))["state"] in ("on", "off"), "held, not run"

        second = await client.conversation("yes, go ahead", conversation)

        assert second["response"]["speech"]["plain"]["speech"] == "The decorative lights are gone, Sir."
        ran = _calls(second)[0]
        assert ran["name"] == "remove_entities"
        assert ran["result"]["removed"] == ["switch.decorative_lights"]
        ids = {state["entity_id"] for state in await client.states()}
        assert "switch.decorative_lights" not in ids
        entries = await client.get_json("/api/config/entity_registry/list")
        assert "switch.decorative_lights" not in {e["entity_id"] for e in entries}
    finally:
        house.reset_ollama()


async def test_all_of_the_elements_is_refused_before_anything_is_held(client, house):
    """The operator's sentence. The model's tool call says 'all'; the tool
    refuses with what to do instead, and nothing reaches a consent surface."""
    house.set_ollama_script(
        {
            "rules": [
                {
                    "match": "remove all of the elements",
                    "responses": [
                        {
                            "tool_calls": [
                                {"name": "remove_entities", "arguments": {"name": "everything"}}
                            ]
                        },
                        {"say": "I would need each one named, Sir."},
                    ],
                }
            ]
        }
    )
    try:
        reply = await client.conversation("can you remove all of the elements of the house?")
        refused = _calls(reply)[0]["result"]
        assert refused["status"] == "error"
        assert "list_entities" in refused["error"]
        pending = (await client.call_service_rest("llm", "pending_requests", return_response=True))[
            "service_response"
        ]["pending"]
        assert not [p for p in pending if p["tool"] == "remove_entities"]
    finally:
        house.reset_ollama()


async def test_an_expired_question_says_so_in_words(client, house):
    house.set_ollama_script(
        {
            "rules": [
                {
                    "match": "what is the printer",
                    "responses": [
                        {
                            "tool_calls": [
                                {
                                    "name": "ask_user",
                                    "arguments": {"question": "What is the printer's URL?"},
                                }
                            ]
                        },
                        {"say": "What is the printer's URL, Sir?"},
                    ],
                }
            ]
        }
    )
    try:
        first = await client.conversation("what is the printer's address? ask me")
        held = _calls(first)[0]["result"]
        assert held["status"] == "approval_required"
        await asyncio.sleep(QUESTION_TTL + 1.5)

        late = (
            await client.call_service_rest(
                "llm",
                "approve",
                {"request_id": held["request_id"], "approved": True, "answer": "http://printer.lan"},
                return_response=True,
            )
        )["service_response"]

        assert late["status"] == "error"
        assert late["expired"] is True
        assert late["error"] == "That question expired after 6 seconds; ask again and I'll wait."
        # And an id nobody ever raised still gets the honest guess.
        unknown = (
            await client.call_service_rest(
                "llm", "approve", {"request_id": "never-raised", "approved": True}, return_response=True
            )
        )["service_response"]
        assert unknown["error"] == "unknown, expired or already-used approval request"
    finally:
        house.reset_ollama()
