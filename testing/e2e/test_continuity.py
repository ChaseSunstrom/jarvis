"""One conversation, two surfaces.

Start something on the console and continue it from the phone: it has to be the
same thread, with the same turns, and "it" in the second sentence still has to
mean what it meant in the first. `docs/AUDIT.md` §15 says ids exist on the wire
and "nothing proves two clients converge on one thread, and no test does". This
is that test.

Two independent websocket clients against one real server — which is what two
devices are.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

SCRIPT = {
    "models": ["qwen3:8b"],
    "rules": [
        {
            "name": "the first request",
            "match": "turn on the bed light.",
            "scope": "last",
            "responses": [
                {
                    "tool_calls": [
                        {"name": "turn_on", "arguments": {"entity_id": "light.bed_light"}}
                    ]
                },
                {"say": "The bed light is on, Sir."},
            ],
        },
        {
            # `scope: user`, not `all`. The system prompt lists every exposed
            # entity — `light.bed_light` among them — so a rule matching "bed
            # light" across all messages fires on the house summary and proves
            # nothing about the conversation.
            "name": "and the follow-up, which only works with the first in front of it",
            "match": "turn on the bed light",
            "scope": "user",
            "repeat": True,
            "say": "You asked me to turn on the bed light, Sir.",
        },
    ],
    "default": {"say": "I have no idea what you are referring to, Sir."},
}


async def _second_client(harness):
    from testing.harness import JarvisClient

    other = JarvisClient(harness.base_url, harness.token)
    await other.connect()
    return other


async def test_a_thread_started_on_one_client_continues_on_another(harness, client):
    harness.reset_ollama()
    harness.set_ollama_script(SCRIPT)
    other = await _second_client(harness)
    try:
        first = await client.conversation("Turn on the bed light.")
        thread = first["conversation_id"]
        assert thread

        # The second device opens the same thread by id — which is all a phone
        # has — and asks something that cannot be answered without the first
        # turn in the prompt.
        answer = await other.conversation("What did I just ask you to do?", conversation_id=thread)
        speech = answer["response"]["speech"]["plain"]["speech"]
        assert "bed light" in speech.lower(), speech
        assert answer["conversation_id"] == thread
    finally:
        await other.aclose()
        harness.set_ollama_script(None)


async def test_both_clients_see_the_same_transcript(harness, client):
    harness.reset_ollama()
    harness.set_ollama_script(SCRIPT)
    other = await _second_client(harness)
    try:
        first = await client.conversation("Turn on the bed light.")
        thread = first["conversation_id"]
        await other.conversation("What did I just ask you to do?", conversation_id=thread)

        one = await client.command("jarvis/conversation/get", conversation_id=thread)
        two = await other.command("jarvis/conversation/get", conversation_id=thread)
        assert [t["content"] for t in one["conversation"]["turns"]] == [
            t["content"] for t in two["conversation"]["turns"]
        ]
        assert len(one["conversation"]["turns"]) >= 4, one["conversation"]["turns"]
    finally:
        await other.aclose()
        harness.set_ollama_script(None)


async def test_a_thread_nobody_named_is_not_shared_by_accident(harness, client):
    """The other half: two conversations with no id are two conversations. A
    server that quietly merged them would put one person's thread in front of
    another's model."""
    harness.reset_ollama()
    harness.set_ollama_script(SCRIPT)
    other = await _second_client(harness)
    try:
        first = await client.conversation("Turn on the bed light.")
        second = await other.conversation("What did I just ask you to do?")
        assert second["conversation_id"] != first["conversation_id"]
        speech = second["response"]["speech"]["plain"]["speech"]
        assert "no idea" in speech.lower(), speech
    finally:
        await other.aclose()
        harness.set_ollama_script(None)
