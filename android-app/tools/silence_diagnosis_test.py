#!/usr/bin/env python3
"""Executable spec for "why did Jarvis not hear me?".

`JarvisConversation` used to answer that question with one sentence for three
different faults:

  * the recorder handed back digital silence (on GrapheneOS, almost always the
    per-app Sensors toggle, which is separate from the Microphone permission);
  * audio arrived and never reached the start threshold;
  * the pipeline never reached LISTENING, so the VAD was gated off and no
    amount of talking could ever have produced speech.

All three closed the surface saying "check the microphone permission", which
is wrong for two of them and actively misleading for the third — the owner of
this app spent a long time granting a permission that was already granted.

They are distinguishable from the evidence the conversation already has, so
this pins the mapping. The discriminator is the peak smoothed level: capture
starts independently of the socket, so a flat-zero peak means the recorder
produced nothing, and any real peak means audio arrived.

Run:  python3 android-app/tools/silence_diagnosis_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# --- the rules, mirrored from JarvisConversation.kt -------------------------

# Mirrors JarvisConversation. Lowered by ten after a report of having to shout:
# the old figure came from the browser, whose getUserMedia applies automatic
# gain, and this capture path deliberately does not.
#: The edge is no longer a constant — it is a multiple of the room's own noise
#: floor (see VoiceActivity). In a silent room it bottoms out at MIN_START,
#: which is the value this file reasons about, because that is the quietest
#: edge the diagnosis can ever be measured against.
START_THRESHOLD = 0.004
DEAD_MIC_LEVEL = 0.0005

DEAD_MIC = "dead-mic"
TOO_QUIET = "too-quiet"
NOTHING_HEARD = "nothing-heard"
NO_PIPELINE = "no-pipeline"


def diagnose(reached_listening: bool, saw_speech: bool, peak_level: float) -> str | None:
    """What the surface says, or None when it says nothing.

    Mirrors the two timers together: the handshake timeout fires when LISTENING
    never arrived, and the inactivity timeout fires when it did and no speech
    followed.
    """
    if not reached_listening:
        return NO_PIPELINE
    if saw_speech:
        return None  # an ordinary end of conversation
    if peak_level <= DEAD_MIC_LEVEL:
        return DEAD_MIC
    if peak_level < START_THRESHOLD:
        return TOO_QUIET
    return NOTHING_HEARD


TABLE: list[tuple[bool, bool, float, str | None, str]] = [
    # reached_listening, saw_speech, peak, verdict, why it is in the table
    (
        False, False, 0.0, NO_PIPELINE,
        "the app pointed at the web console instead of jarvis-core: the mic is "
        "fine and the VAD is gated off, so blaming the mic sends the user the "
        "wrong way entirely",
    ),
    (
        False, False, 0.31, NO_PIPELINE,
        "a LOUD room with no pipeline is still a pipeline fault — the peak must "
        "not out-vote the handshake",
    ),
    (
        True, False, 0.0, DEAD_MIC,
        "the recorder handed back digital silence: Sensors toggle, or another "
        "app holding the mic",
    ),
    (
        True, False, 0.0004, DEAD_MIC,
        "dither and the odd non-zero sample are still a dead mic; exactly zero "
        "is too strict a test for real hardware",
    ),
    (
        True, False, 0.0012, TOO_QUIET,
        "audio arrived and never crossed the start edge — a distance or gain "
        "problem, not a permission one",
    ),
    (
        True, False, 0.00399, TOO_QUIET,
        "just under the edge is the most frustrating case and must name the "
        "numbers. The edge is the room's now, so in a silent room it is "
        "MIN_START and in a loud one it is higher — the sentence quotes "
        "whichever one actually decided",
    ),
    (
        True, False, 0.004, NOTHING_HEARD,
        "at the edge the VAD would have latched, so silence here is a genuine "
        "did-not-speak",
    ),
    (
        True, False, 0.012, NOTHING_HEARD,
        "an ordinary voice at arm's length through an unprocessed phone mic. "
        "This is the case the old 0.02 edge got wrong, and calling it TOO_QUIET "
        "is what told people to move closer to a microphone that could hear "
        "them perfectly well",
    ),
    (
        True, False, 0.4, NOTHING_HEARD,
        "plenty of level, no speech detected: nothing more specific to say",
    ),
    (
        True, True, 0.3, None,
        "speech was heard; the conversation simply ended",
    ),
    (
        True, True, 0.0, None,
        "sawSpeech wins over the peak — an ordinary end must never be an error",
    ),
]


def check_table() -> list[str]:
    failures = []
    for reached, saw, peak, expected, why in TABLE:
        got = diagnose(reached, saw, peak)
        if got != expected:
            failures.append(
                f"reached_listening={reached} saw_speech={saw} peak={peak}\n"
                f"    expected {expected}, got {got}\n    ({why})"
            )
    return failures


def check_distinct_messages() -> list[str]:
    """Four faults, four sentences. A shared string is a shared wrong answer."""
    source = _source()
    failures = []
    messages = {}
    for name in ("DEAD_MIC", "TOO_QUIET", "NOTHING_HEARD", "NO_PIPELINE"):
        # These are multi-line `"..." + "..."` concatenations, so take the whole
        # declaration up to the next one and join its fragments. Matching a
        # single quoted run would silently read only the first line, which is
        # how the first draft of this check "passed" while looking at a third of
        # each message.
        match = re.search(
            rf"const val {name} =(.*?)(?=\n\s*(?:/\*\*|private const val|\}}))",
            source,
            re.S,
        )
        if not match:
            failures.append(f"JarvisConversation.kt no longer defines {name}")
            continue
        text = "".join(re.findall(r'"(.*?)"', match.group(1), re.S))
        if not text.strip():
            # No string literal at all: either the declaration moved, or it was
            # aliased to another constant. Aliasing is exactly the regression
            # this file exists to catch, and an empty parse would otherwise
            # sail through the distinctness check below by being distinctly
            # empty.
            failures.append(f"{name} has no sentence of its own")
            continue
        messages[name] = text

    if len(set(messages.values())) != len(messages):
        failures.append("two faults share a sentence; that is the bug this file exists to stop")

    # The dead-mic case must name the toggle that is actually the cause on the
    # platform this app targets, not the permission the user already granted.
    if "DEAD_MIC" in messages and "Sensors" not in messages["DEAD_MIC"]:
        failures.append("the dead-mic message no longer names the Sensors toggle")
    # The pipeline case must name the ports, because that is the mistake.
    if "NO_PIPELINE" in messages and "8080" not in messages["NO_PIPELINE"]:
        failures.append("the no-pipeline message no longer names jarvis-core's port")
    return failures


def check_kotlin_still_says_so() -> list[str]:
    """The rules are still in the Kotlin, spelled the way this file mirrors."""
    source = _source()
    required = {
        "vad.peak <= VoiceActivity.DEAD_MIC_LEVEL -> DEAD_MIC": "the dead-mic branch",
        "vad.peak < vad.startEdge -> TOO_QUIET": "the too-quiet branch",
        "main.removeCallbacks(handshake)": "the handshake timer being cancelled on LISTENING",
        "main.postDelayed(handshake, HANDSHAKE_MS)": "the handshake timer being armed",
    }
    return [
        f"JarvisConversation.kt no longer contains {what} ({snippet!r})"
        for snippet, what in required.items()
        if snippet not in source
    ]


def _source() -> str:
    path = (
        Path(__file__).resolve().parents[1]
        / "app/src/main/kotlin/ai/jarvis/app/assist/JarvisConversation.kt"
    )
    return path.read_text(encoding="utf-8") if path.exists() else ""


def main() -> int:
    if not _source():
        print("FAIL cannot find JarvisConversation.kt", file=sys.stderr)
        return 1
    failures = check_table() + check_distinct_messages() + check_kotlin_still_says_so()
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    if failures:
        print(f"\n{len(failures)} failed", file=sys.stderr)
        return 1
    print(f"silence diagnosis: {len(TABLE)} cases and the Kotlin mirror agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
