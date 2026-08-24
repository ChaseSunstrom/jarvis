"""A conversation is still there tomorrow.

`tests/test_history.py` proves the archive in process. This proves the claim a
person makes: you said something to Jarvis, the machine was restarted, and the
thread you click is the thread you had — with the earlier turns in front of the
model, not just on the screen.

Real server, real archive on disk, real SIGTERM in the middle.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.e2e

SCRIPT = {
    "models": ["qwen3:8b"],
    "rules": [
        {
            "name": "remember the colour",
            "match": "favourite colour is amber",
            "scope": "user",
            "repeat": True,
            "say": "Noted, Sir — amber it is.",
        },
        {
            "name": "recall it",
            "match": "what did I say my favourite colour was",
            "scope": "all",
            "repeat": True,
            # The fake answers from what it was SENT: if the archived turn is
            # not in the messages, this rule cannot fire, which is exactly the
            # property under test.
            "match_type": "substring",
            "say": "You said amber, Sir.",
        },
    ],
    "default": {"say": "Very good, Sir."},
}


async def test_a_thread_survives_a_restart_and_resumes_with_its_context(harness, client):
    harness.reset_ollama()
    harness.set_ollama_script(SCRIPT)
    try:
        first = await client.conversation("My favourite colour is amber.")
        thread = first["conversation_id"]
        assert thread

        # It is on the list, with a title taken from what was said.
        listing = await client.command("jarvis/conversation/list")
        assert any(row["id"] == thread for row in listing["conversations"])

        harness.restart_core()
        await client.aclose()
        from testing.harness import JarvisClient

        fresh = JarvisClient(harness.base_url, harness.token)
        await fresh.connect()
        try:
            after = await fresh.command("jarvis/conversation/list")
            row = next(r for r in after["conversations"] if r["id"] == thread)
            assert row["turns"] >= 2, "the exchange did not survive"

            # And the model is given the earlier turns when the thread resumes:
            # the scripted rule matches on the whole message list, so it can
            # only fire if the archived turn was sent.
            answer = await fresh.conversation(
                "What did I say my favourite colour was?", conversation_id=thread
            )
            speech = answer["response"]["speech"]["plain"]["speech"]
            assert "amber" in speech.lower(), speech

            sent = json.dumps(harness.ollama_requests()[-1]["payload"])
            assert "amber" in sent, "the archived turn was not in the prompt"
        finally:
            await fresh.aclose()
    finally:
        harness.set_ollama_script(None)


async def test_threads_are_searchable_by_what_was_said(harness, client):
    harness.reset_ollama()
    harness.set_ollama_script(SCRIPT)
    try:
        answer = await client.conversation("My favourite colour is amber.")
        thread = answer["conversation_id"]

        found = await client.command("jarvis/conversation/search", query="amber")
        assert found["results"], "nothing matched a phrase that was definitely said"
        hit = next(row for row in found["results"] if row["id"] == thread)
        # The line that matched, not just the id: a person searching for a
        # phrase wants the sentence, and the thread is what they click.
        assert any("amber" in match["excerpt"].lower() for match in hit["matches"])

        empty = await client.command("jarvis/conversation/search", query="rhubarb")
        assert empty["results"] == []
    finally:
        harness.set_ollama_script(None)


async def test_the_archive_keeps_which_tools_a_turn_ran(harness, client):
    """Enough to redraw the conversation, and no more: the tool RESULT is
    reduced to its status, because a result can hold a page of scraped text and
    a transcript is the wrong place for it to accumulate."""
    harness.reset_ollama()
    harness.set_ollama_script(
        {
            "models": ["qwen3:8b"],
            "rules": [
                {
                    "name": "turn it on",
                    "match": "turn on the bed light",
                    "scope": "user",
                    "responses": [
                        {
                            "tool_calls": [
                                {"name": "turn_on", "arguments": {"entity_id": "light.bed_light"}}
                            ]
                        },
                        {"say": "The bed light is on, Sir."},
                    ],
                }
            ],
            "default": {"say": "Very good, Sir."},
        }
    )
    try:
        answer = await client.conversation("Turn on the bed light.")
        thread = answer["conversation_id"]
        detail = await client.command("jarvis/conversation/get", conversation_id=thread)
        turns = detail["conversation"]["turns"]

        called = [
            call
            for turn in turns
            for call in (turn.get("tool_calls") or [])
        ]
        assert [call["name"] for call in called] == ["turn_on"]
        assert called[0]["arguments"]["entity_id"] == "light.bed_light"
        assert called[0]["ok"] is True
        assert "result" not in called[0], "a whole tool result does not belong in a transcript"
    finally:
        harness.set_ollama_script(None)
