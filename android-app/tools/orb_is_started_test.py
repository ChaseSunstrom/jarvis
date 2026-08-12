#!/usr/bin/env python3
"""Executable spec: a JarvisOrbView nobody starts draws NOTHING.

## The bug this exists to make impossible

`VoiceIdentityActivity` built a `JarvisOrbView`, gave it 160dp of the screen,
set its mode, fed it the live mic level on every audio callback and set its
caption to LISTENING. It drew nothing. Not a still orb — a hole.

Two facts in `JarvisOrbView` combine into that:

  * The master alpha every layer is drawn through is `entranceProgress`, and it
    is initialised to `0f`::

        val a = boot?.coreAlpha ?: entranceProgress          // master fade

  * The frame clock — which integrates the breathing, the ring rotation, the
    blob drift and the smoothed amplitude, and issues the single `invalidate()`
    per frame — is started only by `startClock()`, and the only callers of that
    are `startEntrance()`, `beginBoot()` and `endBoot()`.

So a view that is never handed to one of those three is stuck at alpha 0 with a
clock that never ticks. `setAmplitude` deliberately does not invalidate (the
clock is meant to), `setMode` invalidates a few times through its colour
animator, and every one of those redraws paints the whole reactor at zero
opacity.

Nothing else in the fast lane can see this. It compiles. It lays out. It has no
null to dereference and throws nothing. `onDraw` runs. The JVM tests do not
start an Activity, and the instrumented suite does not look at this screen.
The only report it can produce is a person saying *"there's no animation or ANY
indicator"* — which is what happened.

`onAttachedToWindow` looks like it would save this, and does not::

    if (wasRunning) startClock()

`wasRunning` is set in `onDetachedFromWindow` from `clockRunning`. On a view
that has never run, it is false, and attaching changes nothing.

## What is checked

Every shipping file that constructs a `JarvisOrbView` also calls one of the
three methods that start its clock. Per file rather than per construction —
see the check itself for why, and for what that costs.

Plus the enrolment screen's own half of the report: tap to record rather than
hold, a capture that ends itself, and a caption that does not depend on the
custom view drawing at all.

Run:  python3 android-app/tools/orb_is_started_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "src" / "main" / "kotlin"
ORB = MAIN / "ai" / "jarvis" / "app" / "ui" / "JarvisOrbView.kt"
ENROLMENT = MAIN / "ai" / "jarvis" / "app" / "VoiceIdentityActivity.kt"

#: The three public methods that reach `startClock()`. Derived below from the
#: source rather than trusted, so a fourth one added later is picked up and a
#: renamed one fails loudly instead of silently widening the check.
EXPECTED_STARTERS = {"startEntrance", "beginBoot", "endBoot"}


def kotlin_files() -> list[Path]:
    return sorted(MAIN.rglob("*.kt"))


def code_of(path: Path) -> str:
    """A file's Kotlin with every comment removed.

    Not tidiness — correctness of this file. The check below asks whether a
    host calls `startEntrance()`, and the host that was BROKEN now carries a
    long comment explaining why it must. Scanned raw, that comment satisfies
    the search: deleting the actual call left this spec passing 8/8, which is
    the precise failure `dispatch_spec_test._registry_code` was written for
    ("a comment quoting one of these needles moves the anchor").
    """
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


#: A member function declaration, at EXACTLY the class's own indent.
#:
#: Any run of modifiers, `fun`, an optional generic parameter list, the name.
#: `override fun` and `private fun <T> x(` both have to match — miss either and
#: the function BEFORE it appears to own the body of the one that was skipped,
#: which is how the first draft of this file decided `applyBlend` starts the
#: clock (it does not; `onAttachedToWindow` two declarations later does).
#:
#: The four-space anchor is the other half. Match at any indent and a `fun`
#: inside an anonymous object — `object : AnimatorListenerAdapter() { override
#: fun onAnimationEnd(...)` — becomes a boundary, and the rest of the ENCLOSING
#: function is attributed to it. That is not hypothetical either: it is exactly
#: how the second draft lost `startEntrance`, whose `startClock()` sits after
#: such a listener.
_FUN = re.compile(r"\n {4}(?:[\w@]+ +)*fun +(?:<[^>]*> *)?(\w+) *\(")


def functions(src: str) -> dict[str, str]:
    """`name -> body`, each running to the next declaration.

    Not a parser. It is enough to see the calls a function makes directly,
    which is all anything here asks.
    """
    marks = [(m.group(1), m.start(), m.end()) for m in _FUN.finditer(src)]
    out: dict[str, str] = {}
    for i, (name, _start, end) in enumerate(marks):
        stop = marks[i + 1][1] if i + 1 < len(marks) else len(src)
        out[name] = out.get(name, "") + src[end:stop]
    return out


def starters() -> set[str]:
    """The functions in JarvisOrbView whose bodies call `startClock()`."""
    found = {
        name for name, body in functions(code_of(ORB)).items()
        if "startClock()" in body
    }
    # `startClock` defines it; `onAttachedToWindow` re-enters it for a view that
    # was already running once (`if (wasRunning)`) and cannot start a fresh one.
    return found - {"startClock", "onAttachedToWindow"}


def test_the_only_ways_to_start_the_clock_are_the_ones_named_here() -> None:
    """If this fails, the check below is looking for the wrong thing."""
    assert starters() == EXPECTED_STARTERS, (
        f"JarvisOrbView's clock starters changed: {sorted(starters())}. Update "
        f"EXPECTED_STARTERS, and check every host still calls one of them."
    )


def test_the_master_alpha_still_begins_at_zero() -> None:
    """The premise. If the view ever draws at full opacity un-started, this
    whole spec is guarding something that is no longer true — which is worth
    knowing, not worth silently keeping."""
    src = code_of(ORB)
    assert "private var entranceProgress = 0f" in src, (
        "entranceProgress no longer starts at 0 — re-derive what an un-started "
        "JarvisOrbView actually looks like before relaxing anything here"
    )
    assert "?: entranceProgress" in src, (
        "the master alpha is no longer entranceProgress; this spec's reasoning "
        "about an invisible orb needs redoing"
    )


def test_the_frame_clock_is_the_only_thing_that_invalidates() -> None:
    """`setAmplitude` deliberately does not invalidate — the clock does. That is
    what turns "no clock" into "no redraw at all" rather than a slow orb."""
    body = functions(code_of(ORB)).get("setAmplitude")
    assert body is not None, "JarvisOrbView.setAmplitude is gone"
    assert "invalidate" not in body, (
        "setAmplitude now invalidates, which would have hidden the dead-clock "
        "bug behind a half-working orb instead of an empty one"
    )


def test_every_orb_is_started_by_the_code_that_builds_it() -> None:
    """The check the bug asked for.

    Per FILE, not per construction. Whether a particular `JarvisOrbView(...)`
    is the one a particular `startEntrance()` is called on needs a Kotlin
    parser and a resolver, and this repository has neither in its fast lane.
    What a file-scoped check does catch is the bug that actually happened —
    a host that starts NO orb, anywhere, ever — and it cannot produce a false
    failure, which is what makes it worth having. Every host in this app owns
    exactly one orb; the day one owns two, this check gets weaker and the
    comment above says so out loud rather than the check quietly passing.
    """
    unstarted: list[str] = []
    for path in kotlin_files():
        if path == ORB:
            continue
        src = code_of(path)
        match = re.search(r"JarvisOrbView\(", src)
        if not match:
            continue
        if not any(f"{name}()" in src for name in EXPECTED_STARTERS):
            line = src[: match.start()].count("\n") + 1
            unstarted.append(f"{path.relative_to(ROOT)}:{line}")
    assert not unstarted, (
        "these build a JarvisOrbView and never start its clock, so it draws at "
        "alpha 0 and never redraws — a hole in the layout, not a still orb: "
        f"{unstarted}"
    )


# ---------------------------------------------------------------------------
# The enrolment screen specifically, which is where this was found.
# ---------------------------------------------------------------------------
def test_enrolment_records_on_a_tap_rather_than_a_hold() -> None:
    """Reported alongside the dead orb: *"I shouldn't have to hold it down,
    just press for it to listen"*.

    Hold-to-talk has no feedback of its own — the finger is on the button, and
    the button is the same button. It was survivable only while the orb worked,
    and the orb never worked. Both halves are fixed together, and a touch
    listener coming back would restore half the original complaint.
    """
    src = code_of(ENROLMENT)
    assert "setOnTouchListener" not in src, (
        "the enrolment button is back to push-to-talk"
    )
    assert "HOLD TO RECORD" not in src
    assert "fun toggleRecording()" in src, (
        "nothing turns the capture on and off from one control"
    )


def test_a_capture_cannot_run_forever() -> None:
    """Tap-to-stop means the user can walk away mid-capture. Something has to
    end it, and it must not be the byte cap — a sample truncated at 25s is
    still submitted, and a truncated ENROLMENT sample is written into the
    profile and skews it."""
    src = code_of(ENROLMENT)
    assert "ENROL_WINDOW_MS" in src and "TEST_WINDOW_MS" in src
    assert "main.postDelayed(autoStop" in src, "no capture ever ends by itself"
    enrol = int(re.search(r"ENROL_WINDOW_MS = ([\d_]+)L", src).group(1).replace("_", ""))
    cap_bytes = 16000 * 2 * 25
    assert enrol * 16000 * 2 // 1000 < cap_bytes, (
        "the auto-stop window outlasts MAX_SAMPLE_BYTES, so the byte cap is "
        "what truncates a forgotten capture"
    )


def test_stopping_a_capture_does_not_drop_the_servers_reply() -> None:
    """`removeCallbacksAndMessages(null)` clears EVERY message on the main
    handler, and `offMainThread` hands its result back through exactly such a
    message — the one that clears `busy` and re-enables the buttons. Losing it
    leaves the screen permanently inert with nothing logged."""
    src = code_of(ENROLMENT)
    assert "removeCallbacksAndMessages" not in src, (
        "stopCapture can again cancel the message carrying a server response"
    )
    assert "main.removeCallbacks(autoStop)" in src
    assert "main.removeCallbacks(countdown)" in src


def test_the_screen_says_it_is_listening_in_words_as_well() -> None:
    """The orb is the indicator, and the orb is what failed. A caption and a
    status line cost nothing and do not depend on a custom view drawing."""
    src = code_of(ENROLMENT)
    assert 'setStateLabel("LISTENING' in src, "no caption under the orb"
    assert "Listening — say the line" in src, "no words on the screen either"
    assert "Mode.THINKING" in src, (
        "the orb goes still while the server answers, which is what a broken "
        "screen looks like"
    )


def main() -> int:
    tests = [
        (n, f) for n, f in sorted(globals().items())
        if n.startswith("test_") and callable(f)
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
        f"({len(kotlin_files())} shipping Kotlin files scanned)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
