#!/usr/bin/env python3
"""Executable spec for "why does Jarvis stop listening when I close the app?".

The reported symptom: *"I have to select start listening, in the app, for it to
work while the app is closed, even though with the mic always on/running in
background it should work"*.

The cause is a platform rule with no visible failure mode. A foreground service
whose type is `microphone` is a **while-in-use** service, and Android will not
let one be started while the app is in the background. `BOOT_COMPLETED` is an
exemption from the *general* background-start restriction and explicitly **not**
an exemption for the while-in-use types. So the boot receiver called
`startForegroundService`, the platform refused, the old code logged a warning
onto a phone nobody has a cable for, and always-on listening was simply not
running until the app was next opened — which is exactly what was reported.

`WakeStartPolicy` decides *in advance* whether a start can succeed, so a refusal
becomes a notification the user can tap instead of silence. This mirrors that
decision and checks the wiring around it:

  1. the routing table, written out by hand so a bug in "the algorithm" cannot
     hide in both copies;
  2. that every caller which claims `fromForeground` really is a resumed
     Activity — the one claim that, if wrong, makes the policy a lie and the
     start throw;
  3. that the refusal paths lead somewhere: a trampoline Activity, a
     notification, a quick-settings tile and a heartbeat all exist and are
     declared in the manifest.

Run:  python3 android-app/tools/wake_start_policy_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# --- the rule, mirrored from WakeStartPolicy.kt ----------------------------

OFF = "OFF"
NEEDS_MIC = "NEEDS_MIC_PERMISSION"
DIRECT = "DIRECT"
NEEDS_TAP = "NEEDS_A_TAP"

FIRST_RESTRICTED_SDK = 31


def route(
    enabled: bool,
    has_mic: bool,
    from_foreground: bool,
    sdk_int: int,
    ignoring_battery_optimizations: bool,
    can_draw_overlays: bool,
) -> str:
    if not enabled:
        return OFF
    if not has_mic:
        return NEEDS_MIC
    if from_foreground:
        return DIRECT
    if sdk_int < FIRST_RESTRICTED_SDK:
        return DIRECT
    if ignoring_battery_optimizations or can_draw_overlays:
        return DIRECT
    return NEEDS_TAP


# enabled, mic, foreground, sdk, doze-exempt, overlay -> route, why
CASES: list[tuple[bool, bool, bool, int, bool, bool, str, str]] = [
    # The switch is the switch. Nothing else is even consulted.
    (False, True, True, 35, True, True, OFF, "the user did not ask for this"),
    (False, False, False, 35, False, False, OFF, "off is off, however broken the rest is"),
    # No microphone permission is never worth a foreground notification saying
    # "Jarvis is listening", because it would not be.
    (True, False, True, 35, True, True, NEEDS_MIC, "granting comes before starting"),
    (True, False, False, 30, True, True, NEEDS_MIC, "same on an older phone"),
    # A resumed Activity may always start a while-in-use foreground service.
    (True, True, True, 35, False, False, DIRECT, "the one route Android never refuses"),
    (True, True, True, 31, False, False, DIRECT, "and on the first restricted release"),
    # Before Android 12 there is no ForegroundServiceStartNotAllowedException.
    (True, True, False, 30, False, False, DIRECT, "no background-start refusal on 11"),
    (True, True, False, 29, False, False, DIRECT, "nor on the minimum this app supports"),
    # THE REGRESSION. Android 12+, from a boot receiver, with neither exemption:
    # this is the case that was silently failing on every restart.
    (True, True, False, 31, False, False, NEEDS_TAP, "the reboot that stopped listening"),
    (True, True, False, 35, False, False, NEEDS_TAP, "still true on the current target"),
    # Either documented exemption is enough on its own.
    (True, True, False, 35, True, False, DIRECT, "battery-optimisation exemption"),
    (True, True, False, 35, False, True, DIRECT, "display over other apps"),
    (True, True, False, 35, True, True, DIRECT, "both, which is the same answer"),
]


def check_rules() -> int:
    failures = 0
    for enabled, mic, fg, sdk, doze, overlay, expected, why in CASES:
        got = route(enabled, mic, fg, sdk, doze, overlay, )
        if got != expected:
            print(
                f"FAIL  enabled={enabled} mic={mic} fg={fg} sdk={sdk} doze={doze} "
                f"overlay={overlay}: expected {expected}, got {got} — {why}"
            )
            failures += 1
    return failures


def check_a_refusal_always_says_something() -> int:
    """Every non-startable route must have a sentence for the user.

    The bug this whole file is about was a refusal that produced no sentence.
    """
    kt = KOTLIN_POLICY
    failures = 0
    for name in (NEEDS_MIC, NEEDS_TAP):
        # `explain` must return a string for it, i.e. the branch must not be
        # grouped with the two that return null.
        branch = re.search(
            rf"Route\.{name}\s*->\s*\n?\s*\"", kt, re.MULTILINE
        )
        if not branch:
            print(f"FAIL  WakeStartPolicy.explain has no sentence for {name}")
            failures += 1
    if re.search(r"Route\.(OFF|DIRECT).*->\s*null", kt) is None:
        print("FAIL  WakeStartPolicy.explain no longer returns null for the quiet routes")
        failures += 1
    return failures


def check_kotlin_agrees(android: Path) -> int:
    """The Kotlin still contains the rule this file mirrors."""
    failures = 0
    kt = KOTLIN_POLICY
    if f"FIRST_RESTRICTED_SDK = {FIRST_RESTRICTED_SDK}" not in kt:
        print(f"FAIL  WakeStartPolicy no longer treats {FIRST_RESTRICTED_SDK} as the boundary")
        failures += 1
    for name in (OFF, NEEDS_MIC, DIRECT, NEEDS_TAP):
        if name not in kt:
            print(f"FAIL  WakeStartPolicy has no {name} route any more")
            failures += 1
    # The order of the guards IS the rule: a `fromForeground` check placed after
    # the exemption check would still pass the table above, but a check placed
    # before the permission check would start a service that cannot record.
    order = [
        kt.index("if (!enabled)"),
        kt.index("if (!hasMicPermission)"),
        kt.index("if (fromForeground)"),
        kt.index("if (sdkInt <"),
        kt.index("if (ignoringBatteryOptimizations"),
    ]
    if order != sorted(order):
        print("FAIL  WakeStartPolicy's guards are no longer in the order this spec assumes")
        failures += 1
    return failures


def check_only_activities_claim_foreground(android: Path) -> int:
    """`fromForeground = true` is a promise the platform will check.

    Claiming it from a receiver or a service does not make the start legal; it
    just moves the failure from "policy said no" to "the platform threw". So
    every caller passing it must be an Activity.
    """
    root = android / "app/src/main/kotlin"
    failures = 0
    claimants: list[Path] = []
    for path in sorted(root.rglob("*.kt")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"ensureRunning\([^)]*fromForeground\s*=\s*true", text, re.S):
            claimants.append(path)
    if not claimants:
        print("FAIL  nothing starts the listener from the foreground any more")
        return 1
    for path in claimants:
        text = path.read_text(encoding="utf-8")
        is_activity = re.search(r"class\s+\w+\s*(\([^)]*\))?\s*:\s*[^\n{]*Activity", text)
        if not is_activity:
            print(f"FAIL  {path.name} claims fromForeground but is not an Activity")
            failures += 1
    return failures


def check_the_repair_paths_exist(android: Path) -> int:
    """A refusal has to lead somewhere the user can reach."""
    failures = 0
    src = android / "app/src/main/kotlin/ai/jarvis/app"
    manifest = (android / "app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")

    required_files = {
        "ListenTrampolineActivity.kt": src / "ListenTrampolineActivity.kt",
        "WakeTileService.kt": src / "assist/WakeTileService.kt",
        "WakeHeartbeatReceiver.kt": src / "assist/WakeHeartbeatReceiver.kt",
    }
    for name, path in required_files.items():
        if not path.is_file():
            print(f"FAIL  {name} is missing; a refused start has nowhere to send the user")
            failures += 1

    declarations = (
        ".ListenTrampolineActivity",
        "ai.jarvis.app.assist.WakeTileService",
        "ai.jarvis.app.assist.WakeHeartbeatReceiver",
    )
    for name in declarations:
        if name not in manifest:
            print(f"FAIL  {name} is not declared in the manifest, so it cannot run")
            failures += 1

    # The tile is bound by the system, so it must be exported behind the
    # system-only BIND permission — exported with no permission would let any
    # app toggle the microphone.
    tile = re.search(
        r"<service[^>]*WakeTileService.*?/service>", manifest, re.S
    )
    if not tile:
        print("FAIL  the quick-settings tile has no <service> block")
        failures += 1
    else:
        block = tile.group(0)
        if 'android:permission="android.permission.BIND_QUICK_SETTINGS_TILE"' not in block:
            print("FAIL  the tile is exported without BIND_QUICK_SETTINGS_TILE")
            failures += 1
        if "android.service.quicksettings.action.QS_TILE" not in block:
            print("FAIL  the tile has no QS_TILE intent filter, so it never appears")
            failures += 1

    # The heartbeat must NOT be exported: it starts the microphone.
    beat = re.search(r"<receiver[^>]*WakeHeartbeatReceiver[^>]*/?>", manifest, re.S)
    if beat and 'android:exported="false"' not in beat.group(0):
        print("FAIL  the heartbeat receiver is exported; anything could fire the mic")
        failures += 1

    # The trampoline must not be exported either: it starts listening.
    tramp = re.search(r"<activity[^>]*ListenTrampolineActivity.*?/>", manifest, re.S)
    if tramp and 'android:exported="false"' not in tramp.group(0):
        print("FAIL  the listen trampoline is exported; anything could start the mic")
        failures += 1
    return failures


def check_a_transient_mic_failure_no_longer_kills_it(android: Path) -> int:
    """The other half of "it stops working while the app is closed".

    `onUnavailable` used to call `stopSelf()`. The usual cause is another app
    holding the recorder for a moment — a phone call, a voice note — so a
    momentary conflict permanently ended always-on listening. It now retries.
    """
    text = (android / "app/src/main/kotlin/ai/jarvis/app/assist/WakeWordService.kt").read_text(
        encoding="utf-8"
    )
    failures = 0
    handler = re.search(
        r"private fun onMicUnavailable\(.*?\n    \}", text, re.S
    )
    if not handler:
        print("FAIL  WakeWordService has no onMicUnavailable; the retry path is gone")
        return 1
    body = handler.group(0)
    if "postDelayed" not in body:
        print("FAIL  a microphone that could not be opened is no longer retried")
        failures += 1
    # It may still stop for a REVOKED permission — that is not transient — and
    # that branch is guarded by hasMic(). Anything else stopping is the bug.
    stops = body.count("stopSelf()")
    if stops != 1 or "if (!hasMic())" not in body:
        print(
            "FAIL  onMicUnavailable stops for something other than a revoked "
            f"permission ({stops} stopSelf calls)"
        )
        failures += 1
    if "START_STICKY" not in text:
        print("FAIL  the listener is no longer sticky")
        failures += 1
    return failures


def check_the_heartbeat_is_armed_and_cancelled(android: Path) -> int:
    """An alarm that is never cancelled is a wake-up every quarter hour forever."""
    text = (android / "app/src/main/kotlin/ai/jarvis/app/assist/WakeWordService.kt").read_text(
        encoding="utf-8"
    )
    failures = 0
    if "fun armHeartbeat" not in text or "fun cancelHeartbeat" not in text:
        print("FAIL  the heartbeat has no arm/cancel pair")
        return 1
    # Turning listening off must cancel it, in both places that can turn it off.
    if "ACTION_STOP ->" in text:
        stop_branch = text.split("ACTION_STOP ->", 1)[1].split("}", 1)[0]
        if "cancelHeartbeat" not in stop_branch:
            print("FAIL  STOP leaves the quarter-hourly alarm running")
            failures += 1
    if "Route.OFF -> cancelHeartbeat" not in text:
        print("FAIL  ensureRunning does not cancel the alarm when listening is off")
        failures += 1
    if "FLAG_IMMUTABLE" not in text:
        print("FAIL  the heartbeat PendingIntent is not immutable")
        failures += 1
    return failures


def main() -> int:
    here = Path(__file__).resolve()
    android = here.parents[1]

    global KOTLIN_POLICY
    policy_path = android / "app/src/main/kotlin/ai/jarvis/app/assist/WakeStartPolicy.kt"
    if not policy_path.is_file():
        print(f"FAIL  {policy_path} is missing")
        return 1
    KOTLIN_POLICY = policy_path.read_text(encoding="utf-8")

    failures = (
        check_rules()
        + check_a_refusal_always_says_something()
        + check_kotlin_agrees(android)
        + check_only_activities_claim_foreground(android)
        + check_the_repair_paths_exist(android)
        + check_a_transient_mic_failure_no_longer_kills_it(android)
        + check_the_heartbeat_is_armed_and_cancelled(android)
    )
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(
        f"wake start policy: {len(CASES)} routing cases, the Kotlin, the callers, "
        "the manifest, the retry path and the heartbeat all agree"
    )
    return 0


KOTLIN_POLICY = ""

if __name__ == "__main__":
    sys.exit(main())
