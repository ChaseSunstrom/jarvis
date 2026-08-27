"""The response-routing table, as a pure function.

This is the single normative definition of the routing policy. Three places
mirror it and are kept honest by test_routing.py:

  * jarvis-core/jarvis/llm/tools.py — the `guidance` string `get_user_context`
    hands the model
  * jarvis-core/config/prompts/jarvis.txt — rule 4
  * evals/persona_prompts.yaml routing cases

The Home Assistant script that used to be the third mirror
(`ha-config/packages/jarvis/jarvis_context.yaml`) went with the rest of the
HA generation; see docs/removed.md.

Channels, most → least intrusive:
  speak            — TTS on the active audio device, nothing persisted
  announce_notify  — announce aloud at home AND leave a notification trail
  text             — normal mobile notification
  notify_silent    — low-priority silent notification (the safe default)
"""

from __future__ import annotations

from dataclasses import dataclass

CHANNELS = ("speak", "announce_notify", "text", "notify_silent")


#: Spoken phrasings that mean "write this down as a note", and what they are
#: NOT. The routing table lives here because the two decisions are the same
#: shape — an utterance and a policy — and because this file is already the one
#: three mirrors are checked against.
#:
#: The distinction that matters is the third column: "note that the boiler was
#: serviced" is a document; "remember that I take my coffee black" is a fact
#: about the user and belongs in `memory`, which is one line and goes into
#: every prompt. Getting that wrong is how a four-page report ends up in front
#: of "turn the lights off".
NOTE_INTENTS = (
    ("note that the boiler was serviced today", "note", "a thing that happened"),
    ("make a note of the gate code for the bins", "note", "a document to find later"),
    ("take a note: call the plumber on Tuesday", "note", "a document to find later"),
    ("write that down for me", "note", "an explicit request to write"),
    ("remember that I take my coffee black", "memory", "a standing fact about them"),
    ("remember to put the bins out", "task", "a reminder, not a document"),
    ("what did I note about the boiler", "note_search", "reading, not writing"),
)


def note_intent(said: str) -> str:
    """Which of `note`, `memory`, `task`, `note_search` a spoken line asks for.

    Deliberately small and lexical. It is not the router — the model decides,
    with the tools it has — it is the *definition* the router is checked
    against, and `jarvis-core/tests/test_notes_voice.py` drives the real path
    against these same lines.
    """
    text = " ".join(str(said or "").lower().split())
    if any(phrase in text for phrase in ("what did i note", "find my note", "read my note")):
        return "note_search"
    if text.startswith("remember to ") or " remember to " in text:
        return "task"
    if text.startswith("remember that ") or " remember that " in text:
        return "memory"
    if any(
        phrase in text
        for phrase in ("note that", "make a note", "take a note", "write that down", "add a note")
    ):
        return "note"
    return "reply"


@dataclass(frozen=True)
class Ctx:
    driving: bool = False
    location: str = "home"          # home | away
    awake: bool = True
    conversing: bool = False        # is a voice conversation active right now
    channel_requested: str = "voice"  # voice | text | none (unsolicited)
    kind: str = "reply"             # reply | status | task_done


def decide(ctx: Ctx) -> str:
    # 1. Driving: speak, keep it short, never notify (eyes stay on the road).
    if ctx.driving:
        return "speak"
    # 2. Away + status/finished-task: text, never announce to an empty house.
    if ctx.location == "away" and ctx.kind in ("status", "task_done"):
        return "text"
    # 3. Asked by text → answer by text.
    if ctx.channel_requested == "text":
        return "text"
    # 4. Long task finished outside an active conversation:
    #    announce aloud only if home and awake, always leave a trail.
    if ctx.kind == "task_done" and not ctx.conversing:
        if ctx.location == "home" and ctx.awake:
            return "announce_notify"
        return "notify_silent"
    # 5. Active voice conversation → speak.
    if ctx.conversing and ctx.channel_requested == "voice":
        return "speak"
    # 6. Unsure → least intrusive.
    return "notify_silent"
