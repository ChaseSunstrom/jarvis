#!/usr/bin/env python3
"""Executable spec for the headset button gate.

The Kotlin in `app/src/main/kotlin/ai/jarvis/app/audio/MediaButtonGate.kt`
decides what the single physical button on an earpiece does. Most of that is
ergonomics — do not steal play/pause, debounce a bouncy Bluetooth key — but one
rule is a security boundary:

    A headset button may never answer a Tier-3 consent prompt.

Approving a payment, a message to another person or a shell command must cost a
deliberate look at a screen and a tap on it. A Bluetooth media key arrives with
no indication of who pressed it, from a small object that may be on a desk, in
a bag, or in someone else's hand — and it can be pressed through a coat pocket.
So while a prompt is waiting, every press is swallowed: not forwarded to the
assistant, not forwarded to the media player (a media app taking focus can pull
the prompt out from under the user mid-decision), and not counted for debounce.

This file re-implements the gate and checks it exhaustively, because "no input
combination reaches consent" is a claim about the whole input space rather than
about a handful of examples.

Run:  python3 android-app/tools/media_button_test.py
"""

from __future__ import annotations

import re
import sys
from itertools import product
from pathlib import Path

KOTLIN = Path(__file__).resolve().parent.parent / "app/src/main/kotlin/ai/jarvis/app"
SRC = KOTLIN / "audio/MediaButtonGate.kt"
SESSION = KOTLIN / "audio/HeadsetButtonSession.kt"
SERVICE = KOTLIN / "automation/JarvisAutomationService.kt"
SETTINGS = KOTLIN / "SettingsActivity.kt"

IGNORE = "IGNORE"
PASS_TO_MEDIA = "PASS_TO_MEDIA"
START_TURN = "START_TURN"
END_TURN = "END_TURN"
ACTIONS = {IGNORE, PASS_TO_MEDIA, START_TURN, END_TURN}

DEBOUNCE_MS = 350
LONG_PRESS_MS = 600

# The input space, chosen to straddle every boundary in the rules.
HELD = (0, 50, LONG_PRESS_MS - 1, LONG_PRESS_MS, 5_000)
SINCE = (0, DEBOUNCE_MS - 1, DEBOUNCE_MS, 10_000, 1 << 62)
BOOLS = (True, False)


# --- the rules, mirrored from MediaButtonGate.decide -----------------------


def decide(
    headset_mode: bool,
    consent_pending: bool,
    in_conversation: bool,
    music_active: bool,
    held_ms: int,
    since_ms: int,
) -> str:
    if consent_pending:
        return IGNORE
    if not headset_mode:
        return PASS_TO_MEDIA
    if since_ms < DEBOUNCE_MS:
        return IGNORE
    long_press = held_ms >= LONG_PRESS_MS
    if in_conversation:
        return END_TURN
    if music_active and not long_press:
        return PASS_TO_MEDIA
    return START_TURN


def resets_debounce(action: str) -> bool:
    return action in (START_TURN, END_TURN)


def every_input():
    return product(BOOLS, BOOLS, BOOLS, BOOLS, HELD, SINCE)


# --- rule 1: the security boundary -----------------------------------------


def test_no_input_reaches_consent() -> None:
    """Exhaustive: with a prompt pending, every press is swallowed.

    `consent_pending` is pinned true and the other five axes are swept, so the
    count below is the size of the rest of the input space — not of
    `every_input()`, which also sweeps `consent_pending` itself.
    """
    checked = 0
    for hm, ic, ma, held, since in product(BOOLS, BOOLS, BOOLS, HELD, SINCE):
        action = decide(hm, True, ic, ma, held, since)
        assert action == IGNORE, (
            f"headset button reached a pending consent prompt: "
            f"headset_mode={hm} in_conversation={ic} music={ma} "
            f"held={held} since={since} -> {action}"
        )
        checked += 1
    assert checked == 2 * 2 * 2 * len(HELD) * len(SINCE), checked


def test_a_fresh_press_during_a_prompt_is_still_swallowed() -> None:
    """Ordering matters: the consent check must precede the debounce check."""
    assert decide(True, True, False, False, 50, 1 << 62) == IGNORE


def test_there_is_no_approving_action() -> None:
    assert ACTIONS == {IGNORE, PASS_TO_MEDIA, START_TURN, END_TURN}
    for a in ACTIONS:
        assert not re.search(r"approv|confirm|accept|yes", a, re.I), a


# --- rule 4: opt-out --------------------------------------------------------


def test_opt_out_hands_the_button_back() -> None:
    for _hm, ic, ma, held, since in product(BOOLS, BOOLS, BOOLS, HELD, SINCE):
        assert decide(False, False, ic, ma, held, since) == PASS_TO_MEDIA


# --- rule 3: debounce -------------------------------------------------------


def test_bounced_presses_are_dropped() -> None:
    for since in (0, DEBOUNCE_MS - 1):
        assert decide(True, False, False, False, 50, since) == IGNORE


def test_a_deliberate_second_press_is_accepted() -> None:
    assert decide(True, False, False, False, 50, DEBOUNCE_MS) == START_TURN


def test_only_our_own_presses_reset_the_clock() -> None:
    """Double-tap-to-skip must keep working in the user's music player."""
    assert not resets_debounce(PASS_TO_MEDIA)
    assert not resets_debounce(IGNORE)
    assert resets_debounce(START_TURN)
    assert resets_debounce(END_TURN)


# --- rule 2: do not steal play/pause ---------------------------------------


def test_a_tap_during_music_pauses_music() -> None:
    assert decide(True, False, False, True, 50, 10_000) == PASS_TO_MEDIA


def test_a_long_press_summons_jarvis_over_music() -> None:
    assert decide(True, False, False, True, LONG_PRESS_MS, 10_000) == START_TURN


def test_the_long_press_boundary_is_inclusive() -> None:
    assert decide(True, False, False, True, LONG_PRESS_MS - 1, 10_000) == PASS_TO_MEDIA
    assert decide(True, False, False, True, LONG_PRESS_MS, 10_000) == START_TURN


# --- mid-conversation -------------------------------------------------------


def test_mid_conversation_the_button_only_ever_ends_the_turn() -> None:
    for ma, held, since in product(BOOLS, HELD, SINCE):
        if since < DEBOUNCE_MS:
            continue  # a bounce is a bounce in any state
        assert decide(True, False, True, ma, held, since) == END_TURN


def test_a_press_mid_conversation_never_starts_a_second_turn() -> None:
    for ma, held, since in product(BOOLS, HELD, SINCE):
        assert decide(True, False, True, ma, held, since) != START_TURN


# --- structural: the Kotlin still says the same thing ----------------------


def test_kotlin_source_exists() -> None:
    assert SRC.is_file(), f"missing {SRC}"


def test_kotlin_actions_match() -> None:
    src = SRC.read_text()
    body = src.split("enum class Action {", 1)[1].split("\n    }", 1)[0]
    found = set(re.findall(r"^\s*([A-Z_]+),?\s*$", body, re.M))
    assert found == ACTIONS, f"{found} != {ACTIONS}"


def test_kotlin_checks_consent_first() -> None:
    """The ordering IS the invariant, so assert on the ordering."""
    src = SRC.read_text()
    body = src.split("        if (consentPending)", 1)
    assert len(body) == 2, "the consentPending guard moved or changed shape"
    before = body[0].split("fun decide(", 1)[1]
    # Nothing between the signature and the consent check may return.
    assert "return" not in before, (
        "something returns before the consent check in decide()"
    )


def test_kotlin_constants_match() -> None:
    src = SRC.read_text()
    for name, value in (("DEBOUNCE_MS", DEBOUNCE_MS), ("LONG_PRESS_MS", LONG_PRESS_MS)):
        m = re.search(rf"const val {name} = (\d+)L", src)
        assert m, f"{name} missing"
        assert int(m.group(1)) == value, f"{name} is {m.group(1)}, spec says {value}"


# --- the gate has a caller --------------------------------------------------
#
# Everything above this line tested a pure function that nothing called.
#
# There was no MediaSession anywhere in the app, so no media button event ever
# reached this process, so all 400 combinations above — including the security
# rule that a press may never answer a consent prompt — described a feature that
# did not exist. `docs/earpiece.md` documented it as shipped. An exhaustively
# tested pure function is exactly the shape that hides this: the tests pass,
# the logic is right, and the feature is not there.


def test_something_actually_receives_a_media_button() -> None:
    assert SESSION.is_file(), (
        "there is no MediaSession in the app, so no media button ever reaches "
        "MediaButtonGate and every test above describes an unreachable feature"
    )
    src = SESSION.read_text()
    assert "MediaSession(" in src, "HeadsetButtonSession creates no session"
    assert "onMediaButtonEvent" in src, "nothing handles the button event"
    assert "MediaButtonGate.decide(" in src, "the session does not consult the gate"


def test_the_session_is_owned_by_something_long_lived() -> None:
    """A session that exists only while an Activity is up would never be the
    session the framework routes a button to."""
    src = SERVICE.read_text()
    assert "HeadsetButtonSession(applicationContext)" in src, (
        "nothing constructs the session, so it is a class with no instances"
    )
    assert "headsetButton?.stop()" in src, "the session is never released"


def test_every_outcome_is_carried_out() -> None:
    src = SESSION.read_text()
    body = src.split("MediaButtonGate.decide(", 1)[1]
    for action, evidence, why in (
        ("IGNORE", "-> true", "swallowed presses must be consumed, not forwarded"),
        ("PASS_TO_MEDIA", "super.onMediaButtonEvent(intent)",
         "a press that is not ours must go to the default handler — the framework "
         "discards this method's return value, so `false` swallowed it"),
        ("START_TURN", "startTurn()", "nothing opens the assist surface"),
        ("END_TURN", "endTurnFromButton()", "nothing ends the turn"),
    ):
        arm = body.split(f"MediaButtonGate.Action.{action}", 1)
        assert len(arm) == 2, f"the {action} outcome is not handled at all"
        assert evidence in arm[1][:400], f"{action}: {why}"


def test_the_debounce_clock_is_kept() -> None:
    src = SESSION.read_text()
    assert "MediaButtonGate.resetsDebounce(action)" in src, (
        "the caller decides for itself which presses reset the clock, so the "
        "rule that a press handed to a media app does not reset it can drift"
    )


def test_the_decision_is_made_on_release() -> None:
    """Hold duration is the whole difference between summoning Jarvis and
    pausing a podcast, and it is not known until the key comes up."""
    src = SESSION.read_text()
    assert "KeyEvent.ACTION_UP" in src
    assert "event.eventTime - event.downTime" in src, (
        "the hold duration is not measured from the key event, so every press "
        "looks like the same length"
    )


def test_the_consent_check_has_something_to_read() -> None:
    """Rule 1 needs a live answer to "is a prompt up?". The bridge tracked
    prompts per request id and had no way to answer that question at all."""
    src = SESSION.read_text()
    assert "ApprovalBridge.anyPending" in src, (
        "consentPending is passed something other than the live prompt state"
    )
    bridge = (KOTLIN / "ui/ApprovalBridge.kt").read_text()
    assert "val anyPending" in bridge, "ApprovalBridge cannot answer it"


def test_the_user_can_turn_it_on() -> None:
    """headsetMode, headsetButton and warmLink had getters, defaults and a page
    of documentation, and nothing in the app ever wrote one of them."""
    src = SETTINGS.read_text()
    for setting in ("config.headsetMode =", "config.headsetButton =", "config.warmLink ="):
        assert setting in src, (
            f"nothing writes {setting.split()[0]}, so the earpiece feature is "
            "reachable only by editing SharedPreferences by hand"
        )


def test_warm_link_is_read_by_the_conversation() -> None:
    """A setting with a writer and no reader is the same bug wearing a switch."""
    src = (KOTLIN / "assist/JarvisConversation.kt").read_text()
    assert "config.warmLink && route.warmLinkEligible" in src, (
        "warmLink is stored and never consulted, or is consulted without the "
        "route's veto — an open mic with no echo canceller hears the tail of "
        "Jarvis's own reply and starts a turn against itself"
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
    combos = 2 * 2 * 2 * 2 * len(HELD) * len(SINCE)
    print(
        f"\n{len(tests) - failures}/{len(tests)} checks passed "
        f"({combos} button-press input combinations)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
