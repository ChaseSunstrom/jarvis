#!/usr/bin/env python3
"""Executable spec: who has the audio, and what Jarvis does about it.

Reported as *"it stops working after a phone call"*, and it was two defects
sharing one symptom. Grepping the whole Kotlin tree for `requestAudioFocus`,
`AudioFocusRequest`, `TelephonyManager` or any call-state listener returned
**nothing at all**. Not "the wrong thing"; nothing.

## 1. A conversation held no audio focus

So it talked over whatever the user was playing — an assistant that never asked
the music to stop — and, worse, a call arriving mid-turn was something the app
could not be told about. A process holding no focus gets no `AUDIOFOCUS_LOSS`.
The turn went on holding a microphone it had effectively lost, the orb kept
saying LISTENING, and the user got a surface that appeared to be listening and
heard nothing.

## 2. The always-on listener discovered a call by failing

`AudioRecord` could not be opened, `onMicUnavailable` fired, and the recovery
was blind exponential backoff — base 2 s, doubling, capped at a MINUTE — with a
fifteen-minute inexact repeating alarm behind it. Nothing watched for the call
*ending*, which is the one event that makes retrying worth doing. Hang up and
the phone stayed deaf for as long as the backoff had reached, or until the
quarter-hourly alarm came round.

Both halves are edges that the platform will report for free, and neither was
subscribed to.

## Why the audio mode and not TelephonyManager

`TelephonyCallback.CallStateListener` needs `READ_PHONE_STATE` from API 31 — a
dangerous permission, which `runtime_permissions_test.py` would then require the
app to actually request — and it does not see a WhatsApp or Signal call at all,
which holds the microphone exactly as hard. `AudioManager.mode` sees both, needs
nothing, and is already the probe `PresenceReporter` uses to decide whether this
phone can make a noise the user would hear.

`READ_PHONE_STATE` was in fact already declared AND requested, with the reason
*"Lets Jarvis notice you are on a call and stop talking over it"* — for a feature
that did not exist. A dangerous permission the user is asked to grant for a job
nothing does is the same defect wearing a permission dialog, so it is gone: the
audio mode does that job, better, for nothing. This file keeps it gone.

The honest limit, which this file also pins: `addOnModeChangedListener` arrived
in API 31. Below that there is no callback and `CallGuard.edgeDriven` says so
rather than implying every phone gets the fast path.

Run:  python3 android-app/tools/audio_attention_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
KOTLIN = ANDROID / "app/src/main/kotlin/ai/jarvis/app"

ATTENTION = KOTLIN / "audio/AudioAttention.kt"
CONVO = KOTLIN / "assist/JarvisConversation.kt"
SERVICE = KOTLIN / "assist/WakeWordService.kt"


def code(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


def all_kotlin() -> list[Path]:
    return sorted(KOTLIN.rglob("*.kt"))


# ---------------------------------------------------------------------------
# 1. audio focus exists at all
# ---------------------------------------------------------------------------


def test_something_in_this_app_requests_audio_focus() -> None:
    """The absence this file was written for."""
    found = [p for p in all_kotlin() if "requestAudioFocus(" in code(p)]
    assert found, (
        "no code anywhere in the app requests audio focus, so Jarvis speaks "
        "over the user's music and is never told when a call takes the audio "
        "away mid-turn"
    )


def test_focus_is_taken_by_a_turn_and_never_by_the_listener() -> None:
    """The distinction that makes this usable rather than infuriating.

    A wake-word listener that held focus would pause the user's music for the
    hours it sits idle waiting to hear its name. Detection asks for nothing; a
    conversation asks for `GAIN_TRANSIENT_EXCLUSIVE`, which is the documented
    request for speech recognition, and gives it straight back.
    """
    convo = code(CONVO)
    assert "TurnFocus(" in convo, (
        "the conversation engine does not take audio focus, so a call arriving "
        "mid-turn is not reported to it"
    )
    assert "focus.take()" in convo and "focus.release()" in convo, (
        "TurnFocus is constructed and never taken, or taken and never released "
        "— focus this process does not abandon is music the user has to restart "
        "by hand"
    )
    service = code(SERVICE)
    assert "TurnFocus(" not in service, (
        "the always-on listener takes audio focus, which pauses the user's "
        "music for every hour it spends waiting to hear its name"
    )
    attention = code(ATTENTION)
    assert "AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE" in attention, (
        "a conversation asks for something other than the exclusive transient "
        "gain, which is the documented request for speech recognition"
    )
    assert "USAGE_ASSISTANT" in attention, (
        "the focus request does not describe itself as an assistant, so a car "
        "head unit and a headset route it as media"
    )


def test_losing_focus_ends_the_turn() -> None:
    """Registering the listener is only half of it; acting on it is the point."""
    attention = code(ATTENTION)
    for constant in (
        "AUDIOFOCUS_LOSS",
        "AUDIOFOCUS_LOSS_TRANSIENT",
        "AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK",
    ):
        assert constant in attention, f"focus loss does not handle {constant}"
    convo = code(CONVO)
    focus = re.search(r"private val focus = TurnFocus\(context\) \{.*?\n    \}", convo, re.S)
    assert focus, "the conversation's focus-loss handler is gone"
    assert "stopWith(" in focus.group(0), (
        "losing the audio does not end the turn, so the orb goes on saying it "
        "is listening to a microphone something else has taken"
    )


# ---------------------------------------------------------------------------
# 2. call-state awareness
# ---------------------------------------------------------------------------


def test_a_call_is_noticed_without_a_dangerous_permission() -> None:
    attention = code(ATTENTION)
    assert "MODE_IN_CALL" in attention and "MODE_IN_COMMUNICATION" in attention, (
        "CallGuard does not read the audio mode, which is the only signal that "
        "sees a VoIP call as well as a telephony one"
    )
    assert "MODE_RINGTONE" in attention, (
        "a ringing phone is not treated as a call, so the listener keeps the "
        "recorder open until the user answers — and loses the race for it"
    )
    manifest = (ANDROID / "app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    assert "READ_PHONE_STATE" not in manifest, (
        "call-state awareness was bought with a dangerous permission; the audio "
        "mode gives the same answer for free and sees more calls"
    )
    for path in all_kotlin():
        assert "TelephonyManager" not in code(path), (
            f"{path.name} uses TelephonyManager, which needs READ_PHONE_STATE on "
            "31+ and cannot see a VoIP call at all"
        )


def test_the_listener_pauses_for_a_call_and_comes_straight_back() -> None:
    """The reported symptom, from both ends."""
    src = code(SERVICE)
    assert "CallGuard(" in src, "the wake listener has no call awareness"
    assert "calls?.start()" in src and "calls?.stop()" in src, (
        "the call guard is never started, or its listener is never released"
    )
    handler = re.search(r"private fun onCallChanged\(.*?\n    \}", src, re.S)
    assert handler, "nothing handles the call edge"
    body = handler.group(0)
    assert "pause(" in body, "a call starting does not release the microphone"
    assert "resume()" in body, "a call ending does not take the microphone back"
    assert "micFailures = 0" in body and "removeCallbacks(reconnect)" in body, (
        "the call ending does not cancel the backoff, so the phone waits out a "
        "delay that was measured against a conflict which is already over — "
        "which is the whole of 'it stops working after a phone call'"
    )


def test_a_failed_open_asks_why_instead_of_guessing() -> None:
    """`onMicUnavailable` used to have exactly one theory and one remedy."""
    src = code(SERVICE)
    handler = re.search(r"private fun onMicUnavailable\(.*?\n    \}", src, re.S)
    assert handler, "WakeWordService has no onMicUnavailable"
    body = handler.group(0)
    assert "calls?.publish()" in body, (
        "a microphone that could not be opened does not re-read the audio mode, "
        "so a call is still discovered only as a random failure and recovery is "
        "still blind"
    )
    assert "calls?.inCall" in body, (
        "the failure path does not distinguish a call from any other conflict"
    )


def test_a_microphone_conflict_backs_off_faster_than_a_dead_server() -> None:
    """Two different failures wanted two different curves and shared one.

    An unreachable server may be off all night; a recorder conflict is a voice
    note, an alarm, another assistant's one-shot. A minute of silence after a
    two-second conflict is most of what "the wake word stopped working" was.
    """
    src = code(SERVICE)
    mic_max = re.search(r"const val MIC_BACKOFF_MAX_MS = ([\d_]+)L", src)
    net_max = re.search(r"const val BACKOFF_MAX_MS = ([\d_]+)L", src)
    assert mic_max and net_max, "the two backoff ceilings are not both declared"
    mic_ms = int(mic_max.group(1).replace("_", ""))
    net_ms = int(net_max.group(1).replace("_", ""))
    assert mic_ms < net_ms, (
        f"a microphone conflict waits as long as an unreachable server "
        f"({mic_ms}ms vs {net_ms}ms)"
    )
    assert "micBackoff(" in src, "the microphone path uses the socket's curve again"
    heartbeat = re.search(r"const val HEARTBEAT_MS = ([^\n]+)", src)
    assert heartbeat, "the heartbeat constant is gone"
    # And recovery must not be the alarm's job. The alarm exists for a killed
    # process; a released microphone is an edge.
    recheck = re.search(r"const val CALL_RECHECK_MS = ([\d_]+)L", src)
    assert recheck, "there is no re-check for the devices that have no mode callback"
    assert int(recheck.group(1).replace("_", "")) <= 5 * 60 * 1000, (
        "the fallback re-check is longer than five minutes, which is the "
        "fifteen-minute alarm by another name"
    )


def test_the_pre_31_limit_is_stated_rather_than_implied() -> None:
    """`addOnModeChangedListener` is API 31. Below that there is no callback and
    the code has to say so instead of pretending."""
    attention = code(ATTENTION)
    assert "edgeDriven" in attention, (
        "CallGuard does not report whether it can push the edge or only answer "
        "when asked, so a caller cannot know which device it is on"
    )
    assert "Build.VERSION_CODES.S" in attention, (
        "the API-31 boundary is not checked, so a device below it crashes on a "
        "method that is not there"
    )
    service = code(SERVICE)
    assert "calls?.inCall" in code(SERVICE), (
        "nothing polls the call state, so a device with no mode callback never "
        "learns about a call at all"
    )
    assert "CALL_RECHECK_MS" in service


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
          f"({len(all_kotlin())} Kotlin files scanned)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
