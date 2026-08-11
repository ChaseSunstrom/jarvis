#!/usr/bin/env python3
"""Executable spec for the floating orb — the thing that appears over whatever
app you are using when you say "Hey Jarvis".

The reported symptom: *"it can speak back to me … but there's no Siri like
overlay/animation on my screen"*. The voice half worked and the visible half
did not exist. `JarvisAssistActivity` is an **Activity**, and Android 12+
silently drops a background activity start, so the conversation arrived as a
notification to tap and nothing was ever drawn over the app in front.

`AssistOverlay` is a real `TYPE_APPLICATION_OVERLAY` window, and `SiriOrbView`
is what it draws. Four things are pinned here, each because getting it wrong
produces a bug that is invisible in review:

  1. **The palette.** "It changes colour" is a behaviour a user can describe, so
     the table is a table, every state has one, and no two states share it —
     otherwise THINKING and LISTENING would look identical from across a room.
  2. **The window flags.** An overlay that takes focus steals the keyboard from
     whatever the user was typing in. One sized to the screen swallows every
     touch on the app behind it, because FLAG_NOT_TOUCH_MODAL only passes
     through touches *outside* the window's own bounds.
  3. **The fallback.** Overlay windows are never shown above the keyguard, and
     the permission is one the user has to grant by hand. Both cases must fall
     back to the full-screen intent rather than producing nothing.
  4. **The microphone.** The overlay runs the conversation inside the same
     service that holds the wake mic, so it must give the mic up first and hand
     it back at the end — the invariant `wake_listener_test.py` already guards
     for the Activity path.

Run:  python3 android-app/tools/siri_overlay_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TONES = ("IDLE", "LISTENING", "THINKING", "SPEAKING", "ERROR")
BLOB_COUNT = 3


def parse_palette(text: str, fn: str) -> dict[str, list[str]]:
    """Pull `fn`'s `Tone.X -> …` arm out of SiriPalette.kt."""
    body = re.search(rf"fun {fn}\(tone: Tone\).*?\n    \}}", text, re.S)
    if not body:
        return {}
    out: dict[str, list[str]] = {}
    for tone, arm in re.findall(
        r"Tone\.(\w+)\s*->\s*(.+)", body.group(0)
    ):
        out[tone] = re.findall(r"0x([0-9A-Fa-f]{8})", arm)
    return out


def check_palette(android: Path) -> int:
    path = android / "app/src/main/kotlin/ai/jarvis/app/ui/SiriPalette.kt"
    if not path.is_file():
        print(f"FAIL  {path} is missing")
        return 1
    text = path.read_text(encoding="utf-8")
    failures = 0

    blobs = parse_palette(text, "blobs")
    cores = parse_palette(text, "core")

    for tone in TONES:
        if tone not in blobs:
            print(f"FAIL  no blob colours for {tone}; that state would draw nothing")
            failures += 1
            continue
        if len(blobs[tone]) != BLOB_COUNT:
            print(
                f"FAIL  {tone} has {len(blobs[tone])} blob colours, not {BLOB_COUNT} — "
                "the view draws BLOB_COUNT of them and would index off the end"
            )
            failures += 1
        if tone not in cores:
            print(f"FAIL  no core colour for {tone}")
            failures += 1
        for hexed in blobs[tone]:
            if hexed[:2].lower() != "ff":
                print(
                    f"FAIL  {tone} blob #{hexed} is not fully opaque; the view sets "
                    "its own alpha from the microphone level and would multiply twice"
                )
                failures += 1

    # Distinctness is the point: the states have to be tellable apart at a
    # glance, which is the whole reason the orb changes colour at all.
    seen: dict[tuple[str, ...], str] = {}
    for tone in TONES:
        key = tuple(blobs.get(tone, []))
        if not key:
            continue
        if key in seen:
            print(f"FAIL  {tone} and {seen[key]} are the same colours")
            failures += 1
        seen[key] = tone

    # THINKING must not be mistakable for ERROR: amber and red are the two
    # states a glance most easily confuses, and one of them means "something
    # went wrong in your house".
    if blobs.get("THINKING", [""])[0] == blobs.get("ERROR", [""])[0]:
        print("FAIL  thinking and error lead with the same colour")
        failures += 1

    rates = dict(
        re.findall(r"Tone\.(\w+)\s*->\s*1f\s*/\s*([0-9.]+)f", text)
    )
    for tone in TONES:
        if tone not in rates:
            print(f"FAIL  no orbit rate for {tone}; the orb would not move in that state")
            failures += 1
    if rates.get("IDLE") and rates.get("LISTENING"):
        # Idle has to be the slowest, or resting looks busier than hearing you.
        if float(rates["IDLE"]) <= float(rates["LISTENING"]):
            print("FAIL  the idle orb is not the calmest one")
            failures += 1

    if f"BLOB_COUNT = {BLOB_COUNT}" not in text:
        print(f"FAIL  BLOB_COUNT is no longer {BLOB_COUNT}")
        failures += 1
    return failures


def check_every_mode_has_a_tone(android: Path) -> int:
    """The two state machines must line up.

    `JarvisOrbView.Mode` is what the conversation engine speaks; `SiriPalette.Tone`
    is what the orb wears. The mapping used to be a `when` inside `SiriOrbView`,
    which meant a third file had to be kept in step with two others by hand; it
    is on the mode itself now, so a state added without a tone will not compile.

    What can still go wrong, and is what this checks: several modes collapsing
    onto one tone. That compiles perfectly and makes two states of the house
    indistinguishable from across a room.
    """
    view = android / "app/src/main/kotlin/ai/jarvis/app/ui/SiriOrbView.kt"
    orb = android / "app/src/main/kotlin/ai/jarvis/app/ui/JarvisOrbView.kt"
    if not view.is_file():
        print(f"FAIL  {view} is missing")
        return 1
    text = view.read_text(encoding="utf-8")
    failures = 0

    mapping = dict(
        re.findall(
            r"^\s{8}(\w+)\(SiriPalette\.Tone\.(\w+)\)[,;]",
            orb.read_text(encoding="utf-8"),
            re.M,
        )
    )
    if not mapping:
        print("FAIL  JarvisOrbView.Mode no longer names a tone per state")
        return 1
    for tone in TONES:
        if tone not in mapping.values():
            print(f"FAIL  no mode ever puts the orb into the {tone} tone")
            failures += 1
    if len(set(mapping.values())) != len(mapping):
        print("FAIL  two modes share one tone; those states are indistinguishable")
        failures += 1
    if "fun setMode(mode: JarvisOrbView.Mode) = setTone(mode.tone)" not in text:
        print("FAIL  the floating orb maps modes to tones itself again, in a third place")
        failures += 1
    return failures


def check_window_flags(android: Path) -> int:
    path = android / "app/src/main/kotlin/ai/jarvis/app/assist/AssistOverlay.kt"
    if not path.is_file():
        print(f"FAIL  {path} is missing")
        return 1
    text = path.read_text(encoding="utf-8")
    failures = 0

    if "TYPE_APPLICATION_OVERLAY" not in text:
        print("FAIL  the overlay is not an overlay window; it cannot float over other apps")
        failures += 1
    for flag, why in (
        ("FLAG_NOT_FOCUSABLE", "an overlay that takes focus steals the keyboard"),
        ("FLAG_NOT_TOUCH_MODAL", "without it every touch on the screen goes to the orb"),
        ("FLAG_HARDWARE_ACCELERATED", "screen-blending on a software canvas drops frames"),
    ):
        if flag not in text:
            print(f"FAIL  {flag} is not set: {why}")
            failures += 1
    if "PixelFormat.TRANSLUCENT" not in text:
        print("FAIL  the overlay window is not translucent; it would paint a black box")
        failures += 1
    # Sized to the card, never to the screen: FLAG_NOT_TOUCH_MODAL passes
    # through touches OUTSIDE the window, so a MATCH_PARENT width would eat
    # every tap across the display. Only the WINDOW's own params are checked —
    # the child views inside the card are match-width on purpose.
    params = re.search(r"private fun params\(\).*?\n    \}", text, re.S)
    if not params:
        print("FAIL  AssistOverlay has no window params")
        failures += 1
    elif "MATCH_PARENT" in params.group(0):
        print("FAIL  the overlay window is screen-width; it swallows taps on the app behind")
        failures += 1
    if "canDrawOverlays" not in text:
        print("FAIL  the overlay does not check for its permission before adding a window")
        failures += 1
    # addView throws on some ROMs even with the permission; a throw must not
    # take the conversation down.
    add = re.search(r"fun attach\(\).*?\n    \}", text, re.S)
    if add and "catch" not in add.group(0):
        print("FAIL  a refused addView is not caught, so a wake word would crash the service")
        failures += 1
    if add and "return false" not in add.group(0):
        print("FAIL  attach() cannot report failure, so the caller cannot fall back")
        failures += 1
    return failures


def check_the_fallback_survives(android: Path) -> int:
    """Overlay unavailable must still put Jarvis on screen."""
    path = android / "app/src/main/kotlin/ai/jarvis/app/assist/WakeWordService.kt"
    text = path.read_text(encoding="utf-8")
    failures = 0

    start = re.search(r"private fun startOverlayConversation\(\).*?\n    \}", text, re.S)
    if not start:
        print("FAIL  WakeWordService never tries to show the floating orb")
        return 1
    body = start.group(0)
    if "AssistOverlay.canShow" not in body:
        print("FAIL  the overlay is attached without checking for its permission")
        failures += 1
    # The keyguard is not this function's business.
    #
    # It was, and that was a bug: `isKeyguardLocked()` is true whenever the
    # keyguard is up, which on any phone with a secure lock includes the screen
    # merely being OFF, so returning false here meant the caller could not tell
    # "no permission" from "locked" — and the fallback for those two is not the
    # same. The decision moved to `onWakeWord`, which owns both surfaces.
    if "isLocked()" in body:
        print(
            "FAIL  startOverlayConversation decides about the keyguard again. That "
            "conflates 'no permission' with 'locked', which need different answers."
        )
        failures += 1

    wake = re.search(r"override fun onWakeWord\(.*?\n    \}", text, re.S)
    if not wake:
        print("FAIL  there is no wake handler any more")
        return failures + 1
    wake_body = wake.group(0)
    if "startOverlayConversation()" not in wake_body:
        print("FAIL  the wake word does not try the floating orb first")
        failures += 1
    for fallback in ("startActivity(intent)", "showHeard(intent)"):
        if fallback not in wake_body:
            print(f"FAIL  the {fallback} fallback is gone; a locked phone gets nothing")
            failures += 1
    # The fallback must come AFTER the overlay attempt and be skipped when it
    # worked, or a successful overlay also fires a full-screen intent and the
    # user gets two Jarvises.
    if wake_body.index("startOverlayConversation()") > wake_body.index("showHeard(intent)"):
        print("FAIL  the notification fallback runs before the overlay is tried")
        failures += 1
    # ONE microphone. This is the invariant, and it is not obvious from any one
    # line of the handler.
    #
    # A `TYPE_APPLICATION_OVERLAY` window is not drawn above the keyguard — the
    # platform stopped honouring FLAG_SHOW_WHEN_LOCKED for non-Activity windows
    # long before this app's minSdk — so on a locked phone the overlay
    # conversation is one nobody can see, while the full-screen intent brings up
    # JarvisAssistActivity, which starts a conversation of its own. Both running
    # is two `AudioRecord`s on one microphone: the second startRecording wins
    # and the first goes silent, or it fails outright, and WHICH of the two is
    # deaf is a race with the activity's ACTION_PAUSE.
    #
    # So the keyguard is asked once, before anything opens a microphone, and the
    # overlay is not even attempted when it cannot be seen.
    if "val locked = isLocked()" not in wake_body:
        print(
            "FAIL  the wake handler no longer settles the keyguard question before "
            "opening a microphone"
        )
        failures += 1
    if "val showedOverlay = !locked && startOverlayConversation()" not in wake_body:
        print(
            "FAIL  the wake handler starts an overlay conversation on a locked phone. "
            "The full-screen intent starts a second one, and two AudioRecords on one "
            "microphone means one of the two Jarvises on screen is deaf."
        )
        failures += 1
    if "if (showedOverlay) {" not in wake_body:
        print(
            "FAIL  the wake handler does not stop after the overlay took the turn, so "
            "a successful overlay ALSO fires the full-screen intent"
        )
        failures += 1
    # The keyguard must be sampled before the microphone is opened, not after:
    # asked afterwards it is a check on a decision already made.
    asked = wake_body.find("val locked = isLocked()")
    opened = wake_body.find("startOverlayConversation()")
    if asked >= 0 and opened >= 0 and asked > opened:
        print("FAIL  the keyguard is checked after the overlay has already opened the mic")
        failures += 1
    if "wakeTheScreen()" not in wake_body:
        print(
            "FAIL  nothing turns the screen on. An orb drawn on a dark panel is "
            "not an orb anybody sees, and a wake word is for the phone you are "
            "not holding."
        )
        failures += 1
    return failures


def check_the_microphone_comes_back(android: Path) -> int:
    """The invariant wake_listener_test.py guards, for the new path.

    The overlay conversation runs inside the same service that holds the wake
    mic. Two `AudioRecord`s on one device is a coin toss, so the wake link has
    to be closed before the conversation opens one — and re-opened when it is
    over, or the wake word works exactly once.
    """
    path = android / "app/src/main/kotlin/ai/jarvis/app/assist/WakeWordService.kt"
    text = path.read_text(encoding="utf-8")
    failures = 0

    wake = re.search(r"override fun onWakeWord\(.*?\n    \}", text, re.S).group(0)
    if wake.index("pause()") > wake.index("startOverlayConversation()"):
        print("FAIL  the conversation opens the mic before the wake link gives it up")
        failures += 1

    end = re.search(r"private fun endOverlayConversation\(.*?\n    \}", text, re.S)
    if not end:
        print("FAIL  nothing tears the overlay conversation down")
        return failures + 1
    body = end.group(0)
    if "convo?.stop()" not in body or "overlay?.detach()" not in body:
        print("FAIL  ending the overlay conversation leaks the mic or the window")
        failures += 1
    if body.index("convo?.stop()") > body.index("overlay?.detach()"):
        print("FAIL  the window is removed before the conversation is stopped")
        failures += 1
    if "resume()" not in body:
        print("FAIL  the wake listener never takes the microphone back")
        failures += 1

    # Every way a conversation can end has to reach it.
    ui = re.search(r"private val overlayUi = object : JarvisConversation\.Ui \{.*?\n    \}", text, re.S)
    if not ui:
        print("FAIL  the overlay has no conversation callbacks")
        return failures + 1
    ui_body = ui.group(0)
    for hook in ("onIdle", "onError"):
        arm = re.search(rf"override fun {hook}\(.*?\n        \}}", ui_body, re.S)
        if not arm or "endOverlayConversation(giveMicBack = true)" not in arm.group(0):
            print(f"FAIL  {hook} does not give the microphone back")
            failures += 1
    # onDestroy must not: the service is going away and resume() would start a
    # link nothing will ever close.
    destroy = re.search(r"override fun onDestroy\(\).*?\n    \}", text, re.S)
    if destroy and "endOverlayConversation(giveMicBack = false)" not in destroy.group(0):
        print("FAIL  onDestroy re-opens the microphone on the way out")
        failures += 1
    return failures


def check_the_clock_runs(android: Path) -> int:
    """The orb has to actually move.

    `JarvisOrbView` drives itself with an infinite `ValueAnimator`, which ends
    on its first frame when the system animator duration scale is 0 — developer
    options, or a battery saver. That trade is deliberate there (Espresso would
    otherwise never see an idle main thread). It is the wrong trade for a
    surface nothing automated ever opens and whose entire job is to look alive,
    so this one uses a Choreographer, and stops when it is detached.
    """
    path = android / "app/src/main/kotlin/ai/jarvis/app/ui/SiriOrbView.kt"
    text = path.read_text(encoding="utf-8")
    failures = 0
    if "Choreographer" not in text:
        print("FAIL  the floating orb is not driven by a Choreographer")
        failures += 1
    if "removeFrameCallback" not in text or "onDetachedFromWindow" not in text:
        print("FAIL  the floating orb's clock is never stopped; it would spin forever")
        failures += 1
    if "import android.animation.ValueAnimator" in text:
        print("FAIL  the floating orb uses a ValueAnimator, which stops at scale 0")
        failures += 1
    return failures


def check_the_orb_is_solid_and_fits(android: Path) -> int:
    """The two complaints that outlived the panel: washed out, and boxed in.

    **Washed out.** Three translucent gradients screen-blended over whatever is
    behind them have nothing to add to, so over a white app the orb was a pale
    smudge. The fix is structural rather than a tuned alpha: a nearly opaque
    ball is drawn inside the layer FIRST, and the colours are lit against it.

    **Boxed in.** A View's canvas is clipped to its bounds by its parent, so
    anything that exceeds them is cut into a bright rectangle. Two things can:
    the halo, which grows with the microphone level, and — now that the floating
    orb wears the reactor's rings too — the outermost ring itself.

    The renderer's draw order and its geometry budget belong to
    `reactor_orb_test.py`, which checks them once for both surfaces. What is
    checked HERE is the overlay's own half: that this view tells the shared
    renderer where its edges are, and that the ground the colours are lit
    against is still opaque enough to be one.
    """
    view = android / "app/src/main/kotlin/ai/jarvis/app/ui/SiriOrbView.kt"
    reactor = android / "app/src/main/kotlin/ai/jarvis/app/ui/ReactorOrb.kt"
    for path in (view, reactor):
        if not path.is_file():
            print(f"FAIL  {path} is missing")
            return 1
    src = view.read_text(encoding="utf-8")
    failures = 0

    if "f.maxRadius = span" not in src:
        print(
            "FAIL  the halo is no longer clamped to the view. At full microphone "
            "level it exceeds the bounds and the parent's clip turns it into a box."
        )
        failures += 1
    if "span / (ReactorOrb.OUTER_FACTOR * MAX_SCALE)" not in src:
        print(
            "FAIL  the floating orb no longer sizes itself against the renderer's "
            "own outermost radius, so a retuned ring can be clipped into a box"
        )
        failures += 1

    rsrc = reactor.read_text(encoding="utf-8")
    if "const val SUBSTRATE_ALPHA = 0.90f" not in rsrc:
        print("FAIL  the substrate is no longer nearly opaque at the middle")
        failures += 1
    return failures


def main() -> int:
    android = Path(__file__).resolve().parents[1]
    failures = (
        check_palette(android)
        + check_the_orb_is_solid_and_fits(android)
        + check_every_mode_has_a_tone(android)
        + check_window_flags(android)
        + check_the_fallback_survives(android)
        + check_the_microphone_comes_back(android)
        + check_the_clock_runs(android)
    )
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(
        f"siri overlay: {len(TONES)} tones, the window flags, the keyguard and "
        "permission fallbacks, the microphone hand-back and the frame clock all agree"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
