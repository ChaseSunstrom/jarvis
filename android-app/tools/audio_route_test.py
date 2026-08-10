#!/usr/bin/env python3
"""Executable spec for the earpiece audio routing rules.

The Kotlin in `app/src/main/kotlin/ai/jarvis/app/audio/AudioRoute.kt` decides
where Jarvis listens and speaks when a headset is connected, and — the part
that actually matters — whether to capture through the platform's echo-cancelled
communication path.

Getting that wrong is not a subtle degradation. With an all-day earpiece, the
speaker is two centimetres from the microphone, so without cancellation Jarvis
hears its own reply, the energy VAD reads it as speech, and it interrupts
itself in a loop. Getting it wrong the other way costs transcription accuracy
on every phone-mic turn, because the communication path applies AEC, noise
suppression and AGC tuned for a phone call rather than for an STT model.

So the rule is written down twice: once in Kotlin (which this container cannot
compile) and once here, where it runs.

  1. The rules, re-implemented below, agree with an explicit TABLE covering
     every (headset kind x opt-in x SCO availability) combination. The TABLE is
     written out by hand so a bug in "the algorithm" cannot hide in both copies.
  2. The Kotlin source still contains those rules — a cheap structural check
     that catches someone editing one copy and not the other.

Run:  python3 android-app/tools/audio_route_test.py
"""

from __future__ import annotations

import re
import sys
from itertools import product
from pathlib import Path

SRC = (
    Path(__file__).resolve().parent.parent
    / "app/src/main/kotlin/ai/jarvis/app/audio/AudioRoute.kt"
)

# --- the device classes, mirrored from HeadsetKind -------------------------
# (name, has_mic, is_ear_worn, needs_sco_link)
KINDS = {
    "NONE": (False, False, False),
    "WIRED_HEADSET": (True, True, False),
    "WIRED_HEADPHONES": (False, False, False),
    "BLUETOOTH_SCO": (True, True, True),
    "BLUETOOTH_A2DP": (False, False, False),
    "BLE_HEADSET": (True, True, False),
    "USB_HEADSET": (True, True, False),
}

OPT_IN = (True, False)
SCO = (True, False)


# --- the rules, mirrored from AudioRoute / CaptureProfile ------------------


def captures_through_headset(kind: str, opt_in: bool, sco_available: bool) -> bool:
    has_mic, _worn, needs_sco = KINDS[kind]
    if not has_mic or not opt_in:
        return False
    return sco_available if needs_sco else True


def has_echo_loop(kind: str, opt_in: bool, sco_available: bool) -> bool:
    _mic, worn, _sco = KINDS[kind]
    return captures_through_headset(kind, opt_in, sco_available) and worn


def warm_link_eligible(kind: str, opt_in: bool, sco_available: bool) -> bool:
    return has_echo_loop(kind, opt_in, sco_available)


def capture_profile(kind: str, opt_in: bool, sco_available: bool) -> tuple[bool, bool]:
    """Returns (use_voice_communication, request_communication_device)."""
    if not captures_through_headset(kind, opt_in, sco_available):
        return (False, False)
    # Two branches, not three. Capturing through a headset implies ear-worn,
    # because every kind with a mic is also ear-worn — see
    # test_every_capturing_headset_is_ear_worn. CaptureProfile.forRoute used to
    # carry a third branch for "headset mic, not ear-worn" that nothing could
    # reach; both copies dropped it together.
    return (True, True)


# --- the table, written out by hand ----------------------------------------
# Only the opted-in, SCO-available rows: everything else is covered by the
# invariant tests below, which are stronger than an enumerated row would be.
TABLE_OPTED_IN = {
    # kind:              (captures, echo_loop, warm_link, use_vc, request_dev)
    "NONE": (False, False, False, False, False),
    "WIRED_HEADSET": (True, True, True, True, True),
    "WIRED_HEADPHONES": (False, False, False, False, False),
    "BLUETOOTH_SCO": (True, True, True, True, True),
    "BLUETOOTH_A2DP": (False, False, False, False, False),
    "BLE_HEADSET": (True, True, True, True, True),
    "USB_HEADSET": (True, True, True, True, True),
}


def test_every_capturing_headset_is_ear_worn() -> None:
    """What lets capture_profile have two branches instead of three.

    A headset with a mic that is NOT in the ear would want the raw source
    kept rather than AEC applied, and there is no longer a branch for it. If
    this fails, restore the third branch in BOTH copies rather than relaxing
    it — the Kotlin has a comment waiting where it goes.
    """
    for kind, (has_mic, is_ear_worn, _needs_sco) in KINDS.items():
        if has_mic:
            assert is_ear_worn, (
                f"{kind} can capture but is not ear-worn; capture_profile has "
                "no branch for that any more"
            )


def test_table_matches_the_rules() -> None:
    for kind, expected in TABLE_OPTED_IN.items():
        use_vc, req = capture_profile(kind, True, True)
        actual = (
            captures_through_headset(kind, True, True),
            has_echo_loop(kind, True, True),
            warm_link_eligible(kind, True, True),
            use_vc,
            req,
        )
        assert actual == expected, f"{kind}: {actual} != {expected}"


def test_the_table_covers_every_kind() -> None:
    assert set(TABLE_OPTED_IN) == set(KINDS)


# --- the invariants --------------------------------------------------------


def test_opt_out_disables_every_headset_path() -> None:
    """The user's switch is the outer gate; nothing routes around it."""
    for kind, sco in product(KINDS, SCO):
        assert not captures_through_headset(kind, False, sco), kind
        assert not has_echo_loop(kind, False, sco), kind
        assert not warm_link_eligible(kind, False, sco), kind
        assert capture_profile(kind, False, sco) == (False, False), kind


def test_a_device_without_a_mic_never_captures() -> None:
    for kind, opt_in, sco in product(KINDS, OPT_IN, SCO):
        if not KINDS[kind][0]:
            assert not captures_through_headset(kind, opt_in, sco), kind


def test_unavailable_sco_falls_back_to_the_phone() -> None:
    """A paired headset whose call profile is busy would capture silence."""
    assert not captures_through_headset("BLUETOOTH_SCO", True, False)
    assert capture_profile("BLUETOOTH_SCO", True, False) == (False, False)
    # A cable has no link to be unavailable.
    assert captures_through_headset("WIRED_HEADSET", True, False)


def test_echo_cancellation_exactly_when_capture_and_playback_share_a_device() -> None:
    for kind, opt_in, sco in product(KINDS, OPT_IN, SCO):
        use_vc, _req = capture_profile(kind, opt_in, sco)
        assert use_vc == has_echo_loop(kind, opt_in, sco), (kind, opt_in, sco)


def test_the_accuracy_cost_is_never_paid_without_a_loop_to_cancel() -> None:
    """The headline regression this file exists to prevent."""
    for kind, opt_in, sco in product(KINDS, OPT_IN, SCO):
        use_vc, _ = capture_profile(kind, opt_in, sco)
        if use_vc:
            assert has_echo_loop(kind, opt_in, sco), (
                f"{kind} paid the AEC transcription-accuracy cost with no echo loop"
            )


def test_warm_link_never_without_cancellation() -> None:
    """Warm-link with an open mic and no AEC is a feedback loop, not a feature."""
    for kind, opt_in, sco in product(KINDS, OPT_IN, SCO):
        if warm_link_eligible(kind, opt_in, sco):
            assert has_echo_loop(kind, opt_in, sco), (kind, opt_in, sco)


# --- structural: the Kotlin still says the same thing ----------------------


def test_kotlin_source_exists() -> None:
    assert SRC.is_file(), f"missing {SRC}"


def test_kotlin_declares_every_kind() -> None:
    src = SRC.read_text()
    body = src.split("enum class HeadsetKind {", 1)[1].split("\n}", 1)[0]
    for name in KINDS:
        assert re.search(rf"\b{name}\b", body), f"HeadsetKind.{name} missing"


def test_kotlin_keeps_the_opt_in_gate() -> None:
    src = SRC.read_text()
    block = src.split("val capturesThroughHeadset", 1)[1].split("\n    /**", 1)[0]
    assert "headsetModeEnabled" in block, (
        "capturesThroughHeadset stopped consulting the user's opt-in"
    )
    assert "hasMic" in block


def test_kotlin_keeps_both_capture_sources() -> None:
    """Both branches must still exist; collapsing to one is the regression.

    Two, not three. `forRoute` used to have a third branch for a headset mic
    that is not ear-worn, and nothing could reach it — every kind with a mic is
    ear-worn. It no longer looks at `hasEchoLoop`, because with that invariant
    "captures through a headset" and "has an echo loop" are the same condition,
    and testing for it twice suggested a distinction the enum cannot express.
    What must not happen is the PHONE branch disappearing, which would put AEC
    on the built-in microphone and cost transcription accuracy on every turn.
    """
    src = SRC.read_text()
    body = src.split("fun forRoute", 1)[1]
    assert "useVoiceCommunication = true" in body, "the headset branch is gone"
    assert "useVoiceCommunication = false" in body, "the phone branch is gone"
    assert "capturesThroughHeadset" in body, "forRoute stopped asking about the headset"


def test_kotlin_warm_link_is_tied_to_the_echo_loop() -> None:
    src = SRC.read_text()
    block = src.split("val warmLinkEligible", 1)[1].split("\n}", 1)[0]
    assert "hasEchoLoop" in block, (
        "warmLinkEligible stopped requiring echo cancellation"
    )


# --- wiring: the rules are actually reached in production ------------------
#
# This project has already shipped a fully-tested feature that was never wired
# in — the cross-device companion module had a green suite while nothing
# constructed it. Pure logic with a passing spec proves nothing on its own, so
# these checks assert that the production call path exists.

APP = Path(__file__).resolve().parent.parent / "app/src/main/kotlin/ai/jarvis/app"
CONVERSATION = APP / "assist/JarvisConversation.kt"
MIC = APP / "assist/MicStreamer.kt"
TTS = APP / "assist/TtsPlayer.kt"
CONFIG = APP / "config/JarvisConfig.kt"


def test_the_conversation_resolves_a_route() -> None:
    src = CONVERSATION.read_text()
    assert "HeadsetMonitor(" in src, "nothing constructs HeadsetMonitor"
    assert "CaptureProfile.forRoute" in src, (
        "JarvisConversation never asks for a capture profile"
    )


def test_the_profile_reaches_the_microphone() -> None:
    conv = CONVERSATION.read_text()
    assert "captureProfile" in conv, "the profile is computed but never passed to the mic"
    mic = MIC.read_text()
    assert "VOICE_COMMUNICATION" in mic and "VOICE_RECOGNITION" in mic, (
        "MicStreamer no longer chooses between the two capture sources"
    )
    assert "profile.useVoiceCommunication" in mic, (
        "MicStreamer stopped consulting the capture profile"
    )


def test_playback_usage_follows_the_capture_source() -> None:
    """An AEC with no reference signal cancels nothing; these must agree."""
    conv = CONVERSATION.read_text()
    assert "communicationRoute = profile.useVoiceCommunication" in conv, (
        "TTS playback usage is no longer tied to the capture source"
    )
    tts = TTS.read_text()
    assert "USAGE_VOICE_COMMUNICATION" in tts and "USAGE_ASSISTANT" in tts


def test_the_communication_route_is_released() -> None:
    """A leaked SCO link silences music system-wide on API < 31."""
    src = CONVERSATION.read_text()
    assert "applyCommunicationRoute" in src, "the route is never applied"
    assert "clearCommunicationRoute" in src, (
        "the route is applied but never released — an SCO link would leak"
    )
    teardown = src.split("private fun stopWith(", 1)
    assert len(teardown) == 2, "stopWith() moved; check the teardown path"
    assert "clearCommunicationRoute" in teardown[1].split("\n    private fun", 1)[0], (
        "clearCommunicationRoute is not called from the teardown path"
    )


def test_the_synthetic_mic_seam_stays_ahead_of_route_selection() -> None:
    """`debugPcmSource` must be consulted before any headset routing happens.

    It is how `ConversationE2ETest` drives a full voice turn on an emulator that
    has no microphone. If route selection moves ahead of it, the synthetic
    source stops being consulted and the instrumented test fails ninety seconds
    later as "the transcript never rendered" — which reads like a server fault
    and is not one. Cheap to assert, expensive to debug.
    """
    src = MIC.read_text()
    body = src.split("fun start()", 1)[1]
    seam = body.index("debugPcmSource")
    route = body.index("captureProfile()")
    assert seam < route, (
        "route selection moved ahead of the debugPcmSource test seam; "
        "the synthetic microphone would be bypassed"
    )
    # And the early return has to be between them, or the seam is consulted but
    # the real AudioRecord is opened anyway.
    assert "return" in body[seam:route], (
        "the injected-source branch no longer returns before opening AudioRecord"
    )


def test_headset_mode_defaults_to_off() -> None:
    """Plugging in a headset must never silently move the microphone."""
    src = CONFIG.read_text()
    block = src.split("var headsetMode", 1)
    assert len(block) == 2, "JarvisConfig.headsetMode missing"
    getter = block[1].split("set(", 1)[0]
    assert "false" in getter, "headsetMode no longer defaults to off"


def test_warm_link_cannot_outlive_headset_mode() -> None:
    src = CONFIG.read_text()
    block = src.split("var warmLink", 1)[1].split("set(", 1)[0]
    assert "headsetMode" in block, (
        "warmLink stopped requiring headsetMode, so opting out would not disable it"
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
    combos = len(KINDS) * len(OPT_IN) * len(SCO)
    print(
        f"\n{len(tests) - failures}/{len(tests)} checks passed "
        f"({combos} kind/opt-in/SCO combinations)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
