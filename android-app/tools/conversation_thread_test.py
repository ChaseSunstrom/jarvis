#!/usr/bin/env python3
"""Executable spec: one conversation, not four — and one of them a documented lie.

`docs/cross-device.md` says, under "Conversation continuity":

> Messages carry a `conversation_id`. Answer on your phone and the reply lands
> back in the same conversation the desktop started — so "yes" means the right
> thing without re-establishing context. `companion.handoff` moves an in-flight
> conversation to another device deliberately.

On Android, none of that was true, in four separate ways.

## 1. The id arrived and was dropped on the floor

`CompanionMessage.parse` read `conversation_id` off the wire. The handler put it
in the ask activity's intent as `EXTRA_CONVERSATION_ID`. `CompanionAskActivity
.onCreate` read MESSAGE_ID, MODE, TEXT, IMPORTANCE, OPTIONS and TIMEOUT_MS —
six of the seven — and **nothing anywhere read the seventh**. Parsed, carried,
never used: the `CompanionSpeechHost` shape with an extra hop.

## 2. `AssistPipelineClient.conversationId` could not be seeded

It was a `private var` with no constructor parameter and no setter. It could
*learn* an id from an `intent-end` event and could never be *given* one, so
every client this app built started a fresh conversation and nothing outside the
class ever heard the id it learned.

## 3. Which broke continuity on ONE device, with no server involved

`JarvisConversation.speakToServer` builds a **second** `AssistPipelineClient` for
the on-device transcription path — which is the DEFAULT — so a phone doing its
own speech-to-text forgot the conversation on every turn. And `WakeWordService`
and `JarvisAssistActivity` each construct their own `JarvisConversation`, so
speaking to the wake orb and then opening the assist card lost the thread with
one user, one device and one server.

## 4. Three unconnected state machines

`DeviceLink` kept a private `conversationId` for `ask_jarvis`,
`AssistPipelineClient` kept another, and the companion field was the third. A
task asking Jarvis something and the user asking Jarvis something were two
different conversations on one phone.

## And `companion.handoff`

`grep -rn handoff` over `companion/` and `channel/` returned nothing. It turns
out there is no `handoff` frame to implement: the server's service (see
`jarvis-core/jarvis/integrations/companion/__init__.py`) is
`manager.send(kind="say", conversation_id=…)` aimed at a chosen device. The
handoff IS the conversation id on an ordinary message — so implementing it means
adopting the thread a message arrives on, which is one line and was the missing
one.

The answer frame deliberately does NOT carry a conversation id back. The server
matches by `message_id` and holds the mapping itself (`on_device_answer` fires
`EVENT_MESSAGE_ANSWERED` with the conversation id off its own pending message),
so a copy from the device would be a field nothing reads — which is the exact
shape of defect this repo keeps finding, and adding one to "fix" a dropped field
would be trading one for another.

Run:  python3 android-app/tools/conversation_thread_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
KOTLIN = ANDROID / "app/src/main/kotlin/ai/jarvis/app"
DOC = ANDROID.parent / "docs" / "cross-device.md"

REGISTRY = KOTLIN / "assist/ConversationRegistry.kt"
CLIENT = KOTLIN / "assist/AssistPipelineClient.kt"
CONVO = KOTLIN / "assist/JarvisConversation.kt"
LINK = KOTLIN / "channel/DeviceLink.kt"
HANDLER = KOTLIN / "companion/CompanionMessageHandler.kt"
ASK = KOTLIN / "companion/CompanionAskActivity.kt"
PROTOCOL = KOTLIN / "companion/CompanionMessage.kt"


def code(path: Path) -> str:
    """Kotlin with comments stripped. A comment is not a caller."""
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


# ---------------------------------------------------------------------------
# 1. there is exactly one store
# ---------------------------------------------------------------------------


def test_there_is_one_registry_and_it_is_persisted() -> None:
    """In memory would be lost by exactly the transitions this is for.

    The surfaces are a Service, an Activity started from a notification, and an
    Activity the system may kill between two sentences.
    """
    src = code(REGISTRY)
    assert "getSharedPreferences(" in src, (
        "the conversation registry is not persisted, so a question answered "
        "from a notification after the process was killed starts a new thread"
    )
    for fn in ("fun current(", "fun remember(", "fun touch(", "fun clear("):
        assert fn in src, f"ConversationRegistry has no {fn.split('(')[0]}"


def test_the_thread_expires() -> None:
    """A conversation id from this morning is not the conversation you are in.

    Continuing a day-old thread hands the model context the user has forgotten
    providing, which is how "yes" comes to mean something nobody meant.
    """
    src = code(REGISTRY)
    timeout = re.search(r"const val IDLE_TIMEOUT_MS = ([^\n]+)", src)
    assert timeout, "the registry never expires a thread"
    assert "System.currentTimeMillis()" in src, (
        "the registry ages the thread against a monotonic clock, which restarts "
        "at zero after a reboot and would make an ancient id look fresh"
    )
    current = re.search(r"fun current\(.*?\n    \}", src, re.S)
    assert current and "clear(context)" in current.group(0), (
        "a stale thread is reported rather than forgotten, so the next read "
        "finds it stale all over again"
    )


def test_no_component_keeps_a_conversation_id_the_registry_never_hears_about() -> None:
    """The three state machines, gone.

    The rule is not "nobody may hold the id" — several components legitimately
    hold the one they were handed. `AssistPipelineClient` holds it because the
    wire frame needs it; `CompanionAskActivity` holds the one off its intent
    because answering has to refresh that thread and not some other. The rule is
    that a component holding one must **hand it to the registry**, so there is
    one answer to "which conversation is this phone in" rather than three.

    `DeviceLink.conversationId` was the counter-example: private, never shared,
    and therefore a second opinion.
    """
    strays = []
    for path in sorted(KOTLIN.rglob("*.kt")):
        if path == REGISTRY:
            continue
        src = code(path)
        if not re.search(r"(?:private\s+)?var\s+conversationId\b", src):
            continue
        # Either it publishes to the registry, or it takes the id from a
        # constructor whose caller does — which is `AssistPipelineClient`, whose
        # `onConversationId` callback is exactly that hand-off.
        if "ConversationRegistry.remember(" in src or "onConversationId" in src:
            continue
        strays.append(str(path.relative_to(KOTLIN)))
    assert not strays, (
        "these keep a conversation id the shared registry never hears about, so "
        "the phone is in two conversations at once: " + ", ".join(sorted(set(strays)))
    )


# ---------------------------------------------------------------------------
# 2. the client can be seeded
# ---------------------------------------------------------------------------


def test_the_pipeline_client_takes_a_conversation_and_reports_one() -> None:
    src = code(CLIENT)
    header = src.split(") : WebSocketListener()", 1)[0]
    assert "conversationId: String? = null" in header, (
        "AssistPipelineClient still cannot be given a conversation, so every "
        "client it builds starts a fresh one"
    )
    assert "onConversationId" in header, (
        "nothing outside the client ever hears the id the server issued, so it "
        "dies with the socket"
    )
    intent_end = re.search(r'"intent-end" -> \{.*?\n            \}', src, re.S)
    assert intent_end, "the intent-end handler is gone"
    assert "onConversationId(" in intent_end.group(0), (
        "the server issued a conversation id and the caller was not told"
    )
    assert "run.put(\"conversation_id\"" in src, (
        "the run frame no longer carries the conversation, so the server "
        "starts a new one however well the phone remembers"
    )


def test_every_pipeline_client_in_a_conversation_is_seeded() -> None:
    """Both of them. The second was the bug.

    `JarvisConversation` builds one client for the streaming path and another in
    `speakToServer` for the on-device transcription path. Seeding only the first
    fixes the case that was already nearly working and leaves the DEFAULT one
    broken.
    """
    src = code(CONVO)
    built = re.findall(r"AssistPipelineClient\((.*?)\n        \)", src, re.S)
    assert len(built) >= 2, (
        f"expected both pipeline clients in JarvisConversation, found {len(built)}"
    )
    for i, args in enumerate(built):
        assert "ConversationRegistry.current(context)" in args, (
            f"the pipeline client at position {i} starts from nothing; if it is "
            "speakToServer's, every on-device-transcribed turn forgets the one "
            "before it"
        )
        assert "ConversationRegistry.remember(context" in args, (
            f"the pipeline client at position {i} never stores the id it is given"
        )


# ---------------------------------------------------------------------------
# 3. the companion path
# ---------------------------------------------------------------------------


def test_the_handler_adopts_the_thread_a_message_arrives_on() -> None:
    """This is `companion.handoff`, on this end. All of it."""
    src = code(HANDLER)
    present = re.search(r"private fun present\(.*?\n    \}", src, re.S)
    assert present, "CompanionMessageHandler.present is gone"
    assert "ConversationRegistry.remember(app, message.conversationId)" in present.group(0), (
        "an inbound jarvis_message no longer adopts its conversation, so "
        "`companion.handoff` moves a thread to this device and this device "
        "ignores it — which is the documented feature that did not exist"
    )
    # Before the mode branch, so a `say` (which is what handoff sends) adopts it
    # too rather than only a question.
    body = present.group(0)
    assert body.index("ConversationRegistry.remember") < body.index("when (message.mode)"), (
        "the thread is adopted inside one mode's branch, so a handoff — which "
        "arrives as `kind: say` — is not one of them"
    )


def test_the_ask_screen_reads_the_extra_that_was_written_for_it() -> None:
    src = code(ASK)
    assert "EXTRA_CONVERSATION_ID" in src, (
        "CompanionAskActivity still ignores EXTRA_CONVERSATION_ID, which the "
        "handler has been putting on its intent all along"
    )
    assert "ConversationRegistry.remember(" in src, (
        "the conversation is read and then not stored, which is the same bug "
        "one step further along"
    )
    answer = re.search(r"private fun answer\(.*?\n    \}", src, re.S)
    assert answer and "ConversationRegistry.remember(" in answer.group(0), (
        "answering does not refresh the thread, so a question read an hour "
        "after it arrived is answered into a conversation that has expired"
    )
    # The extra is still written, or reading it proves nothing.
    assert "EXTRA_CONVERSATION_ID" in code(HANDLER)


def test_the_answer_frame_does_not_invent_a_field_nobody_reads() -> None:
    """The tempting wrong fix.

    Echoing `conversation_id` back on `jarvis_message_result` looks symmetrical
    and would be a write with no reader: the server matches the answer by
    `message_id` and already holds the mapping. Trading a dropped field for an
    ignored one is not a fix.
    """
    src = code(PROTOCOL)
    result = re.search(r"fun result\(.*?\n    \}", src, re.S)
    assert result, "CompanionProtocol.result is gone"
    assert "conversation_id" not in result.group(0), (
        "the result frame carries a conversation_id the server never reads"
    )


def test_the_automation_link_shares_the_thread() -> None:
    src = code(LINK)
    assert "ConversationRegistry.current(context)" in src, (
        "ask_jarvis starts its own conversation again, so a task asking Jarvis "
        "something and the user asking Jarvis something are two threads"
    )
    assert "ConversationRegistry.remember(context" in src


# ---------------------------------------------------------------------------
# 4. the document
# ---------------------------------------------------------------------------


def test_the_document_says_what_the_code_does() -> None:
    """`docs/cross-device.md` is the file that made the claim.

    It now has to name the mechanism, because "conversation_id" as a bare noun
    is what allowed four separate places to each assume somebody else was
    handling it.
    """
    text = DOC.read_text(encoding="utf-8")
    assert "companion.handoff" in text, "the handoff section is gone"
    # The correction: handoff is not a wire kind, and saying it is sends the
    # next reader looking for a frame that does not exist.
    assert "kind" in text and "not a separate wire" in text, (
        "docs/cross-device.md still implies `handoff` is its own message kind. "
        "It is `manager.send(kind='say', conversation_id=…)` — an ordinary "
        "message carrying a thread — and the phone implements it by adopting "
        "that thread."
    )


def main() -> int:
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # a broken check is a failure, not an abort
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed "
          f"({len(list(KOTLIN.rglob('*.kt')))} Kotlin files scanned)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
