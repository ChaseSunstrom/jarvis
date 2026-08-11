#!/usr/bin/env python3
"""Executable spec for the Jarvis boot animation and the GrapheneOS checklist.

Two pieces of Kotlin that this container cannot compile, written down a second
time here where they can actually run:

  1. `app/src/main/kotlin/ai/jarvis/app/ui/BootTimeline.kt` — the power-on
     sequence as a function of elapsed milliseconds. The properties that make
     it safe to drive a view from are all structural: the stages tile the
     timeline with no gap and no overlap, progress never goes backwards,
     `skip()` lands on exactly the frame the full sequence would have ended on,
     and a system animation scale of 0 produces a zero-length animation instead
     of trapping the user in one they turned off.

  2. `app/src/main/kotlin/ai/jarvis/app/compat/GrapheneCompat.kt` — the network
     verdict and the requirements checklist. Every requirement must map to a
     deep link and to a satisfied/unsatisfied verdict, and the network verdict
     must never be optimistic when the OS has said no.

Both are mirrored below and then checked against the Kotlin source, so a change
to one copy that is not made to the other fails here rather than on a phone.

Run:  python3 android-app/tools/boot_timeline_test.py
"""

from __future__ import annotations

import re
import sys
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KOTLIN_TIMELINE = ROOT / "app/src/main/kotlin/ai/jarvis/app/ui/BootTimeline.kt"
KOTLIN_ANIMATION = ROOT / "app/src/main/kotlin/ai/jarvis/app/ui/JarvisBootAnimation.kt"
KOTLIN_ORB = ROOT / "app/src/main/kotlin/ai/jarvis/app/ui/JarvisOrbView.kt"
#: The reactor itself. `JarvisOrbView` is the HUD that hosts it — scrim,
#: brackets, wordmark, boot hooks — and the geometry the boot sequence drives
#: lives one level down, shared with the floating overlay window.
KOTLIN_REACTOR = ROOT / "app/src/main/kotlin/ai/jarvis/app/ui/ReactorOrb.kt"
KOTLIN_COMPAT = ROOT / "app/src/main/kotlin/ai/jarvis/app/compat/GrapheneCompat.kt"
KOTLIN_CRASH = ROOT / "app/src/main/kotlin/ai/jarvis/app/crash/JarvisCrashHandler.kt"
KOTLIN_APP = ROOT / "app/src/main/kotlin/ai/jarvis/app/JarvisApp.kt"
KOTLIN_MAIN = ROOT / "app/src/main/kotlin/ai/jarvis/app/MainActivity.kt"
KOTLIN_CONFIG = ROOT / "app/src/main/kotlin/ai/jarvis/app/config/JarvisConfig.kt"
KOTLIN_CHANNEL = ROOT / "app/src/main/kotlin/ai/jarvis/app/channel/JarvisChannel.kt"
KOTLIN_CRASH_UI = ROOT / "app/src/main/kotlin/ai/jarvis/app/ui/CrashLogActivity.kt"
KOTLIN_SETTINGS = ROOT / "app/src/main/kotlin/ai/jarvis/app/SettingsActivity.kt"
#: Debug source set, so it is outside KOTLIN_SRC_ROOT and the structural checks
#: below never see it. Read by name because it owns the one switch that decides
#: which frame clock the orb runs under instrumentation.
KOTLIN_TEST_HOOKS = ROOT / "app/src/debug/kotlin/ai/jarvis/app/testing/TestHooks.kt"
MANIFEST = ROOT / "app/src/main/AndroidManifest.xml"
THEMES = ROOT / "app/src/main/res/values/themes.xml"
THEMES_V31 = ROOT / "app/src/main/res/values-v31/themes.xml"
ADAPTIVE_ICON = ROOT / "app/src/main/res/mipmap-anydpi-v26/ic_jarvis.xml"
KOTLIN_SRC_ROOT = ROOT / "app/src/main/kotlin"

#: Every Kotlin file this spec reads back by name. The structural checks at the
#: bottom run over the WHOLE source tree instead — there is no kotlinc in this
#: container, so a repo-wide stand-in for the compiler is worth more than one
#: scoped to the files this feature happened to touch.
TRACKED_KOTLIN = [
    KOTLIN_TIMELINE,
    KOTLIN_ANIMATION,
    KOTLIN_ORB,
    KOTLIN_REACTOR,
    KOTLIN_COMPAT,
    KOTLIN_CRASH,
    KOTLIN_CRASH_UI,
    KOTLIN_APP,
    KOTLIN_MAIN,
    KOTLIN_CONFIG,
    KOTLIN_CHANNEL,
    KOTLIN_TEST_HOOKS,
    ROOT / "app/src/main/kotlin/ai/jarvis/app/ui/SystemCheckActivity.kt",
]

# =========================================================================
# 1. The boot timeline, mirrored from BootTimeline.kt
# =========================================================================

TOTAL_MS = 1400

SCAN_START_MS = 0
IGNITE_START_MS = 120
RINGS_START_MS = 300
WORDMARK_START_MS = 600
CHECKS_START_MS = 850
HANDOFF_START_MS = 1200

# name -> (start, end). Order IS the sequence order.
STAGES = [
    ("SCAN", SCAN_START_MS, IGNITE_START_MS),
    ("IGNITE", IGNITE_START_MS, RINGS_START_MS),
    ("RINGS", RINGS_START_MS, WORDMARK_START_MS),
    ("WORDMARK", WORDMARK_START_MS, CHECKS_START_MS),
    ("CHECKS", CHECKS_START_MS, HANDOFF_START_MS),
    ("HANDOFF", HANDOFF_START_MS, TOTAL_MS),
]

SCAN_FADE_MS = 100
CORE_RISE_MS = 180
CORE_FADE_MS = 90
FLARE_MS = 200

RING_COUNT = 4
RING_STAGGER_MS = 60
RING_MS = 120
RING_TENSION = 2.2

LETTER_COUNT = 6
LETTER_STAGGER_MS = 26
LETTER_MS = 120
LETTER_SPACING_START = 0.90
LETTER_SPACING_END = 0.55
LETTER_BLUR_DP = 7.0

CHECK_LINE_COUNT = 3
CHECK_STAGGER_MS = 90
CHECK_TYPE_MS = 170

HANDOFF_FADE_MS = 140
HOME_FADE_DELAY_MS = 60
HOME_FADE_MS = 140

MAX_DURATION_MS = 4200

CHECK_CORE = "core online"
CHECK_VOICE = "voice pipeline ready"


def clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def decelerate(t: float, factor: float = 1.0) -> float:
    return 1.0 - (1.0 - clamp01(t)) ** (2.0 * factor)


def accelerate(t: float, factor: float = 1.0) -> float:
    return clamp01(t) ** (2.0 * factor)


def overshoot(t: float, tension: float = RING_TENSION) -> float:
    p = clamp01(t) - 1.0
    return p * p * ((tension + 1.0) * p + tension) + 1.0


def window(t: int, start: int, duration: int) -> float:
    if duration <= 0:
        return 1.0 if t >= start else 0.0
    return clamp01((t - start) / duration)


def stage_at(t: int) -> str:
    for name, _start, end in STAGES:
        if t < end:
            return name
    return "HANDOFF"


def stage_progress(t: int, stage: str) -> float:
    for name, start, end in STAGES:
        if name == stage:
            return window(t, start, end - start)
    raise KeyError(stage)


def scan_y(t: int) -> float:
    return decelerate(window(t, SCAN_START_MS, IGNITE_START_MS), 0.7)


def scan_alpha(t: int) -> float:
    if t <= IGNITE_START_MS:
        return 1.0
    return 1.0 - window(t, IGNITE_START_MS, SCAN_FADE_MS)


def core_scale(t: int) -> float:
    if t < IGNITE_START_MS:
        return 0.0
    return decelerate(window(t, IGNITE_START_MS, CORE_RISE_MS), 1.8)


def core_alpha(t: int) -> float:
    if t < IGNITE_START_MS:
        return 0.0
    return decelerate(window(t, IGNITE_START_MS, CORE_FADE_MS), 1.4)


def flare_alpha(t: int) -> float:
    if t < IGNITE_START_MS:
        return 0.0
    p = window(t, IGNITE_START_MS, FLARE_MS)
    if p >= 1.0:
        return 0.0
    return 4.0 * p * (1.0 - p)


def ring_start_ms(i: int) -> int:
    return RINGS_START_MS + i * RING_STAGGER_MS


def ring_reveal(t: int, i: int) -> float:
    p = window(t, ring_start_ms(i), RING_MS)
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    return overshoot(p)


def ring_alpha(t: int, i: int) -> float:
    return decelerate(window(t, ring_start_ms(i), RING_MS), 1.2)


def letter_start_ms(i: int) -> int:
    return WORDMARK_START_MS + i * LETTER_STAGGER_MS


def letter_alpha(t: int, i: int) -> float:
    return decelerate(window(t, letter_start_ms(i), LETTER_MS), 1.3)


def letter_blur(t: int, i: int) -> float:
    return LETTER_BLUR_DP * (
        1.0 - decelerate(window(t, letter_start_ms(i), LETTER_MS), 1.3)
    )


def letter_spacing(t: int) -> float:
    span = letter_start_ms(LETTER_COUNT - 1) + LETTER_MS - WORDMARK_START_MS
    p = decelerate(window(t, WORDMARK_START_MS, span), 1.6)
    return LETTER_SPACING_START + (LETTER_SPACING_END - LETTER_SPACING_START) * p


def check_start_ms(i: int) -> int:
    return CHECKS_START_MS + i * CHECK_STAGGER_MS


def check_progress(t: int, i: int) -> float:
    return window(t, check_start_ms(i), CHECK_TYPE_MS)


def typed_chars(t: int, i: int, length: int) -> int:
    if length <= 0:
        return 0
    n = int(check_progress(t, i) * length)
    return length if n > length else n


def chrome_alpha(t: int) -> float:
    return 1.0 - decelerate(window(t, HANDOFF_START_MS, HANDOFF_FADE_MS), 1.2)


def home_alpha(t: int) -> float:
    return decelerate(
        window(t, HANDOFF_START_MS + HOME_FADE_DELAY_MS, HOME_FADE_MS), 1.2
    )


def orb_chrome_alpha(t: int) -> float:
    """The exact complement of `chrome_alpha`; see BootTimeline.orbChromeAlpha."""
    return decelerate(window(t, HANDOFF_START_MS, HANDOFF_FADE_MS), 1.2)


def should_skip(animator_scale: float, reduced_motion: bool) -> bool:
    return reduced_motion or animator_scale <= 0.0 or animator_scale != animator_scale


def scaled_duration_ms(animator_scale: float, reduced_motion: bool = False) -> int:
    if should_skip(animator_scale, reduced_motion):
        return 0
    scaled = int(TOTAL_MS * animator_scale)
    return MAX_DURATION_MS if scaled > MAX_DURATION_MS else scaled


def state_at(t: int) -> dict:
    return {
        "scanAlpha": scan_alpha(t),
        "coreScale": core_scale(t),
        "coreAlpha": core_alpha(t),
        "flareAlpha": flare_alpha(t),
        "ringReveal": [ring_reveal(t, i) for i in range(RING_COUNT)],
        "letterAlpha": [letter_alpha(t, i) for i in range(LETTER_COUNT)],
        "letterSpacing": letter_spacing(t),
        "checkProgress": [check_progress(t, i) for i in range(CHECK_LINE_COUNT)],
        "chromeAlpha": chrome_alpha(t),
        "orbChromeAlpha": orb_chrome_alpha(t),
        "homeAlpha": home_alpha(t),
    }


def end_state() -> dict:
    return state_at(TOTAL_MS)


def check_lines(action_count):
    lines = [CHECK_CORE, CHECK_VOICE]
    if action_count is not None and action_count > 0:
        lines.append(
            "1 action ready" if action_count == 1 else f"{action_count} actions ready"
        )
    return lines


# =========================================================================
# 2. GrapheneCompat, mirrored from GrapheneCompat.kt
# =========================================================================

SUSPECT_THRESHOLD = 3

SIG_SECURITY = "SECURITY"
SIG_HOST = "HOST"
SIG_OTHER = "OTHER"

V_GRANTED = "GRANTED"
V_DENIED = "DENIED"
V_SUSPECT = "SUSPECT"


def classify(exception_class_names) -> str:
    for name in exception_class_names:
        if name == "java.lang.SecurityException":
            return SIG_SECURITY
    for name in exception_class_names:
        if name == "java.net.UnknownHostException":
            return SIG_HOST
    return SIG_OTHER


def network_verdict(permission_granted, security_denials, host_failures, successes):
    if not permission_granted:
        return V_DENIED
    if security_denials > 0:
        return V_DENIED
    if successes > 0:
        return V_GRANTED
    if host_failures >= SUSPECT_THRESHOLD:
        return V_SUSPECT
    return V_GRANTED


# id -> (essential, Settings constant name, needs package: uri)
REQUIREMENT_TABLE = [
    ("network", True, "ACTION_APPLICATION_DETAILS_SETTINGS", True),
    ("microphone", True, "ACTION_APPLICATION_DETAILS_SETTINGS", True),
    ("assistant", False, "ACTION_VOICE_INPUT_SETTINGS", False),
    ("accessibility", False, "ACTION_ACCESSIBILITY_SETTINGS", False),
    ("notifications", False, "ACTION_NOTIFICATION_LISTENER_SETTINGS", False),
    ("battery", True, "ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS", True),
    ("post_notifications", True, "ACTION_APPLICATION_DETAILS_SETTINGS", True),
    ("on_screen", True, "ACTION_MANAGE_OVERLAY_PERMISSION", True),
    ("full_screen", False, "ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT", True),
    ("overlay", False, "ACTION_MANAGE_OVERLAY_PERMISSION", True),
    ("exact_alarms", False, "ACTION_REQUEST_SCHEDULE_EXACT_ALARM", True),
]

# The actual intent action strings, so "maps to a deep link" is checked against
# a value and not just against a symbol that could be anything.
SETTINGS_ACTIONS = {
    "ACTION_APPLICATION_DETAILS_SETTINGS": "android.settings.APPLICATION_DETAILS_SETTINGS",
    "ACTION_VOICE_INPUT_SETTINGS": "android.settings.VOICE_INPUT_SETTINGS",
    "ACTION_ACCESSIBILITY_SETTINGS": "android.settings.ACCESSIBILITY_SETTINGS",
    "ACTION_NOTIFICATION_LISTENER_SETTINGS": "android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS",
    "ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS": "android.settings.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS",
    "ACTION_MANAGE_OVERLAY_PERMISSION": "android.settings.action.MANAGE_OVERLAY_PERMISSION",
    "ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT": "android.settings.MANAGE_APP_USE_FULL_SCREEN_INTENT",
    "ACTION_REQUEST_SCHEDULE_EXACT_ALARM": "android.settings.REQUEST_SCHEDULE_EXACT_ALARM",
}

STATUS_FIELDS = [
    "network",
    "microphone",
    "assistant",
    "accessibility",
    "notificationListener",
    "batteryExempt",
    "batteryRestricted",
    "canDrawOverlays",
    "postNotifications",
    "fullScreenIntents",
    "exactAlarms",
]


def evaluate(status: dict):
    """Mirror of GrapheneCompat.evaluate: Status -> ordered requirement list."""
    satisfied = {
        "network": status["network"],
        "microphone": status["microphone"],
        "assistant": status["assistant"],
        "accessibility": status["accessibility"],
        "notifications": status["notificationListener"],
        # Exempt from doze AND not background-restricted. Either one alone
        # still gets the automation service killed.
        "battery": status["batteryExempt"] and not status["batteryRestricted"],
        # The other half of "does listening survive a reboot", and the whole
        # of "does the wake word draw an orb over the app you are in".
        "overlay": status["canDrawOverlays"],
        # Runtime since Android 13 and never requested until now, which is why a
        # wake word could not put anything on screen at all.
        "post_notifications": status["postNotifications"],
        # A DISJUNCTION, and the only one on the checklist. Either grant lets a
        # wake word put something in front of you; with neither, nothing was
        # both essential and missing, so no banner appeared and the phone sat
        # broken with nothing anywhere saying so.
        "on_screen": status["canDrawOverlays"] or status["fullScreenIntents"],
        # Android 14 grants this at install only to calling and alarm apps.
        # Without it setFullScreenIntent silently becomes a heads-up, and the
        # conversation waits in the shade for a tap.
        "full_screen": status["fullScreenIntents"],
        "exact_alarms": status["exactAlarms"],
    }
    out = []
    for rid, essential, action_const, needs_uri in REQUIREMENT_TABLE:
        out.append(
            {
                "id": rid,
                "essential": essential,
                "satisfied": satisfied[rid],
                "action": SETTINGS_ACTIONS[action_const],
                "actionConst": action_const,
                "needsPackageUri": needs_uri,
            }
        )
    return out


# =========================================================================
# Helpers for reading the Kotlin back
# =========================================================================


def kotlin_consts(path: Path) -> dict:
    """`const val NAME = 123L` / `= 1.5f` -> {NAME: value}."""
    src = path.read_text()
    out = {}
    for name, raw in re.findall(
        r"const val ([A-Z][A-Z0-9_]*)\s*(?::\s*\w+\s*)?=\s*([0-9_]+\.?[0-9]*)[fFlL]?\b", src
    ):
        text = raw.replace("_", "")
        out[name] = float(text) if "." in text else int(text)
    return out


def flat(path: Path) -> str:
    return re.sub(r"\s+", " ", path.read_text())


def code_only(path: Path) -> str:
    """Source with comments stripped.

    These checks are about what the code does, not about what the KDoc says.
    Without this, a doc comment that *mentions* `postDelayed` in order to say
    the design forbids it would fail the check that forbids it.
    """
    src = re.sub(r"/\*.*?\*/", " ", path.read_text(), flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


def first_statements(path: Path, signature: str) -> list[str]:
    """The statements of a function body, in order, comments and braces gone."""
    body = path.read_text().split(signature, 1)[1]
    body = body.split("\n    }", 1)[0]
    out = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line in ("{", "}"):
            continue
        out.append(line)
    return out


# =========================================================================
# Tests: the timeline's shape
# =========================================================================


def test_stages_tile_the_timeline_with_no_gap_or_overlap():
    assert STAGES[0][1] == 0, "the sequence must start at 0"
    assert STAGES[-1][2] == TOTAL_MS, "the last stage must end at TOTAL_MS"
    for i in range(len(STAGES) - 1):
        name, _start, end = STAGES[i]
        nxt_name, nxt_start, _ = STAGES[i + 1]
        assert end == nxt_start, (
            f"gap/overlap between {name} (ends {end}) and {nxt_name} (starts {nxt_start})"
        )
    for name, start, end in STAGES:
        assert end > start, f"stage {name} is empty"


def test_every_millisecond_belongs_to_exactly_one_stage():
    for t in range(0, TOTAL_MS):
        hits = [name for name, start, end in STAGES if start <= t < end]
        assert len(hits) == 1, f"t={t} is in {hits}"
        assert stage_at(t) == hits[0], f"stage_at({t}) said {stage_at(t)}, not {hits[0]}"


def test_stage_boundaries_match_the_brief():
    """The keyframes are a design decision; they are pinned deliberately."""
    assert dict((n, s) for n, s, _ in STAGES) == {
        "SCAN": 0,
        "IGNITE": 120,
        "RINGS": 300,
        "WORDMARK": 600,
        "CHECKS": 850,
        "HANDOFF": 1200,
    }
    assert TOTAL_MS == 1400


def test_stage_progress_is_zero_at_start_and_one_at_end():
    for name, start, end in STAGES:
        assert stage_progress(start, name) == 0.0, name
        assert stage_progress(end, name) == 1.0, name
        assert stage_progress(start - 500, name) == 0.0, name
        assert stage_progress(end + 500, name) == 1.0, name


# =========================================================================
# Tests: monotonic progress
# =========================================================================

MONOTONIC = [
    ("scanY", scan_y),
    ("coreScale", core_scale),
    ("coreAlpha", core_alpha),
    ("homeAlpha", home_alpha),
    ("orbChromeAlpha", orb_chrome_alpha),
    ("letterSpacing (descending)", lambda t: -letter_spacing(t)),
    ("chromeAlpha (descending)", lambda t: -chrome_alpha(t)),
]


def test_progress_never_goes_backwards():
    for label, fn in MONOTONIC:
        prev = fn(0)
        for t in range(0, TOTAL_MS + 200):
            now = fn(t)
            assert now >= prev - 1e-9, f"{label} went backwards at t={t}: {prev} -> {now}"
            prev = now


def test_per_element_progress_never_goes_backwards():
    for i in range(RING_COUNT):
        prev = ring_alpha(0, i)
        for t in range(0, TOTAL_MS + 200):
            now = ring_alpha(t, i)
            assert now >= prev - 1e-9, f"ring {i} alpha regressed at t={t}"
            prev = now
    for i in range(LETTER_COUNT):
        prev = letter_alpha(0, i)
        for t in range(0, TOTAL_MS + 200):
            now = letter_alpha(t, i)
            assert now >= prev - 1e-9, f"letter {i} regressed at t={t}"
            prev = now
    for i in range(CHECK_LINE_COUNT):
        prev = check_progress(0, i)
        for t in range(0, TOTAL_MS + 200):
            now = check_progress(t, i)
            assert now >= prev - 1e-9, f"check line {i} regressed at t={t}"
            prev = now


def test_typed_chars_never_un_types():
    line = CHECK_VOICE
    for i in range(CHECK_LINE_COUNT):
        prev = 0
        for t in range(0, TOTAL_MS + 200):
            now = typed_chars(t, i, len(line))
            assert now >= prev, f"line {i} lost characters at t={t}"
            assert 0 <= now <= len(line)
            prev = now
        assert prev == len(line), f"line {i} never finished typing"


def test_alphas_stay_in_range():
    fns = (
        [("scanAlpha", scan_alpha), ("coreAlpha", core_alpha), ("flareAlpha", flare_alpha),
         ("chromeAlpha", chrome_alpha), ("orbChromeAlpha", orb_chrome_alpha),
         ("homeAlpha", home_alpha), ("coreScale", core_scale)]
        + [(f"ringAlpha[{i}]", lambda t, i=i: ring_alpha(t, i)) for i in range(RING_COUNT)]
        + [(f"letterAlpha[{i}]", lambda t, i=i: letter_alpha(t, i)) for i in range(LETTER_COUNT)]
    )
    for label, fn in fns:
        for t in range(-100, TOTAL_MS + 200):
            v = fn(t)
            assert -1e-9 <= v <= 1.0 + 1e-9, f"{label} out of range at t={t}: {v}"


# =========================================================================
# Tests: the sequence actually does what the brief describes
# =========================================================================


def test_frame_zero_is_black_except_the_scan_line():
    s = state_at(0)
    assert s["coreScale"] == 0.0
    assert s["coreAlpha"] == 0.0
    assert s["flareAlpha"] == 0.0
    assert all(v == 0.0 for v in s["ringReveal"])
    assert all(v == 0.0 for v in s["letterAlpha"])
    assert all(v == 0.0 for v in s["checkProgress"])
    assert s["homeAlpha"] == 0.0
    assert s["orbChromeAlpha"] == 0.0
    # ...and the scan line is the one thing that IS visible.
    assert s["scanAlpha"] == 1.0
    assert scan_y(0) == 0.0


def test_scan_sweeps_the_whole_screen_before_ignition():
    assert scan_y(IGNITE_START_MS) == 1.0, "scan must reach the bottom by ignition"
    assert scan_alpha(IGNITE_START_MS + SCAN_FADE_MS) == 0.0, "scan must be gone after"


def test_core_ignites_from_a_point():
    assert core_scale(IGNITE_START_MS - 1) == 0.0
    assert core_scale(IGNITE_START_MS) == 0.0
    assert 0.0 < core_scale(IGNITE_START_MS + 20) < 1.0
    assert core_scale(RINGS_START_MS) == 1.0, "core must be full size when rings start"
    # Decelerating: more of the growth happens early than late.
    first_half = core_scale(IGNITE_START_MS + CORE_RISE_MS // 2)
    assert first_half > 0.5, f"core rise is not decelerating ({first_half})"


def test_flare_is_a_one_shot_bloom():
    assert flare_alpha(IGNITE_START_MS) == 0.0
    peak_t = IGNITE_START_MS + FLARE_MS // 2
    assert abs(flare_alpha(peak_t) - 1.0) < 0.01, "flare should peak halfway"
    assert flare_alpha(IGNITE_START_MS + FLARE_MS) == 0.0
    assert flare_alpha(TOTAL_MS) == 0.0, "flare must not linger into the end state"


def test_rings_arrive_one_at_a_time_and_overshoot():
    # Strictly staggered starts, in order, outward.
    starts = [ring_start_ms(i) for i in range(RING_COUNT)]
    assert starts == sorted(starts) and len(set(starts)) == RING_COUNT
    assert starts[0] == RINGS_START_MS
    # Every ring is finished by the time the wordmark begins.
    last_end = ring_start_ms(RING_COUNT - 1) + RING_MS
    assert last_end == WORDMARK_START_MS, (
        f"rings finish at {last_end}, wordmark starts at {WORDMARK_START_MS}"
    )
    # Each ring is still absent when the previous one starts arriving.
    for i in range(1, RING_COUNT):
        assert ring_reveal(ring_start_ms(i - 1), i) == 0.0, f"ring {i} started too early"
    # And each one overshoots its resting size somewhere in its window.
    for i in range(RING_COUNT):
        peak = max(
            ring_reveal(t, i) for t in range(ring_start_ms(i), ring_start_ms(i) + RING_MS)
        )
        assert peak > 1.0, f"ring {i} never overshoots (peak {peak})"
        assert peak < 1.25, f"ring {i} overshoot is a bounce, not a flourish ({peak})"
        assert ring_reveal(ring_start_ms(i) + RING_MS, i) == 1.0, f"ring {i} did not settle"


def test_wordmark_resolves_letter_by_letter_and_settles():
    starts = [letter_start_ms(i) for i in range(LETTER_COUNT)]
    assert starts == sorted(starts) and len(set(starts)) == LETTER_COUNT
    assert starts[0] == WORDMARK_START_MS
    assert starts[-1] + LETTER_MS == CHECKS_START_MS, (
        "the last letter must land exactly as the check lines begin"
    )
    assert LETTER_COUNT == len("JARVIS")
    # Spacing settles from wide to the resting value, and never overshoots.
    assert abs(letter_spacing(WORDMARK_START_MS) - LETTER_SPACING_START) < 1e-9
    assert abs(letter_spacing(CHECKS_START_MS) - LETTER_SPACING_END) < 1e-9
    for t in range(0, TOTAL_MS):
        assert LETTER_SPACING_END - 1e-9 <= letter_spacing(t) <= LETTER_SPACING_START + 1e-9
    # Blur starts wide and reaches zero.
    assert abs(letter_blur(WORDMARK_START_MS, 0) - LETTER_BLUR_DP) < 1e-9
    assert letter_blur(CHECKS_START_MS, LETTER_COUNT - 1) == 0.0


def test_check_lines_type_on_in_order_and_finish_before_the_handoff():
    starts = [check_start_ms(i) for i in range(CHECK_LINE_COUNT)]
    assert starts == sorted(starts) and len(set(starts)) == CHECK_LINE_COUNT
    assert starts[0] == CHECKS_START_MS
    assert starts[-1] + CHECK_TYPE_MS == HANDOFF_START_MS, (
        "the last check line must finish exactly as the handoff begins"
    )
    for i in range(1, CHECK_LINE_COUNT):
        assert check_progress(check_start_ms(i - 1), i) == 0.0


def test_third_check_line_uses_real_data_or_is_omitted():
    assert check_lines(None) == [CHECK_CORE, CHECK_VOICE]
    assert check_lines(0) == [CHECK_CORE, CHECK_VOICE], "0 actions must omit the line"
    assert check_lines(-4) == [CHECK_CORE, CHECK_VOICE], "garbage must omit the line"
    assert check_lines(1) == [CHECK_CORE, CHECK_VOICE, "1 action ready"]
    assert check_lines(12) == [CHECK_CORE, CHECK_VOICE, "12 actions ready"]
    # There is never a fourth line, so the timeline's three slots always suffice.
    for n in (None, 0, 1, 2, 500):
        assert len(check_lines(n)) <= CHECK_LINE_COUNT


def test_handoff_leaves_only_the_orb():
    assert chrome_alpha(HANDOFF_START_MS) == 1.0
    assert chrome_alpha(HANDOFF_START_MS + HANDOFF_FADE_MS) == 0.0
    assert home_alpha(HANDOFF_START_MS) == 0.0
    assert home_alpha(TOTAL_MS) == 1.0
    # The orb itself is untouched by the handoff: it is the one thing that stays.
    assert core_scale(TOTAL_MS) == 1.0
    assert core_alpha(TOTAL_MS) == 1.0
    assert all(v == 1.0 for v in state_at(TOTAL_MS)["ringReveal"])


def test_home_ui_starts_arriving_while_the_chrome_is_still_leaving():
    """A cut would read as a jump; the two have to overlap."""
    overlap = [
        t
        for t in range(HANDOFF_START_MS, TOTAL_MS)
        if chrome_alpha(t) > 0.0 and home_alpha(t) > 0.0
    ]
    assert overlap, "chrome and home never overlap — the handoff is a cut"


def composited(a: float, b: float) -> float:
    """Opacity of two stacked draws of the same thing at `a` and `b`."""
    return a + b - a * b


def test_the_wordmark_crossfade_has_no_hole_in_it():
    """The handoff draws ONE wordmark through two views, so it must not dip.

    `JarvisBootAnimation` paints "JARVIS" with `chromeAlpha`, and the orb paints
    the identical glyphs, in the identical colour, on the identical baseline,
    with whatever `JarvisBootAnimation.pushFrame` puts in `drive.chromeAlpha`.
    Those two are a crossfade of one object.

    It used to be `homeAlpha`, which starts HOME_FADE_DELAY_MS after the chrome
    begins leaving — right for the home CONTROLS, which crossfade with nothing,
    and a 60ms hole in the middle of this one. The wordmark fell to under a
    third of its opacity and came back, which is the flicker at the end of the
    power-on.
    """
    worst_correct = min(
        composited(chrome_alpha(t), orb_chrome_alpha(t))
        for t in range(HANDOFF_START_MS, TOTAL_MS + 1)
    )
    assert worst_correct > 0.7, (
        f"the wordmark drops to {worst_correct:.3f} of its opacity mid-handoff"
    )

    # The bug this replaced, kept as an executable statement of it: with
    # homeAlpha driving the orb's chrome the same crossfade collapses.
    worst_buggy = min(
        composited(chrome_alpha(t), home_alpha(t))
        for t in range(HANDOFF_START_MS, TOTAL_MS + 1)
    )
    assert worst_buggy < 0.35, (
        "homeAlpha no longer produces the dip this function exists to avoid; if "
        "the handoff constants changed, re-derive which curve the orb's chrome "
        "should follow rather than deleting this"
    )

    # Complementary, so the two curves also meet the frame the orb takes over on.
    for t in range(HANDOFF_START_MS - 200, TOTAL_MS + 200):
        assert abs(chrome_alpha(t) + orb_chrome_alpha(t) - 1.0) < 1e-9, (
            f"the orb's chrome is not the complement of the overlay's at t={t}"
        )


# =========================================================================
# Tests: skip() and the animation scale
# =========================================================================


def test_skip_lands_on_exactly_the_natural_end_state():
    """`skip()` is 'set the clock to TOTAL_MS', not a second code path."""
    assert end_state() == state_at(TOTAL_MS)
    # And the end state is the settled one, whatever route got there.
    s = end_state()
    assert s["coreScale"] == 1.0
    assert s["coreAlpha"] == 1.0
    assert s["scanAlpha"] == 0.0
    assert s["flareAlpha"] == 0.0
    assert s["ringReveal"] == [1.0] * RING_COUNT
    assert s["letterAlpha"] == [1.0] * LETTER_COUNT
    assert abs(s["letterSpacing"] - LETTER_SPACING_END) < 1e-9
    assert s["checkProgress"] == [1.0] * CHECK_LINE_COUNT
    assert s["chromeAlpha"] == 0.0
    assert s["orbChromeAlpha"] == 1.0
    assert s["homeAlpha"] == 1.0


def test_skipping_from_anywhere_reaches_the_same_state():
    for t in range(0, TOTAL_MS, 37):
        assert state_at(TOTAL_MS) == end_state(), f"skip from t={t} diverged"


def test_state_is_pinned_past_the_end():
    for t in (TOTAL_MS, TOTAL_MS + 1, TOTAL_MS + 5000):
        assert state_at(t) == end_state(), f"state drifted at t={t}"


def test_zero_animation_scale_means_zero_duration():
    assert scaled_duration_ms(0.0) == 0
    assert should_skip(0.0, False) is True
    # Negative and NaN are garbage from a settings read; treat them as "off".
    assert scaled_duration_ms(-1.0) == 0
    assert scaled_duration_ms(float("nan")) == 0


def test_reduced_motion_skips_even_at_full_scale():
    assert scaled_duration_ms(1.0, reduced_motion=True) == 0
    assert should_skip(1.0, True) is True


def test_normal_and_slowed_scales_are_honoured_and_capped():
    assert scaled_duration_ms(1.0) == TOTAL_MS
    assert scaled_duration_ms(0.5) == TOTAL_MS // 2
    assert scaled_duration_ms(2.0) == 2 * TOTAL_MS
    # A developer-options 10x must not hang the launcher.
    assert scaled_duration_ms(10.0) == MAX_DURATION_MS
    assert scaled_duration_ms(1000.0) == MAX_DURATION_MS


# =========================================================================
# Tests: GrapheneCompat network verdict
# =========================================================================


def test_revoked_permission_is_always_denied():
    for sec, host, ok in product((0, 1, 5), (0, 1, 9), (0, 1, 7)):
        assert network_verdict(False, sec, host, ok) == V_DENIED


def test_security_exception_denies_even_with_the_permission_granted():
    """This is the GrapheneOS case: INTERNET reads GRANTED, the OS says no."""
    assert network_verdict(True, 1, 0, 0) == V_DENIED


def test_one_success_outranks_mere_suspicion():
    assert network_verdict(True, 0, 99, 1) == V_GRANTED


def test_a_security_denial_outranks_an_earlier_success():
    """The Network toggle is revocable while the app is running.

    noteNetworkSuccess() clears the denial counters, so a non-zero
    securityDenials can only have been recorded AFTER the last success. Letting
    a stale success win would pin the verdict to GRANTED for the rest of the
    process the moment the user revoked Network mid-session, and the banner
    explaining the outage would never appear.
    """
    assert network_verdict(True, 1, 0, 1) == V_DENIED
    assert network_verdict(True, 1, 99, 40) == V_DENIED


def test_repeated_resolve_failures_become_suspicion_not_certainty():
    for n in range(SUSPECT_THRESHOLD):
        assert network_verdict(True, 0, n, 0) == V_GRANTED, (
            f"{n} failures should not be enough to accuse the user's settings"
        )
    assert network_verdict(True, 0, SUSPECT_THRESHOLD, 0) == V_SUSPECT
    assert network_verdict(True, 0, SUSPECT_THRESHOLD + 40, 0) == V_SUSPECT


def test_classify_reads_the_cause_chain():
    assert classify(["java.lang.SecurityException"]) == SIG_SECURITY
    assert classify(["java.net.UnknownHostException"]) == SIG_HOST
    assert classify(["java.net.ConnectException"]) == SIG_OTHER
    assert classify(["javax.net.ssl.SSLHandshakeException"]) == SIG_OTHER
    assert classify(["java.net.SocketTimeoutException"]) == SIG_OTHER
    assert classify([]) == SIG_OTHER
    # A SecurityException anywhere in the chain wins over a host failure.
    assert (
        classify(["java.io.IOException", "java.net.UnknownHostException",
                  "java.lang.SecurityException"])
        == SIG_SECURITY
    )


def test_a_server_that_is_merely_off_never_accuses_the_network_permission():
    """Connection refused / timeout must not produce a permission banner."""
    verdict = V_GRANTED
    sec = host = 0
    for _ in range(50):
        signal = classify(["java.net.ConnectException"])
        assert signal == SIG_OTHER
        verdict = network_verdict(True, sec, host, 0)
    assert verdict == V_GRANTED


# =========================================================================
# Tests: the requirements checklist
# =========================================================================


def all_status(value: bool) -> dict:
    return {f: value for f in STATUS_FIELDS}


def test_every_requirement_has_an_id_a_verdict_and_a_deep_link():
    reqs = evaluate(all_status(False))
    assert len(reqs) == len(REQUIREMENT_TABLE)
    seen = set()
    for r in reqs:
        assert r["id"], "requirement without an id"
        assert r["id"] not in seen, f"duplicate requirement id {r['id']}"
        seen.add(r["id"])
        assert isinstance(r["satisfied"], bool)
        assert r["action"].startswith("android.settings."), r["action"]
        assert isinstance(r["needsPackageUri"], bool)


def test_the_checklist_covers_exactly_the_documented_surface():
    ids = [r["id"] for r in evaluate(all_status(False))]
    assert ids == [
        "network",
        "microphone",
        "assistant",
        "accessibility",
        "notifications",
        "battery",
        "post_notifications",
        "on_screen",
        "full_screen",
        "overlay",
        "exact_alarms",
    ], ids
    assert ids[0] == "network", "network is the most common GrapheneOS surprise; it goes first"


def test_verdicts_follow_the_status_for_every_combination():
    for combo in product((False, True), repeat=len(STATUS_FIELDS)):
        status = dict(zip(STATUS_FIELDS, combo))
        by_id = {r["id"]: r for r in evaluate(status)}
        assert by_id["network"]["satisfied"] == status["network"]
        assert by_id["microphone"]["satisfied"] == status["microphone"]
        assert by_id["assistant"]["satisfied"] == status["assistant"]
        assert by_id["accessibility"]["satisfied"] == status["accessibility"]
        assert by_id["notifications"]["satisfied"] == status["notificationListener"]
        assert by_id["exact_alarms"]["satisfied"] == status["exactAlarms"]
        assert by_id["overlay"]["satisfied"] == status["canDrawOverlays"]
        assert by_id["post_notifications"]["satisfied"] == status["postNotifications"]
        assert by_id["on_screen"]["satisfied"] == (
            status["canDrawOverlays"] or status["fullScreenIntents"]
        )
        assert by_id["full_screen"]["satisfied"] == status["fullScreenIntents"]
        # Battery needs BOTH: exempt from doze and not background-restricted.
        assert by_id["battery"]["satisfied"] == (
            status["batteryExempt"] and not status["batteryRestricted"]
        )


def test_background_restriction_alone_fails_the_battery_check():
    status = all_status(True)  # batteryExempt True, batteryRestricted True
    by_id = {r["id"]: r for r in evaluate(status)}
    assert by_id["battery"]["satisfied"] is False, (
        "an app that is exempt from doze but background-restricted is still killed"
    )


def test_all_granted_leaves_nothing_unsatisfied():
    status = all_status(True)
    status["batteryRestricted"] = False
    reqs = evaluate(status)
    assert all(r["satisfied"] for r in reqs)
    assert [r for r in reqs if r["essential"] and not r["satisfied"]] == []


def test_nothing_granted_flags_every_essential():
    reqs = evaluate(all_status(False))
    missing = [r["id"] for r in reqs if r["essential"] and not r["satisfied"]]
    assert missing == [
        "network", "microphone", "battery", "post_notifications", "on_screen",
    ], missing


def test_essential_and_optional_are_split_the_way_the_docs_claim():
    reqs = {r["id"]: r["essential"] for r in evaluate(all_status(False))}
    assert reqs["network"] and reqs["microphone"] and reqs["battery"]
    # Essential: without it Jarvis cannot show the listening notification, the
    # wake-word alert, or a Tier-3 approval — and an approval that cannot be
    # delivered times out and is denied.
    assert reqs["post_notifications"]
    assert reqs["on_screen"]
    # NOT essential: "display over other apps" makes Jarvis draw the orb
    # directly, which needs no full-screen intent at all. Only one of the two
    # has to be granted, so neither can be the one that fails the checklist.
    assert not reqs["full_screen"]
    assert not reqs["overlay"]
    assert not reqs["assistant"]
    assert not reqs["accessibility"]
    assert not reqs["notifications"]
    assert not reqs["exact_alarms"]


def test_package_scoped_deep_links_are_the_ones_that_need_a_package():
    by_id = {r["id"]: r for r in evaluate(all_status(False))}
    # These screens are per-app and are useless without a package: URI.
    for rid in ("network", "microphone", "battery", "exact_alarms"):
        assert by_id[rid]["needsPackageUri"] is True, rid
    # These are global lists the user picks Jarvis out of.
    for rid in ("assistant", "accessibility", "notifications"):
        assert by_id[rid]["needsPackageUri"] is False, rid


# =========================================================================
# Tests: the Kotlin still says the same thing
# =========================================================================


def test_kotlin_timeline_constants_match():
    assert KOTLIN_TIMELINE.is_file(), f"missing {KOTLIN_TIMELINE}"
    k = kotlin_consts(KOTLIN_TIMELINE)
    expected = {
        "TOTAL_MS": TOTAL_MS,
        "SCAN_START_MS": SCAN_START_MS,
        "IGNITE_START_MS": IGNITE_START_MS,
        "RINGS_START_MS": RINGS_START_MS,
        "WORDMARK_START_MS": WORDMARK_START_MS,
        "CHECKS_START_MS": CHECKS_START_MS,
        "HANDOFF_START_MS": HANDOFF_START_MS,
        "SCAN_FADE_MS": SCAN_FADE_MS,
        "CORE_RISE_MS": CORE_RISE_MS,
        "CORE_FADE_MS": CORE_FADE_MS,
        "FLARE_MS": FLARE_MS,
        "RING_COUNT": RING_COUNT,
        "RING_STAGGER_MS": RING_STAGGER_MS,
        "RING_MS": RING_MS,
        "RING_TENSION": RING_TENSION,
        "LETTER_COUNT": LETTER_COUNT,
        "LETTER_STAGGER_MS": LETTER_STAGGER_MS,
        "LETTER_MS": LETTER_MS,
        "LETTER_SPACING_START": LETTER_SPACING_START,
        "LETTER_SPACING_END": LETTER_SPACING_END,
        "LETTER_BLUR_DP": LETTER_BLUR_DP,
        "CHECK_LINE_COUNT": CHECK_LINE_COUNT,
        "CHECK_STAGGER_MS": CHECK_STAGGER_MS,
        "CHECK_TYPE_MS": CHECK_TYPE_MS,
        "HANDOFF_FADE_MS": HANDOFF_FADE_MS,
        "HOME_FADE_DELAY_MS": HOME_FADE_DELAY_MS,
        "HOME_FADE_MS": HOME_FADE_MS,
        "MAX_DURATION_MS": MAX_DURATION_MS,
    }
    for name, value in expected.items():
        assert name in k, f"BootTimeline.kt no longer defines {name}"
        assert abs(k[name] - value) < 1e-9, (
            f"BootTimeline.{name} is {k[name]}, this spec says {value}"
        )


def test_kotlin_stage_enum_matches_the_partition():
    src = KOTLIN_TIMELINE.read_text()
    body = src.split("enum class Stage(", 1)[1]
    found = re.findall(r"\b([A-Z]+)\(([A-Z_]+),\s*([A-Z_]+)\)", body)
    names = [f[0] for f in found if f[0] in {s[0] for s in STAGES}]
    assert names == [s[0] for s in STAGES], f"Stage order is {names}"


def test_kotlin_timeline_still_encodes_the_rules():
    src = flat(KOTLIN_TIMELINE)
    required = [
        "if (durationMs <= 0L) return if (tMs >= startMs) 1f else 0f",
        "fun shouldSkip(animatorScale: Float, reducedMotion: Boolean): Boolean = "
        "reducedMotion || animatorScale <= 0f || animatorScale.isNaN()",
        "if (shouldSkip(animatorScale, reducedMotion)) return 0L",
        "if (scaled > MAX_DURATION_MS) MAX_DURATION_MS else scaled",
        "fun endState(): EndState = stateAt(TOTAL_MS)",
        "if (actionCount != null && actionCount > 0)",
    ]
    for needle in required:
        assert re.sub(r"\s+", " ", needle) in src, f"BootTimeline.kt no longer contains: {needle}"


def test_kotlin_animation_uses_one_clock_and_cleans_up():
    assert KOTLIN_ANIMATION.is_file(), f"missing {KOTLIN_ANIMATION}"
    src = KOTLIN_ANIMATION.read_text()
    flat_src = flat(KOTLIN_ANIMATION)
    code = code_only(KOTLIN_ANIMATION)

    # One clock. Nested postDelayed chains are the thing this design forbids.
    assert code.count("ValueAnimator.ofFloat") == 1, "the boot must have exactly one animator"
    assert "postDelayed" not in code, "no postDelayed chains — the timeline is one clock"
    assert "Handler(" not in code, "no private Handler — the timeline is one clock"

    # skip() is 'set the clock to the end', not a second code path.
    assert "timeMs = BootTimeline.TOTAL_MS" in src

    # Full cleanup on detach.
    detach = src.split("override fun onDetachedFromWindow()", 1)[1].split("super.onDetachedFromWindow()", 1)[0]
    for needle in (
        "animator.cancel()",
        "animator.removeAllUpdateListeners()",
        "animator.removeAllListeners()",
        "orb?.setBootDrive(null)",
        "onComplete = null",
    ):
        assert needle in detach, f"onDetachedFromWindow does not do: {needle}"

    # The animation scale is read, and read defensively.
    assert "Settings.Global.ANIMATOR_DURATION_SCALE" in src
    assert "BootTimeline.scaledDurationMs(animatorScale(), reducedMotion())" in flat_src


def test_kotlin_hands_the_wordmark_over_without_a_hole():
    """The Kotlin half of `test_the_wordmark_crossfade_has_no_hole_in_it`."""
    timeline = flat(KOTLIN_TIMELINE)
    # Both halves, because "complement" is a claim about the pair: pinning only
    # orbChromeAlpha would let chromeAlpha drift out from under it.
    assert (
        "fun chromeAlpha(tMs: Long): Float = "
        "1f - decelerate(window(tMs, HANDOFF_START_MS, HANDOFF_FADE_MS), 1.2f)"
    ) in timeline, "BootTimeline.chromeAlpha has changed shape"
    assert (
        "fun orbChromeAlpha(tMs: Long): Float = "
        "decelerate(window(tMs, HANDOFF_START_MS, HANDOFF_FADE_MS), 1.2f)"
    ) in timeline, (
        "BootTimeline.orbChromeAlpha must be the exact complement of chromeAlpha"
    )
    # It is part of the frame, so `stateAt` reports it and skip() cannot land on
    # a different value from the one the last animated frame had.
    assert "val orbChromeAlpha: Float," in KOTLIN_TIMELINE.read_text()
    assert "orbChromeAlpha = orbChromeAlpha(tMs)," in KOTLIN_TIMELINE.read_text()

    animation = flat(KOTLIN_ANIMATION)
    assert "drive.chromeAlpha = BootTimeline.orbChromeAlpha(t)" in animation, (
        "the orb's chrome is driven by something other than orbChromeAlpha again; "
        "with homeAlpha the JARVIS wordmark dips to under a third mid-handoff"
    )
    # The HOST still gets homeAlpha — its controls are crossfading with nothing.
    assert "onHomeAlpha?.invoke(BootTimeline.homeAlpha(t))" in animation


def test_the_orb_keeps_a_frame_clock_when_the_animator_scale_is_zero():
    """A frozen orb is what "the animation isn't looped" meant.

    An infinite `ValueAnimator` ends on its FIRST frame when the system animator
    duration scale is 0 — developer options, or a battery saver, both routine on
    GrapheneOS — and `JarvisOrbView` integrates every quantity it draws off that
    one clock. When it dies the view stops calling `invalidate` at all: no
    breathing, no ring rotation, no microphone response, no colour blend.

    So there has to be a second clock the scale cannot reach, and it has to stop
    with the view — a vsync callback still posting itself after a detach is a
    leaked Activity.
    """
    src = KOTLIN_ORB.read_text()
    code = code_only(KOTLIN_ORB)
    flat_src = flat(KOTLIN_ORB)

    assert "import android.view.Choreographer" in src, (
        "JarvisOrbView has no clock the animator duration scale cannot switch off"
    )
    assert "Choreographer.getInstance().postFrameCallback(frameCallback)" in flat_src
    assert "!ValueAnimator.areAnimatorsEnabled()" in code, (
        "nothing asks whether the animator clock will run before starting it"
    )
    # Two ways in, because the scale can also be dropped after the animator is
    # already running: an INFINITE animator that reaches onAnimationEnd died.
    assert "if (clockRunning) startFrameCallback()" in code, (
        "an animator clock that ends while the view still wants one is not "
        "noticed, so a battery saver switching on mid-conversation freezes the orb"
    )

    stop = src.split("private fun stopClock()", 1)[1].split("\n    }", 1)[0]
    for needle in (
        "clockRunning = false",
        "frameAnimator.cancel()",
        "Choreographer.getInstance().removeFrameCallback(frameCallback)",
    ):
        assert needle in stop, f"stopClock does not do: {needle}"
    # Order is load-bearing: cancel() delivers onAnimationEnd, and the listener
    # reads clockRunning to tell a deliberate stop from a death.
    assert stop.index("clockRunning = false") < stop.index("frameAnimator.cancel()"), (
        "stopClock cancels before it clears clockRunning, so stopping the clock "
        "immediately restarts it on the vsync callback"
    )
    detach = src.split("override fun onDetachedFromWindow()", 1)[1].split(
        "super.onDetachedFromWindow()", 1
    )[0]
    assert "stopClock()" in detach, "the frame clock outlives the view"

    # The fallback is on unless the debug-only hooks turn it off, and a release
    # build cannot even ask the question.
    assert "var frameClockFallbackEnabled = true" in code
    assert "!BuildConfig.DEBUG || frameClockFallbackEnabled" in code, (
        "the test seam is readable from a release build"
    )
    hooks = KOTLIN_TEST_HOOKS.read_text()
    assert "JarvisOrbView.frameClockFallbackEnabled = false" in hooks, (
        "nothing holds the orb to its animator clock under instrumentation, so "
        "AppLaunchTest's onView would be matching a view that repaints forever"
    )
    init_block = hooks.split("    init {", 1)[1].split("\n    }", 1)[0]
    assert "holdTheOrbToItsAnimatorClock()" in init_block, (
        "the hold is left to individual tests to remember, which is how the "
        "Espresso suite goes red again"
    )


def test_the_orbs_edge_light_fades_in_with_the_rest_of_its_chrome():
    """Nothing may appear whole on the frame the boot lets go of the orb.

    The edge light is a rounded rectangle traced around the WHOLE view. It was
    suppressed for the entire power-on and then drawn at full strength the
    instant `bootDrive` went null — a box snapping on around the screen while
    everything beside it was still fading up.
    """
    src = KOTLIN_ORB.read_text()
    code = code_only(KOTLIN_ORB)
    assert "if (chromeEnabled) drawEdgeLight(canvas, chromeA)" in code, (
        "the edge light is gated on the boot rather than faded with the chrome"
    )
    body = src.split("private fun drawEdgeLight(canvas: Canvas, chromeA: Float)", 1)[1]
    body = body.split("\n    }", 1)[0]
    assert "* chromeA" in body, "the resting edge light ignores the chrome opacity"


def test_kotlin_animation_reuses_the_orb_rather_than_drawing_its_own():
    src = KOTLIN_ANIMATION.read_text()
    assert "var orb: JarvisOrbView?" in src, "the boot must drive the real orb view"
    assert "orb?.beginBoot()" in src and "orb?.endBoot()" in src
    # It must not paint an opaque lid over the orb it is driving.
    assert "setBackgroundColor(Color.TRANSPARENT)" in src


def test_kotlin_orb_exposes_the_boot_hooks():
    src = KOTLIN_ORB.read_text()
    for needle in (
        "class BootDrive",
        "fun setBootDrive(drive: BootDrive?)",
        "fun beginBoot()",
        "fun endBoot()",
        "fun wordmarkBaselineY()",
    ):
        assert needle in src, f"JarvisOrbView no longer has: {needle}"
    k = kotlin_consts(KOTLIN_ORB)
    # The wordmark the boot resolves in must settle onto the orb's own spacing.
    assert abs(k.get("WORDMARK_SPACING", -1) - LETTER_SPACING_END) < 1e-9, (
        "JarvisOrbView.WORDMARK_SPACING must equal BootTimeline.LETTER_SPACING_END, "
        "or the wordmark jumps at the handoff"
    )

    # The ring identifiers are ReactorOrb's — JarvisOrbView aliases them so
    # BootDrive can size its arrays, and the alias is what stops the two from
    # drifting apart. Both halves are checked: the names still resolve from the
    # view the boot animation talks to, and the values live in one place.
    ring_names = ("RING_INNER_RIM", "RING_MID_DASH", "RING_FINE_DASH", "RING_GAUGE")
    orb_src = KOTLIN_ORB.read_text()
    for name in ring_names + ("RING_COUNT",):
        assert f"const val {name} = ReactorOrb.{name}" in orb_src, (
            f"JarvisOrbView.{name} must alias ReactorOrb.{name}; a second copy of "
            "the ring indices is a second copy that can disagree with the renderer"
        )
    r = kotlin_consts(KOTLIN_REACTOR)
    assert r.get("RING_COUNT") == RING_COUNT, "orb ring count must match the timeline"
    for name in ring_names:
        assert name in r, f"ReactorOrb is missing {name}"
    assert [r[name] for name in ring_names] == [0, 1, 2, 3], (
        "rings must be indexed inner -> outer, the order they materialise in"
    )


def test_orb_guards_the_zero_radius_the_boot_starts_from():
    """The ignition genuinely asks for a radius of 0, and Skia will not have it.

    `RadialGradient` throws outright on a zero radius, and a `DashPathEffect`
    whose intervals sum to zero is undefined. Before the boot animation existed
    the orb never went below 70% scale, so nothing hit this; now frame 0 sits
    exactly on it. Every primitive that builds a shader or a path effect has to
    bail out first.
    """
    assert core_scale(0) == 0.0, "the premise: the boot starts the core at zero"
    src = KOTLIN_REACTOR.read_text()
    assert "MIN_DRAW_PX" in src, "ReactorOrb has no degenerate-geometry guard"
    for fn in (
        "drawSubstrate",
        "drawBlob",
        "drawSpokes",
        "drawCore",
        "drawGlass",
        "drawHalo",
        "drawRim",
        "drawRing",
        "drawDashedRing",
        "drawTicks",
        "drawAnnulusSweep",
    ):
        body = src.split(f"private fun {fn}(", 1)[1][:900]
        assert "MIN_DRAW_PX" in body, f"{fn} does not guard against a sub-pixel radius"
    dashed = src.split("private fun drawDashedRing(", 1)[1][:900]
    assert "if (seg <= 0f) return" in dashed, (
        "a DashPathEffect with zero-length intervals is undefined behaviour"
    )


def test_kotlin_compat_requirements_match_this_table():
    assert KOTLIN_COMPAT.is_file(), f"missing {KOTLIN_COMPAT}"
    src = KOTLIN_COMPAT.read_text()
    block = src.split("fun evaluate(status: Status): List<Requirement>", 1)[1]
    # Each Requirement(...) entry, in source order.
    entries = re.findall(
        r"id = (ID_[A-Z_]+),.*?essential = (true|false),\s*"
        r"settingsAction = ([^,]+),\s*needsPackageUri = (true|false)",
        block,
        re.S,
    )
    assert len(entries) == len(REQUIREMENT_TABLE), (
        f"GrapheneCompat has {len(entries)} requirements, this spec has {len(REQUIREMENT_TABLE)}"
    )
    id_consts = dict(re.findall(r'const val (ID_[A-Z_]+) = "([a-z_]+)"', src))
    for (id_const, essential, action, needs_uri), (
        exp_id,
        exp_essential,
        exp_action_const,
        exp_needs_uri,
    ) in zip(entries, REQUIREMENT_TABLE):
        assert id_consts.get(id_const) == exp_id, (
            f"{id_const} is {id_consts.get(id_const)}, expected {exp_id}"
        )
        assert (essential == "true") == exp_essential, f"{exp_id} essential flag drifted"
        assert (needs_uri == "true") == exp_needs_uri, f"{exp_id} package-uri flag drifted"
        assert exp_action_const in action, (
            f"{exp_id} deep-links to {action.strip()}, expected {exp_action_const}"
        )


def test_kotlin_compat_still_encodes_the_verdict_rules():
    src = flat(KOTLIN_COMPAT)
    required = [
        "if (!permissionGranted) return NetworkVerdict.DENIED",
        "if (securityDenials > 0) return NetworkVerdict.DENIED",
        "if (successes > 0) return NetworkVerdict.GRANTED",
        "if (hostFailures >= SUSPECT_THRESHOLD) return NetworkVerdict.SUSPECT",
        'if (name == "java.lang.SecurityException") return Signal.SECURITY',
        'if (name == "java.net.UnknownHostException") return Signal.HOST',
        "satisfied = status.batteryExempt && !status.batteryRestricted",
    ]
    for needle in required:
        assert re.sub(r"\s+", " ", needle) in src, f"GrapheneCompat.kt no longer contains: {needle}"
    assert f"const val SUSPECT_THRESHOLD = {SUSPECT_THRESHOLD}" in src


def test_kotlin_compat_never_throws():
    """Every public probe has to swallow its own failures."""
    src = KOTLIN_COMPAT.read_text()
    # Split into function bodies that take a Context and touch the platform.
    for fn in (
        "isRestrictedBattery",
        "isIgnoringBatteryOptimizations",
        "hasPermission",
        "hasAssistantRole",
        "hasAccessibilityService",
        "hasNotificationListener",
        "canScheduleExactAlarms",
    ):
        assert f"fun {fn}(" in src, f"GrapheneCompat is missing {fn}"
        body = src.split(f"fun {fn}(", 1)[1][:900]
        assert "catch (t: Throwable)" in body, f"{fn} does not catch Throwable"
    # And every system service is fetched through the nullable overload.
    for call in re.findall(r"getSystemService\(([^)]*)\)", src):
        assert "::class.java" in call, f"unsafe getSystemService cast: {call}"
    assert "!!" not in src, "no !! on platform types in GrapheneCompat"


def test_crash_handler_installs_first_and_always_delegates():
    assert KOTLIN_CRASH.is_file(), f"missing {KOTLIN_CRASH}"
    src = KOTLIN_CRASH.read_text()
    flat_src = flat(KOTLIN_CRASH)
    assert "Thread.setDefaultUncaughtExceptionHandler(this)" in src
    assert "previous = Thread.getDefaultUncaughtExceptionHandler()" in src
    # Record first, delegate second, and the delegate runs in a finally.
    handler = src.split("override fun uncaughtException", 1)[1]
    assert "finally" in handler, "the previous handler must run in a finally block"
    assert "next.uncaughtException(thread, throwable)" in handler
    # Every recorded field the spec asks for.
    for field in ("ts", "thread", "exception", "stack", "app_version",
                  "android_version", "device"):
        assert f'"{field}"' in src, f"crash record is missing {field}"
    # Rotation, so the log cannot grow without bound.
    assert "MAX_RECORDS" in src and "MAX_FILE_BYTES" in src
    assert "crashes.jsonl" in src

    statements = first_statements(KOTLIN_APP, "override fun onCreate()")
    assert statements[0] == "JarvisCrashHandler.install(this)", (
        f"the crash handler must be the first statement in onCreate, not {statements[0]!r}"
    )
    assert statements[1] == "super.onCreate()", (
        "the handler goes in before super.onCreate, so a throw in startup is still caught"
    )
    assert flat_src.count("catch (t: Throwable)") >= 5, (
        "the crash handler must not be able to become the crash"
    )


def test_cold_start_is_tracked_in_the_application_object():
    app = KOTLIN_APP.read_text()
    assert "AtomicBoolean(true)" in app
    assert "fun consumeColdStart(): Boolean = coldStart.getAndSet(false)" in flat(KOTLIN_APP)
    main = KOTLIN_MAIN.read_text()
    assert "JarvisApp.consumeColdStart(this)" in main
    assert "savedInstanceState == null" in main, (
        "a rotation restores savedInstanceState and must not replay the boot"
    )


def test_splash_screen_is_wired_with_no_white_flash():
    main = KOTLIN_MAIN.read_text()
    assert "splashScreen.setOnExitAnimationListener" in main
    assert "Build.VERSION_CODES.S" in main, "the platform SplashScreen API is 31+"
    assert "splashView.remove()" in main

    assert THEMES_V31.is_file(), f"missing {THEMES_V31}"
    themes = THEMES_V31.read_text()
    assert "windowSplashScreenBackground" in themes
    assert "@color/jarvis_bg" in themes
    assert "windowSplashScreenAnimatedIcon" in themes

    colors = (ROOT / "app/src/main/res/values/colors.xml").read_text()
    assert "<color name=\"jarvis_bg\">#FF04070C</color>" in colors, (
        "the splash background must be exactly the HUD ground"
    )


def test_launcher_icon_has_all_three_layers():
    assert ADAPTIVE_ICON.is_file(), f"missing {ADAPTIVE_ICON}"
    icon = ADAPTIVE_ICON.read_text()
    assert "<background" in icon and "<foreground" in icon and "<monochrome" in icon
    mono = re.search(r"<monochrome android:drawable=\"@drawable/([a-z_]+)\"", icon)
    assert mono, "no monochrome drawable"
    assert mono.group(1) != "ic_jarvis_foreground", (
        "the monochrome layer must be its own alpha-only art; the launcher discards "
        "colour, so reusing the cyan foreground flattens into a blob"
    )
    mono_file = ROOT / f"app/src/main/res/drawable/{mono.group(1)}.xml"
    assert mono_file.is_file(), f"missing {mono_file}"
    assert "#3FD8FF" not in mono_file.read_text(), "the monochrome layer must not carry colour"
    status = ROOT / "app/src/main/res/drawable/ic_jarvis_status.xml"
    assert status.is_file(), "no notification icon"


def test_manifest_declares_the_new_screens_and_queries():
    assert MANIFEST.is_file()
    src = MANIFEST.read_text()
    for name in (".ui.SystemCheckActivity", ".ui.CrashLogActivity"):
        assert f'android:name="{name}"' in src, f"{name} is not in the manifest"
        block = src.split(f'android:name="{name}"', 1)[1][:400]
        assert 'android:exported="false"' in block, f"{name} must not be exported"
    assert "<queries>" in src, (
        "intent visibility must be declared with <queries>, not left to QUERY_ALL_PACKAGES"
    )


# =========================================================================
# Tests: the wiring the boot animation and the checklist depend on
# =========================================================================


def test_skip_moves_the_clock_before_it_cancels_the_animator():
    """`Animator.cancel()` sends onAnimationCancel AND THEN onAnimationEnd.

    That is documented platform behaviour and AOSP's `endAnimation()` does it,
    so cancelling re-enters this view's own end listener. Cancel before the
    clock is moved and that re-entrant finish() settles the orb, fades the home
    UI up and detaches the view while `timeMs` is still mid-sequence — leaving
    every statement after the cancel operating on a detached view whose `orb`
    and callbacks the detach had already nulled.
    """
    src = KOTLIN_ANIMATION.read_text()
    body = src.split("fun skip() {", 1)[1].split("\n    }", 1)[0]
    clock = body.index("timeMs = BootTimeline.TOTAL_MS")
    push = body.index("pushFrame()")
    cancel = body.index("animator.cancel()")
    assert clock < cancel, "skip() must set the clock BEFORE cancelling the animator"
    assert push < cancel, "skip() must push the end frame BEFORE cancelling the animator"
    assert body.rindex("finish()") > cancel, (
        "skip() still needs a trailing finish(): an animator that never started "
        "notifies nobody, so nothing else would complete the sequence"
    )
    assert "cancel does not fire onAnimationEnd" not in src, (
        "cancel() DOES fire onAnimationEnd; that comment was wrong"
    )


def test_a_disabled_animation_does_not_wait_for_the_splash():
    """Animations off must not mean a black screen while the splash exits.

    On API 31+ the sequence is started by the splash-exit listener. If it has
    been reduced to its end state there is nothing to hand off to, and waiting
    just holds homeControls at alpha 0 for however long the splash takes.
    """
    animation = KOTLIN_ANIMATION.read_text()
    assert "fun willPlay(): Boolean" in animation
    assert "BootTimeline.shouldSkip(animatorScale(), reducedMotion())" in flat(KOTLIN_ANIMATION)
    main = flat(KOTLIN_MAIN)
    assert "!animation.willPlay()" in main, (
        "MainActivity must start the sequence immediately when it will not play"
    )


def test_the_third_check_line_has_something_writing_its_input():
    """A boot line whose input nothing writes is a line that never appears."""
    animation = KOTLIN_ANIMATION.read_text()
    assert "fun lastActionCount(context: Context): Int?" in animation
    assert "JarvisConfig(context.applicationContext).lastActionCount" in flat(KOTLIN_ANIMATION)

    config = flat(KOTLIN_CONFIG)
    assert "var lastActionCount: Int" in config, "JarvisConfig must own the key"
    assert 'putInt(KEY_ACTION_COUNT' in config, "lastActionCount must have a setter"
    assert 'const val KEY_ACTION_COUNT = "last_action_count"' in config

    channel = flat(KOTLIN_CHANNEL)
    assert "rememberActionCount(tierTable.size)" in channel, (
        "nothing writes the action count, so the third check line can never show"
    )
    assert "JarvisConfig(appContext).lastActionCount = count" in channel

    # And the dead key it replaces is gone for good.
    for path in TRACKED_KOTLIN:
        assert "last_device_count" not in path.read_text(), (
            f"{path.name} still references the key nothing ever wrote"
        )


def test_the_network_verdict_has_call_sites_on_the_wire():
    """SUSPECT is unreachable unless something reports what the wire did.

    Without these the verdict is only ever the permission check, which is the
    one signal GrapheneCompat's own KDoc says cannot be relied on.
    """
    channel = KOTLIN_CHANNEL.read_text()
    code = re.sub(r"/\*.*?\*/", " ", channel, flags=re.S)
    code = re.sub(r"//[^\n]*", " ", code)
    assert "GrapheneCompat.noteNetworkFailure(" in code, (
        "no failure call site: hostFailures can never reach SUSPECT_THRESHOLD"
    )
    assert "GrapheneCompat.noteNetworkSuccess()" in code, (
        "no success call site: suspicion would never clear once raised"
    )
    # The failure site has to be the socket's own failure callback.
    failure = code.split("override fun onFailure(", 1)[1][:900]
    assert "GrapheneCompat.note" in failure, "onFailure must fold into the verdict"


def test_crash_records_are_redacted_before_they_are_stored():
    """The crash screen's whole purpose is a COPY button.

    A stack trace out of OkHttp or a JSON parser routinely quotes the frame or
    the URL that failed, and channel/Redact.kt exists to keep the bearer token
    out of exactly that. It has to be applied on the way in, because the file is
    what the COPY button reads.
    """
    src = KOTLIN_CRASH.read_text()
    assert "import ai.jarvis.app.channel.Redact" in src
    record = src.split("internal fun record(", 1)[1].split("\n    }", 1)[0]
    assert "redact(safe { throwable.message }" in re.sub(r"\s+", " ", record), (
        "the exception message is stored verbatim"
    )
    assert "redact(sw.toString().take(CrashRecord.MAX_STACK_CHARS))" in re.sub(
        r"\s+", " ", record
    ), "the stack trace is stored verbatim"
    # The redactor itself must not be able to become the crash.
    helper = src.split("private fun redact(value: String)", 1)[1][:400]
    assert "catch (t: Throwable)" in helper

    ui = KOTLIN_CRASH_UI.read_text()
    assert "EXTRA_IS_SENSITIVE" in ui, (
        "a copied crash report must not be rendered in the system clipboard preview"
    )


def test_redact_masks_the_shapes_a_stack_trace_carries():
    """Mirror of channel/Redact.kt's two regex families, on real trace text."""
    src = (ROOT / "app/src/main/kotlin/ai/jarvis/app/channel/Redact.kt").read_text()
    keys = re.search(r"SECRET_KEYS = listOf\(([^)]*)\)", src).group(1)
    keys = re.findall(r'"([a-z_]+)"', keys)
    assert "token" in keys and "access_token" in keys and "authorization" in keys

    def redact(value: str) -> str:
        out = value
        for key in keys:
            out = re.sub(
                r'"%s"\s*:\s*"[^"]*"' % re.escape(key), '"%s":"[redacted]"' % key,
                out, flags=re.I,
            )
            out = re.sub(
                r"([?&])%s=[^&\s\"]*" % re.escape(key), r"\g<1>%s=[redacted]" % key,
                out, flags=re.I,
            )
        return out

    frame = 'java.io.IOException: sending {"type":"auth","access_token":"s3cr3t-value"}'
    assert "s3cr3t-value" not in redact(frame)
    url = "java.net.UnknownHostException: GET https://box.local/api?token=abc123&x=1"
    assert "abc123" not in redact(url)
    assert "&x=1" in redact(url), "redaction must not eat the rest of the query"
    plain = "java.lang.IllegalStateException: nothing secret here"
    assert redact(plain) == plain, "redaction must leave ordinary traces alone"


def test_the_splash_theme_extends_the_base_rather_than_restating_it():
    """A same-named style in a qualified folder REPLACES the unqualified one.

    Resource qualifiers select a winner, they never merge. values-v31 must
    therefore inherit the shared items, or every item added to values/themes.xml
    silently stops applying from API 31 up — which is nearly every device.
    """
    base = THEMES.read_text()
    assert '<style name="Theme.JarvisBase"' in base, "no shared base style to inherit from"
    assert '<style name="Theme.Jarvis" parent="@style/Theme.JarvisBase"' in base

    v31 = THEMES_V31.read_text()
    assert '<style name="Theme.Jarvis" parent="@style/Theme.JarvisBase">' in v31, (
        "values-v31 redeclares Theme.Jarvis without inheriting the base"
    )
    for item in ("android:windowBackground", "android:colorAccent", "android:statusBarColor"):
        assert item not in v31, (
            f"values-v31 restates {item}; it must inherit it, or the two drift"
        )


def test_every_notification_uses_this_app_s_own_status_icon():
    """A framework drawable in the status bar reads as some other app."""
    offenders = []
    for path in sorted(KOTLIN_SRC_ROOT.rglob("*.kt")):
        for match in re.finditer(r"setSmallIcon\(([^)]*)\)", path.read_text()):
            if "android.R.drawable" in match.group(1):
                offenders.append(f"{path.name}: {match.group(1).strip()}")
    assert not offenders, "framework notification icons: " + "; ".join(offenders)


def test_settings_reaches_the_diagnostics_screens():
    """The home banner only appears when something is already wrong.

    Someone who wants the checklist before that has to be able to find it.
    """
    src = flat(KOTLIN_SETTINGS)
    assert "ai.jarvis.app.ui.SystemCheckActivity::class.java" in src
    assert "ai.jarvis.app.ui.CrashLogActivity::class.java" in src


# =========================================================================
# Tests: structural checks over the Kotlin this spec tracks
#
# No kotlinc in this container, so these stand in for the compiler on the two
# things that are cheap to check and expensive to get wrong.
# =========================================================================


def strip_kotlin_literals(src: str) -> str:
    """Source with comments, strings and char literals blanked out."""
    out = []
    i = 0
    n = len(src)
    while i < n:
        two = src[i:i + 2]
        if two == "//":
            i = src.find("\n", i)
            if i < 0:
                break
            continue
        if two == "/*":
            depth = 1  # Kotlin block comments nest.
            i += 2
            while i < n and depth:
                if src[i:i + 2] == "/*":
                    depth += 1
                    i += 2
                elif src[i:i + 2] == "*/":
                    depth -= 1
                    i += 2
                else:
                    i += 1
            continue
        if src[i:i + 3] == '"""':
            end = src.find('"""', i + 3)
            i = n if end < 0 else end + 3
            continue
        if src[i] == '"':
            i += 1
            while i < n and src[i] != '"':
                i += 2 if src[i] == "\\" else 1
            i += 1
            continue
        if src[i] == "'":
            i += 1
            while i < n and src[i] != "'":
                i += 2 if src[i] == "\\" else 1
            i += 1
            continue
        out.append(src[i])
        i += 1
    return "".join(out)


def all_kotlin() -> list[Path]:
    files = sorted(KOTLIN_SRC_ROOT.rglob("*.kt"))
    assert len(files) > 50, f"only {len(files)} Kotlin files found; wrong root?"
    return files


def test_tracked_kotlin_is_bracket_balanced():
    for path in TRACKED_KOTLIN:
        assert path.is_file(), f"missing {path}"
    for path in all_kotlin():
        code = strip_kotlin_literals(path.read_text())
        for open_ch, close_ch in (("{", "}"), ("(", ")"), ("[", "]")):
            assert code.count(open_ch) == code.count(close_ch), (
                f"{path.name}: {open_ch}{close_ch} unbalanced "
                f"({code.count(open_ch)} vs {code.count(close_ch)})"
            )


def test_no_kdoc_block_is_orphaned_from_its_declaration():
    """Two KDoc blocks in a row means one of them documents nothing.

    Kotlin accepts it silently — the first block just stops being anybody's
    documentation. It is what you get when a new function is pasted in between
    an existing KDoc and the declaration it belonged to, which is easy to do and
    invisible in a diff that only shows the added lines.
    """
    # A file-header banner sitting above the first declaration's own KDoc is the
    # same shape and is deliberate, so only look past the first declaration.
    first_decl = re.compile(
        r"^(?:@\w[\w.]*\s*)*"
        r"(?:public |internal |private |protected |abstract |open |sealed |data |"
        r"enum |annotation |value |inline |)*"
        r"(?:class|object|interface|fun|typealias)\b",
        re.M,
    )
    orphans = []
    for path in all_kotlin():
        src = path.read_text()
        start = first_decl.search(src)
        if start is None:
            continue
        # `*/` then only blank lines and annotations before the next `/**`.
        for match in re.finditer(r"\*/\s*(?:@[\w.]+(?:\([^)]*\))?\s*)*/\*\*", src):
            if match.start() < start.start():
                continue
            orphans.append(f"{path.relative_to(KOTLIN_SRC_ROOT)}:{src[: match.start()].count(chr(10)) + 1}")
    assert not orphans, "KDoc with no declaration after it: " + "; ".join(orphans)


def test_tracked_kotlin_has_no_unused_imports():
    """Cheap stand-in for the compiler warning, since there is no kotlinc here.

    A name counts as used if it appears in the code OR in a KDoc `[Link]`, which
    is what the Kotlin compiler itself accepts.
    """
    unused = []
    for path in all_kotlin():
        src = path.read_text()
        body = "\n".join(l for l in src.splitlines() if not l.startswith("import "))
        code = strip_kotlin_literals(body)
        links = set(re.findall(r"\[([A-Za-z_][A-Za-z0-9_.]*)", body))
        for line in src.splitlines():
            if not line.startswith("import "):
                continue
            spec = line[len("import "):].strip()
            if spec.endswith("*"):
                continue
            name = spec.split(" as ")[-1].strip() if " as " in spec else spec.split(".")[-1]
            if re.search(r"\b%s\b" % re.escape(name), code):
                continue
            if any(link == name or link.startswith(name + ".") for link in links):
                continue
            unused.append(f"{path.relative_to(KOTLIN_SRC_ROOT)}: {spec}")
    assert not unused, "unused imports: " + "; ".join(unused)


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # a broken check is a failure, not an abort
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")

    combos = 2 ** len(STATUS_FIELDS)
    print(
        f"\n{len(tests) - failures}/{len(tests)} checks passed "
        f"({TOTAL_MS} timeline milliseconds sampled, "
        f"{combos} requirement status combinations)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
