#!/usr/bin/env python3
"""Executable spec: the surface on screen is the one that gets asked.

Reported as: *"when Jarvis asks a question the UI for it sucks, and if I was
talking to it from the SIRI overlay that pops up when saying hey Jarvis, it
closes the overlay, and when I respond, that closes too, instead of keeping the
hey Jarvis overlay and continuing."*

All three symptoms were one cause, and the cause was **an empty seam**.

`CompanionSpeechHost` is the interface a visible Jarvis surface fills in so a
proactive message can be spoken and a question asked in place. It was written,
documented with a usage example, given a working implementation, and never
constructed by anything. `CompanionMessageHandler.speechHost` was null for the
entire life of the app.

Everything downstream then behaved exactly as designed:

  * a spoken message with no host posts a notification — correct fallback;
  * a question with no host starts `CompanionAskActivity` with
    `FLAG_ACTIVITY_NEW_TASK` — correct fallback.

So the fallback *was* the product. Starting a NEW_TASK activity over the
wake-word orb takes the orb down, and the activity finishes itself once
answered, which is why the conversation appeared to end twice; and the
"UI that sucks" was the fallback screen, which is the one surface nobody was
supposed to meet routinely.

Nothing was broken enough to log. That is what this file is for: a seam with no
caller is invisible to every other kind of test, because the code all works.

Run:  python3 android-app/tools/speech_host_test.py
      python3 -m pytest android-app/tools/speech_host_test.py -q
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MAIN_KOTLIN = Path(__file__).resolve().parents[1] / "app/src/main/kotlin"
JARVIS = MAIN_KOTLIN / "ai" / "jarvis" / "app"

HANDLER = JARVIS / "companion" / "CompanionMessageHandler.kt"
SEAM = JARVIS / "companion" / "CompanionSpeaker.kt"
HOST = JARVIS / "companion" / "ConversationAskHost.kt"
CONVERSATION = JARVIS / "assist" / "JarvisConversation.kt"
PIPELINE_CLIENT = JARVIS / "assist" / "AssistPipelineClient.kt"

#: Every surface that owns a conversation, and therefore has somewhere to put
#: a question. If a fourth appears, it belongs on this list.
SURFACES = {
    "MainActivity.kt": JARVIS / "MainActivity.kt",
    "JarvisAssistActivity.kt": JARVIS / "JarvisAssistActivity.kt",
    "WakeWordService.kt": JARVIS / "assist" / "WakeWordService.kt",
}


def code_only(source: str) -> str:
    return "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*", "*/"))
    )


# --- the seam has callers ---------------------------------------------------
def test_every_conversation_surface_registers_a_host():
    """The check that was missing. A surface that can hold a conversation can
    hold a question."""
    missing = []
    for name, path in SURFACES.items():
        source = code_only(path.read_text(encoding="utf-8"))
        if "CompanionMessageHandler.speechHost = it" not in source:
            missing.append(name)
    assert not missing, (
        "these surfaces own a JarvisConversation but never register a "
        "CompanionSpeechHost, so a question asked while they are on screen "
        "takes over the screen instead of being asked on it: "
        + ", ".join(missing)
    )


def test_every_surface_that_registers_also_clears():
    """A host outliving its surface is worse than no host: the handler would
    hand a question to a screen that is gone and never fall back."""
    missing = []
    for name, path in SURFACES.items():
        source = code_only(path.read_text(encoding="utf-8"))
        if "clearSpeechHost(" not in source:
            missing.append(name)
    assert not missing, f"these register a host and never clear it: {missing}"


def test_clearing_is_by_identity():
    """Two surfaces handing over must not leave the slot pointing at the one
    that went away first — the handover is the common case, not the rare one:
    the orb hands off to the home screen every time the app is opened."""
    handler = code_only(HANDLER.read_text(encoding="utf-8"))
    assert "speechHost === host" in handler, (
        "clearSpeechHost no longer compares by identity"
    )
    for path in SURFACES.values():
        text = code_only(path.read_text(encoding="utf-8"))
        assert "speechHost = null" not in text, (
            f"{path.name} clears the slot directly instead of via "
            "clearSpeechHost, which cannot tell whose host is in it"
        )


def test_the_handler_tries_the_host_before_the_activity():
    """Ordering is the fix. Starting the activity first and asking the host
    afterwards would be the old behaviour with extra steps."""
    source = re.sub(r"\s+", " ", HANDLER.read_text(encoding="utf-8"))
    body = source.split("private fun ask(app: Context", 1)
    assert len(body) == 2, "CompanionMessageHandler.ask is gone"
    body = body[1][:2500]
    host_at = body.find("host.ask(message.text)")
    activity_at = body.find("app.startActivity(intent)")
    assert host_at != -1, "ask() no longer offers the question to the on-screen host"
    assert activity_at != -1, "ask() lost its fallback to the activity"
    assert host_at < activity_at, "the fallback runs before the host is asked"


def test_a_question_with_options_still_goes_to_a_screen():
    """A list to choose from cannot be matched to a spoken answer without
    putting the model back in the loop, which is what this path exists to keep
    out. Buttons need a surface that can draw buttons."""
    source = re.sub(r"\s+", " ", HANDLER.read_text(encoding="utf-8"))
    body = source.split("private fun ask(app: Context", 1)[1][:2500]
    assert "message.options.isEmpty()" in body, (
        "a question with options is being handed to the voice host, which can "
        "only return free text"
    )


def test_a_host_that_throws_does_not_swallow_the_question():
    source = re.sub(r"\s+", " ", HANDLER.read_text(encoding="utf-8"))
    body = source.split("private fun ask(app: Context", 1)[1][:2500]
    assert "catch (t: Throwable)" in body, "ask() no longer guards the host call"


# --- the conversation survives the question ---------------------------------
def test_the_conversation_is_held_not_stopped():
    """`stop()` would fire onIdle, which finishes the popup and detaches the
    overlay — the exact teardown this is meant to prevent."""
    source = code_only(HOST.read_text(encoding="utf-8"))
    assert "holdForQuestion()" in source, "the host no longer holds the conversation"
    assert "resumeAfterQuestion()" in source, "the host no longer resumes it"
    assert ".stop()" not in source.split("override fun ask(", 1)[1][:1500], (
        "ask() stops the conversation instead of holding it"
    )


def test_holding_keeps_running_true():
    """`running` staying true is what stops the inactivity timer pulling the
    surface out from under a question nobody has answered yet."""
    source = CONVERSATION.read_text(encoding="utf-8")
    body = source.split("fun holdForQuestion(", 1)
    assert len(body) == 2, "JarvisConversation.holdForQuestion is gone"
    body = body[1][:1200]
    assert "running = false" not in body, (
        "holdForQuestion clears `running`, which lets onIdle fire mid-question"
    )
    assert "mic?.stop()" in body, (
        "holdForQuestion does not release the microphone; the asking surface is "
        "about to open its own, and two AudioRecords is a coin toss"
    )


def test_the_turn_loop_cannot_restart_under_a_held_conversation():
    """A TTS completion or run-end from the turn that was in flight when the
    question arrived would otherwise re-open the mic underneath it."""
    source = CONVERSATION.read_text(encoding="utf-8")
    body = source.split("private fun beginNextTurn()", 1)
    assert len(body) == 2, "beginNextTurn is gone"
    assert "if (!running || held) return" in body[1][:600], (
        "beginNextTurn no longer checks `held`"
    )


def test_the_answer_never_reaches_the_conversation_agent():
    """"No, delete them" is a reply to a question. Running it through the agent
    would execute it."""
    source = code_only(HOST.read_text(encoding="utf-8"))
    assert "CompanionVoiceClient(" in source, (
        "the host takes the answer by some route other than the stt-only client"
    )
    assert "AssistPipelineClient" not in source, (
        "the host reaches for the full stt->intent->tts client, which would "
        "dispatch the answer as a command"
    )


def test_the_host_settles_exactly_once():
    """The caller's ledger settles on the callback; a second one would answer a
    question that has already been answered."""
    source = code_only(HOST.read_text(encoding="utf-8"))
    assert "var settled = false" in source, "the once-only guard is gone"
    assert "if (settled) return" in source


def test_every_failure_still_hands_the_microphone_back():
    """Answered, dismissed, timed out or failed: the conversation is owed its
    microphone in all four cases."""
    source = code_only(HOST.read_text(encoding="utf-8"))
    finish = source.split("fun finish(answer: String?)", 1)
    assert len(finish) == 2, "the single-outcome path is gone"
    assert "resumeAfterQuestion()" in finish[1][:600], (
        "the one settle path does not resume the conversation"
    )
    stop = source.split("fun stop()", 1)
    assert len(stop) == 2 and "isHeldForQuestion" in stop[1][:400], (
        "tearing the host down while a question is in flight leaves the "
        "conversation muted forever"
    )


# --- on-device transcription vs the speaker gate ----------------------------
def test_the_local_path_suspends_while_the_gate_enforces():
    """Two settings that silently cancelled each other.

    Transcribing on this phone sends WORDS. The speaker check runs on the
    server, on SOUND. With `mode: enforce` and on-device transcription both on,
    every turn walked straight past the gate — and neither setting looks
    dangerous on its own.

    It cannot be fixed by verifying here instead: `createOnDeviceSpeechRecognizer`
    owns the microphone and hands this app partial text and an RMS level, never
    samples, so there is no audio on the device to embed. See DEVIATIONS §10.
    """
    source = code_only(CONVERSATION.read_text(encoding="utf-8"))
    body = source.split("private fun startLocalTurn()", 1)
    assert len(body) == 2, "startLocalTurn is gone"
    body = body[1][:900]
    assert "config.speakerGateEnforcing" in body, (
        "startLocalTurn no longer checks whether the server is enforcing, so "
        "on-device transcription can bypass the speaker gate again"
    )
    # And it must bail BEFORE doing anything else with the recogniser.
    assert body.index("config.speakerGateEnforcing") < body.index(
        "LocalTranscriber.isAvailable"
    ), "the enforcement check must come before the local path is chosen"


def test_the_client_admits_its_text_came_from_a_microphone():
    """The server's half. The phone check keeps Jarvis working; this is what
    keeps it safe when the phone is old, misconfigured, or wrong."""
    source = code_only(PIPELINE_CLIENT.read_text(encoding="utf-8"))
    body = source.split("start_stage", 1)[1]
    assert '"audio_derived", true' in body.replace("'", '"'), (
        "an INTENT-start run no longer declares that its text came from a "
        "microphone, so the server cannot tell it apart from somebody typing"
    )


def test_only_the_transcript_path_claims_to_be_audio_derived():
    """Typed input must not be flagged: a person at a keyboard is authenticated
    by the bearer token they typed it with, and flagging it would break the
    console's chat the moment the gate turns on."""
    source = code_only(PIPELINE_CLIENT.read_text(encoding="utf-8"))
    run = source.split("assist_pipeline/run", 1)[1][:1600]
    flag_at = run.find("audio_derived")
    intent_at = run.find("StartStage.INTENT")
    assert flag_at != -1 and intent_at != -1
    assert intent_at < flag_at, (
        "audio_derived is set outside the INTENT branch, so it would be "
        "attached to microphone runs and typed runs alike"
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
    print(
        f"\n{len(tests) - failures}/{len(tests)} checks passed "
        f"({len(SURFACES)} conversation surfaces)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
