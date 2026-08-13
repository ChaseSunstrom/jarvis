#!/usr/bin/env python3
"""Executable spec: teaching Jarvis your voice, without being asked twice.

`docs/voice-identity.md` explains why `/api/voice/speaker/enrol` takes **one
sample per request**:

> Enrolment takes one sample per request because the useful feedback is per
> sample: "that one was too quiet, say it again" between phrases, rather than
> one failure for the whole set at the end.

The server holds up its end. `async_enrol` returns `accepted`, and a `sample`
block carrying `speech_ms`, `voiced_frames` and `has_pitch`. The phone parsed
none of it: `VoiceIdentityClient.enrol` mapped the response straight through
`Status.from`, which reads counts and thresholds and drops everything about the
sample just given. The screen showed a running total. **The per-sample half of a
per-sample API was thrown away in the parser**, and the reason the API is shaped
that way could not be acted on at all.

Three more, all on `VoiceIdentityActivity`:

## `promptIndex` was not persisted

A plain field starting at 0, reset only by FORGET MY VOICE. Rotate the phone,
take a call, or come back tomorrow to finish, and the phrase list restarted from
the top — while the server's sample count kept climbing. The user re-read
phrases they had already given, which is precisely the thing this screen must
not ask for: the profile's whole value is that the samples DIFFER from each
other, and five recordings of one sentence teach it that its owner never varies.

The fix is not a persisted counter. `samples` already IS the index — with three
stored, the next phrase is the fourth — and a local number would be a second
opinion about something the server knows exactly.

## No step list

Progress was one line of text. Which phrases had been given was something the
user had to remember, so the screen and the server could disagree about where
enrolment had got to with nothing on screen showing it.

## No redo

Only FORGET MY VOICE and start over. There is an honest limit here and this file
pins it too: the API has four endpoints — status, enrol, verify, forget — and no
per-sample delete, so "say that one again" can re-offer the phrase and cannot
remove the sample already stored. The screen has to SAY that rather than imply a
clean second attempt.

Run:  python3 android-app/tools/enrolment_flow_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
KOTLIN = ANDROID / "app/src/main/kotlin/ai/jarvis/app"
DOC = ANDROID.parent / "docs" / "voice-identity.md"

SCREEN = KOTLIN / "VoiceIdentityActivity.kt"
CLIENT = KOTLIN / "config/VoiceIdentityClient.kt"


def code(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


# ---------------------------------------------------------------------------
# 1. the per-sample verdict
# ---------------------------------------------------------------------------


def test_the_client_parses_what_the_server_says_about_the_sample() -> None:
    src = code(CLIENT)
    assert "data class Enrolment(" in src, (
        "enrol() still returns only the profile status, so the per-sample "
        "verdict the whole one-sample-per-request API exists to deliver is "
        "dropped in the parser"
    )
    enrol = re.search(r"fun enrol\(pcm: ByteArray\).*?\n        \}", src, re.S)
    assert enrol, "VoiceIdentityClient.enrol is gone"
    body = enrol.group(0)
    assert '"sample"' in body, "the response's per-sample block is not read"
    for field in ("speech_ms", "has_pitch"):
        assert field in body, f"the sample's {field} is dropped"


def test_an_absent_field_does_not_invent_a_warning() -> None:
    """A server too old to send `has_pitch` must not produce a warning about a
    sample nobody measured."""
    src = code(CLIENT)
    enrol = re.search(r"fun enrol\(pcm: ByteArray\).*?\n        \}", src, re.S)
    assert 'optBoolean("has_pitch", true)' in enrol.group(0), (
        "has_pitch defaults to false for a server that does not send it, so "
        "every sample from an older jarvis-core is reported as bad"
    )


def test_the_verdict_only_speaks_up_when_something_is_wrong() -> None:
    """A screen that congratulates the user on each of five samples trains them
    to stop reading it — which is exactly when the sixth one needs reading."""
    src = code(CLIENT)
    note = re.search(r"fun note\(\): String\? = when \{.*?\n        \}", src, re.S)
    assert note, "Enrolment has no note()"
    body = note.group(0)
    assert "else -> null" in body, (
        "note() always says something, so nothing it says stands out"
    )
    assert "hasPitch" in body and "speechMs" in body, (
        "note() ignores one of the two things the server measured"
    )


def test_the_screen_shows_the_verdict_first() -> None:
    src = code(SCREEN)
    assert "lastNote" in src, "the screen has nowhere to put a per-sample verdict"
    submit = re.search(r"private fun submitEnrolment\(.*?\n        \}", src, re.S)
    assert submit and "enrolment.note()" in submit.group(0), (
        "the enrolment response's verdict is fetched and not shown"
    )
    render = re.search(r"private fun render\(fresh:.*?\n    \}", src, re.S)
    assert render and "lastNote" in render.group(0), (
        "the verdict never reaches the screen"
    )
    # And it is cleared when the next capture starts: a verdict about the
    # PREVIOUS sample sitting under a live microphone is a lie about this one.
    start = re.search(r"private fun startCapture\(.*?\n    \}", src, re.S)
    assert start and "lastNote = null" in start.group(0), (
        "the previous sample's verdict stays on screen while the next one is "
        "being recorded"
    )


# ---------------------------------------------------------------------------
# 2. where enrolment has got to
# ---------------------------------------------------------------------------


def test_the_phrase_index_comes_from_the_server() -> None:
    """Not persisted locally — DERIVED, which is persistence for free and has
    no way to disagree with the count it is derived from."""
    src = code(SCREEN)
    assert not re.search(r"var promptIndex\s*=\s*0", src), (
        "promptIndex is a plain field again, so rotating the phone or leaving "
        "mid-enrolment restarts the phrase list from the top while the "
        "server's sample count keeps climbing"
    )
    index = re.search(r"private val promptIndex: Int\s*\n\s*get\(\) = ([^\n]+)", src)
    assert index, "promptIndex is no longer derived"
    assert "samples" in index.group(1), (
        "promptIndex is derived from something other than the server's sample "
        "count, which is the only thing that knows how many phrases were given"
    )
    assert "coerceAtLeast(0)" in index.group(1), (
        "promptIndex can go negative, which indexes the phrase list backwards"
    )


def test_render_reads_the_index_after_the_status_it_derives_from() -> None:
    """Ordering, and it is a real trap: `promptIndex` reads `status`, so reading
    it before the assignment shows the PREVIOUS sample count — the screen would
    be exactly one phrase behind for the whole of enrolment."""
    src = code(SCREEN)
    render = re.search(r"private fun render\(fresh:.*?\n    \}", src, re.S)
    assert render, "render() is gone"
    body = render.group(0)
    assign = body.find("status = fresh")
    read = body.find("promptIndex")
    assert assign >= 0 and read > assign, (
        "render() reads promptIndex before assigning `status`, so the phrase "
        "shown is one behind the server's count"
    )


def test_there_is_a_step_list_with_per_phrase_state() -> None:
    src = code(SCREEN)
    assert "private fun renderSteps(" in src, (
        "progress is one line of text again, so which phrases have been given "
        "is something the user has to remember"
    )
    steps = re.search(r"private fun renderSteps\(.*?\n    \}", src, re.S)
    body = steps.group(0)
    assert "[ok]" in body and "[>>]" in body, (
        "the step list does not distinguish given, current and still-to-say"
    )
    assert "JarvisUi.describe(" in body, (
        "the step glyphs are read out as punctuation — TalkBack says "
        "bracket-o-k-bracket — and the row has no English description"
    )


# ---------------------------------------------------------------------------
# 3. redo, and its honest limit
# ---------------------------------------------------------------------------


def test_redo_exists_and_admits_what_it_cannot_do() -> None:
    src = code(SCREEN)
    assert "private fun redo(" in src, (
        "there is still no way to re-read a phrase; the only remedy offered is "
        "FORGET MY VOICE and start over"
    )
    body = re.search(r"private fun redo\(\) \{.*?\n    \}", src, re.S).group(0)
    assert "redo += 1" in body, "redo does not move the phrase list back"
    assert "no way to remove just one" in body or "stays in" in body, (
        "redo implies a clean second attempt. The API has status, enrol, "
        "verify and forget — there is NO per-sample delete — so the sample "
        "already stored stays in the profile, and a screen that does not say "
        "so is worse than no redo at all."
    )
    assert "promptIndex > 0" in code(SCREEN), (
        "redo is offered before there is an earlier phrase to go back to"
    )


def test_an_accepted_sample_clears_the_redo_offset() -> None:
    """Otherwise the list walks backwards one phrase per redo, forever."""
    src = code(SCREEN)
    submit = re.search(r"private fun submitEnrolment\(.*?\n        \}", src, re.S)
    assert submit and "redo = 0" in submit.group(0), (
        "a redo offset survives the sample that answered it, so the phrase "
        "list drifts further behind the server with every use"
    )
    forget = re.search(r"private fun forget\(\) = .*?\n    \}", src, re.S)
    assert forget and "redo = 0" in forget.group(0), (
        "FORGET MY VOICE does not reset the redo offset"
    )


# ---------------------------------------------------------------------------
# 4. the document
# ---------------------------------------------------------------------------


def test_the_document_describes_the_screen_that_exists() -> None:
    """It said *"Five phrases, hold the button while you say each one"* and was
    stale on both counts: the count is server-driven (`min_samples` /
    `max_samples`) and it is tap-to-start, tap-to-stop."""
    text = DOC.read_text(encoding="utf-8")
    assert "hold the button while you say each one" not in text, (
        "docs/voice-identity.md still says press-and-hold. Holding does "
        "nothing; the button says TAP TO SPEAK."
    )
    assert "TAP TO SPEAK" in text, "the doc does not say what the control is"
    assert "min_samples" in text, (
        "the doc still implies a fixed number of phrases; the count comes from "
        "the server"
    )
    # And the redo limit, which a reader will otherwise discover by trying it.
    assert "per-sample delete" in text, (
        "the doc does not mention that SAY THAT ONE AGAIN cannot remove the "
        "sample already stored"
    )
    # The screen's own label has to match what the doc now claims.
    assert 'RECORD_START = "TAP TO SPEAK"' in code(SCREEN), (
        "the doc and the button no longer agree on what the control is called"
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
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
