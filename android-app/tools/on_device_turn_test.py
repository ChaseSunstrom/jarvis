#!/usr/bin/env python3
"""Executable spec for a turn that happens entirely on the phone.

Two reports, one subject: *"the STT doesn't work that well for some reason? and
it isn't doing it on my phone (even though I have the models downloaded)"* and
*"I was talking to it and it wasn't really able to hear me"*.

Neither turned out to be about recognition accuracy.

**The models are the wake word's.** `ModelStore` downloads three ONNX files for
openWakeWord. Transcription uses `SpeechRecognizer.createOnDeviceSpeechRecognizer`,
which is part of Android and is not something this app can download — a phone
can have every model and still stream its audio to the server, because those two
sentences are about different things. The settings screen has to say so, under
its own heading, or the switch above it reads as the switch below it.

**And the orb did not move.** The local path has no `MicStreamer` — the platform
recogniser owns the microphone — so nothing was feeding `onAmplitude`, and the
orb sat perfectly still while somebody talked at it. That is indistinguishable
from a surface that has stopped listening, which is a surface people repeat
themselves at, over the top of the recogniser that was working fine.

Then the hinge both paths turn on: **one microphone, one owner**. `MicStreamer.stop`
runs on the main thread at the end of every turn and the wake listener asks for
the mic back the instant it returns, so it has to be synchronous — and it has to
not cost a frame budget to be so.

Run:  python3 android-app/tools/on_device_turn_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
STT = ANDROID / "app/src/main/kotlin/ai/jarvis/app/assist/LocalTranscriber.kt"
CONVO = ANDROID / "app/src/main/kotlin/ai/jarvis/app/assist/JarvisConversation.kt"
MIC = ANDROID / "app/src/main/kotlin/ai/jarvis/app/assist/MicStreamer.kt"
SETTINGS = ANDROID / "app/src/main/kotlin/ai/jarvis/app/SettingsActivity.kt"


# =========================================================================
# 1. The level the orb is fed
# =========================================================================

#: Mirrors LocalTranscriber's companion.
RMS_DB_FLOOR = -2.0
RMS_DB_CEILING = 10.0
RMS_SCALE = 0.25

#: Both orbs multiply whatever they are handed by this before drawing.
ORB_GAIN = 4.0


def level_of(rms_db: float) -> float:
    """`onRmsChanged`'s decibels on MicStreamer's linear scale."""
    t = (rms_db - RMS_DB_FLOOR) / (RMS_DB_CEILING - RMS_DB_FLOOR)
    return min(max(t, 0.0), 1.0) * RMS_SCALE


def check_the_level_lands_where_the_orb_can_use_it() -> list[str]:
    """The mapping has to end up in the range the orb's gain expects.

    The orb multiplies by four, because a smoothed RMS of ordinary speech lives
    in the bottom tenth of 0..1. Hand it a full-scale number instead and every
    syllable pins the orb at maximum — which is exactly as informative as the
    frozen orb this replaces, and much harder to notice is wrong.
    """
    failures = []
    cases = [
        # rms dB, what it is, what the ORB ends up drawing (level * gain)
        (-10.0, "below anything the platform reports", 0.0, 0.0),
        (-2.0, "silence", 0.0, 0.0),
        (1.0, "a quiet room", None, None),
        (4.0, "conversational speech", None, None),
        (10.0, "shouting", RMS_SCALE, 1.0),
        (30.0, "out of range", RMS_SCALE, 1.0),
    ]
    for db, what, want_level, want_drawn in cases:
        got = level_of(db)
        drawn = got * ORB_GAIN
        if want_level is not None and abs(got - want_level) > 1e-9:
            failures.append(f"{what} ({db} dB) maps to {got}, expected {want_level}")
        if want_drawn is not None and abs(drawn - want_drawn) > 1e-9:
            failures.append(f"{what} ({db} dB) draws {drawn}, expected {want_drawn}")
        if not (0.0 <= drawn <= 1.0):
            failures.append(
                f"{what} ({db} dB) drives the orb to {drawn}, outside 0..1 — the orb "
                "clamps, so everything above the ceiling looks identical"
            )

    # Monotonic, and it must actually MOVE across the range people speak in.
    # A mapping that is technically in range but spends conversation between
    # 0.98 and 1.0 is the frozen orb again.
    quiet = level_of(1.0) * ORB_GAIN
    loud = level_of(7.0) * ORB_GAIN
    if not (loud - quiet) > 0.25:
        failures.append(
            f"a quiet voice draws {quiet:.3f} and a loud one {loud:.3f}; that is not "
            "enough travel for anybody to see the orb reacting to them"
        )

    kotlin = STT.read_text(encoding="utf-8")
    for name, value in (
        ("RMS_DB_FLOOR", RMS_DB_FLOOR),
        ("RMS_DB_CEILING", RMS_DB_CEILING),
        ("RMS_SCALE", RMS_SCALE),
    ):
        found = re.search(rf"const val {name} = (-?[0-9.]+)f", kotlin)
        if not found:
            failures.append(f"LocalTranscriber.{name} is gone")
        elif abs(float(found.group(1)) - value) > 1e-9:
            failures.append(
                f"LocalTranscriber.{name} is {found.group(1)}, this spec says {value}"
            )
    return failures


# =========================================================================
# 2. The orb has to be driven at all
# =========================================================================


def check_the_orb_moves_on_the_local_path() -> list[str]:
    failures = []
    stt = STT.read_text(encoding="utf-8")
    convo = CONVO.read_text(encoding="utf-8")

    if "interface Listener" not in stt:
        failures.append(
            "LocalTranscriber reports no progress, so the surface driving it has "
            "nothing to animate while somebody is talking"
        )
    for hook in ("fun onLevel(level: Float)", "fun onPartial(text: String)", "fun onSpeechEnd()"):
        if hook not in stt:
            failures.append(f"LocalTranscriber.Listener no longer has {hook}")
    # The platform hands these to the RecognitionListener; dropping any of them
    # on the floor is how this was silent in the first place.
    for override, why in (
        ("override fun onRmsChanged", "the level meter"),
        ("override fun onPartialResults", "words as they are recognised"),
        ("override fun onEndOfSpeech", "the end of the utterance"),
    ):
        if f"{override}(rmsdB: Float) {{" not in stt and f"{override}(" not in stt:
            failures.append(f"LocalTranscriber ignores {why} again")

    local = re.search(r"private fun startLocalTurn\(\): Boolean \{.*?\n    \}", convo, re.S)
    if not local:
        return failures + ["JarvisConversation has no local turn"]
    body = local.group(0)
    if "ui.onAmplitude(level)" not in body:
        failures.append(
            "the local turn does not drive the orb's amplitude. With no MicStreamer "
            "in this path nothing else does, so the orb is frozen for the whole turn."
        )
    if "ui.onTranscript(text)" not in body:
        failures.append("the local turn shows no words while it recognises them")
    if "ui.onAmplitude(0f)" not in body:
        failures.append(
            "the orb is left at its last level when the turn ends, so it freezes "
            "mid-swell instead of settling"
        )
    if "JarvisOrbView.Mode.THINKING" not in body:
        failures.append(
            "the orb stays on LISTENING after the recogniser has stopped listening"
        )
    return failures


# =========================================================================
# 3. What it says when it fails
# =========================================================================


def check_the_failures_are_told_apart() -> list[str]:
    """Four different faults, four different sentences, one of them load-bearing.

    ERROR_NO_MATCH means the recogniser heard audio and found no words in it.
    ERROR_SPEECH_TIMEOUT means it heard nothing. Telling somebody who was
    talking that they were silent is how they conclude the microphone is broken
    and go looking for a permission that was already granted — which is exactly
    the afternoon this project has already spent once.
    """
    failures = []
    src = STT.read_text(encoding="utf-8")
    describe = re.search(r"private fun describe\(error: Int\): String = when \(error\) \{.*?\n    \}", src, re.S)
    if not describe:
        return ["LocalTranscriber no longer explains its errors"]
    body = describe.group(0)

    for code in (
        "ERROR_AUDIO",
        "ERROR_NO_MATCH",
        "ERROR_SPEECH_TIMEOUT",
        "ERROR_INSUFFICIENT_PERMISSIONS",
        "ERROR_RECOGNIZER_BUSY",
    ):
        if code not in body:
            failures.append(f"LocalTranscriber no longer handles {code}")

    # The two that a user most easily confuses must not share a sentence.
    def sentence(code: str) -> str:
        """Just this arm's strings.

        Bounded at the NEXT arm, not at a character count: these are multi-line
        `"..." + "..."` concatenations, and a fixed window runs into the arm
        below. The first draft of this check did exactly that and reported
        ERROR_NO_MATCH as saying "I did not hear anything" — which was
        ERROR_SPEECH_TIMEOUT's sentence, one line further down.
        """
        at = body.find(f"SpeechRecognizer.{code} ->")
        if at < 0:
            return ""
        rest = body[at + len(code) + 20 :]
        nxt = rest.find("SpeechRecognizer.ERROR_")
        chunk = rest if nxt < 0 else rest[:nxt]
        # Comments out first. They quote the wrong answers on purpose — the arm
        # below this one is annotated `// NOT "nothing was said"` — and reading
        # those as the message makes this check fail on the very sentence it
        # exists to require.
        chunk = "\n".join(line.split("//", 1)[0] for line in chunk.splitlines())
        return "".join(re.findall(r'"(.*?)"', chunk, re.S))

    no_match = sentence("ERROR_NO_MATCH")
    timeout = sentence("ERROR_SPEECH_TIMEOUT")
    if no_match and timeout and no_match == timeout:
        failures.append("'could not make out the words' and 'heard nothing' share a sentence")
    if no_match and ("not hear" in no_match or "nothing was said" in no_match):
        failures.append(
            "ERROR_NO_MATCH claims nothing was said. The recogniser heard audio and "
            "failed to find words in it; those have different fixes."
        )
    # The dead-mic case has to name the toggle that actually causes it on the
    # platform this app targets, exactly as the streaming path's does.
    audio = sentence("ERROR_AUDIO")
    if audio and "Sensors" not in audio:
        failures.append("the dead-microphone message no longer names the Sensors toggle")
    # A recogniser reporting a NETWORK failure is one that tried to use the
    # network, and this path exists so that it does not.
    if "ERROR_NETWORK" not in body:
        failures.append(
            "a network failure from the ON-DEVICE recogniser is reported as a number. "
            "It is the one error that contradicts the feature's whole promise."
        )

    # And the promise itself: a failed local turn must never quietly become a
    # streamed one. That would send the audio after saying it would not.
    convo = CONVO.read_text(encoding="utf-8")
    local = re.search(r"private fun startLocalTurn\(\): Boolean \{.*?\n    \}", convo, re.S)
    if local and "speakToServer" in local.group(0).split("if (text == null)", 1)[-1].split("return@listen", 1)[0]:
        failures.append(
            "a failed on-device transcription falls back to streaming the audio, which "
            "sends it after promising it would not"
        )
    return failures


# =========================================================================
# 4. The settings screen has to distinguish the two "on-device" things
# =========================================================================


def check_the_settings_do_not_conflate_them() -> list[str]:
    failures = []
    src = SETTINGS.read_text(encoding="utf-8")

    if 'JarvisUi.label(ctx, "Speech to text")' not in src:
        failures.append(
            "the transcription switch has no heading of its own, so it reads as part "
            "of the wake-word model section above it — which is the confusion behind "
            "'I have the models downloaded, why is it not transcribing on my phone'"
        )
    # A status line that only refreshes on save contradicts the switch above it
    # for as long as the user is looking at both.
    for switch, refresh in (("sttOnDevice", "refreshSttStatus"), ("wakeOnDevice", "refreshModelStatus")):
        listener = re.search(
            rf"{switch}\.setOnCheckedChangeListener \{{ _, _ ->.*?\n        \}}", src, re.S
        )
        if not listener:
            failures.append(f"{switch} does not refresh its status line when it is toggled")
        elif refresh not in listener.group(0):
            failures.append(f"{switch}'s listener does not call {refresh}")

    # The honest-status text is the point of the whole section: the setting is a
    # preference and availability is a fact, and when they disagree the audio is
    # still being streamed.
    status = re.search(r"private fun refreshSttStatus\(\) \{.*?\n    \}", src, re.S)
    if not status:
        failures.append("the transcription status line is gone")
    elif "LocalTranscriber.isAvailable" not in status.group(0):
        failures.append(
            "the transcription status reports the SETTING rather than what is "
            "happening. A privacy switch that looks on while the thing it turns off "
            "is still running is worse than no switch."
        )
    return failures


# =========================================================================
# 5. Giving the microphone back
# =========================================================================


def check_the_microphone_comes_back_promptly() -> list[str]:
    """`stop()` is synchronous, and must not cost a frame budget to be.

    Synchronous because the wake listener re-opens the microphone the instant
    this returns, and two `AudioRecord`s on one device is the coin toss the
    whole area exists to avoid.

    But `AudioRecord.read` blocks for a buffer period and is NOT interruptible:
    a worker parked inside one ignores both the running flag and an interrupt,
    so a naive `join` waits it out — on the main thread, at the end of every
    single turn. Stopping the DEVICE first makes that read return at once.

    And `release()` strictly after the join: releasing a recorder another thread
    is reading from is a native crash, not an exception.
    """
    failures = []
    src = MIC.read_text(encoding="utf-8")
    stop = re.search(r"fun stop\(\) \{.*?\n    \}", src, re.S)
    if not stop:
        return ["MicStreamer has no stop()"]
    body = stop.group(0)

    device_stop = body.find("device.stop()")
    join = body.find(".join(")
    release = body.find("device?.release()")
    if device_stop < 0:
        failures.append("stop() no longer stops the recorder")
    if join < 0:
        failures.append(
            "stop() no longer waits for the capture thread, so it can return while a "
            "worker is still reading and the wake listener opens a second recorder"
        )
    if release < 0:
        failures.append("stop() no longer releases the recorder")
    if device_stop >= 0 and join >= 0 and device_stop > join:
        failures.append(
            "stop() joins the capture thread before stopping the device. AudioRecord.read "
            "is not interruptible, so that waits out a whole buffer period on the main "
            "thread, every turn."
        )
    if join >= 0 and release >= 0 and release < join:
        failures.append(
            "stop() releases the recorder before the capture thread has finished with "
            "it, which is a native crash rather than an exception"
        )
    if "it.interrupt()" not in body:
        failures.append(
            "stop() does not interrupt the capture thread, so the injected test source — "
            "which parks in Thread.sleep rather than in a read — waits out its sleep"
        )

    # A failing read returns immediately rather than after a buffer's worth of
    # time, so an unguarded `continue` spins a core for the length of the turn.
    if "MAX_READ_ERRORS" not in src:
        failures.append(
            "a recorder that errors on every read spins the capture thread at full "
            "speed with nothing to show for it"
        )
    return failures


def main() -> int:
    for path in (STT, CONVO, MIC, SETTINGS):
        if not path.is_file():
            print(f"FAIL  {path} is missing", file=sys.stderr)
            return 1
    failures = (
        check_the_level_lands_where_the_orb_can_use_it()
        + check_the_orb_moves_on_the_local_path()
        + check_the_failures_are_told_apart()
        + check_the_settings_do_not_conflate_them()
        + check_the_microphone_comes_back_promptly()
    )
    for failure in failures:
        print(f"FAIL  {failure}", file=sys.stderr)
    if failures:
        print(f"\n{len(failures)} failure(s)", file=sys.stderr)
        return 1
    print(
        "on-device turn: the level mapping, the orb's progress, the five failure "
        "sentences, the settings' two meanings of \"on this phone\" and the "
        "microphone hand-back all agree"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
