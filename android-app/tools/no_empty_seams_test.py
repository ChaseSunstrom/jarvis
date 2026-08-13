#!/usr/bin/env python3
"""Executable spec: code that is written, documented, tested — and never called.

Six bugs found in one week, all the same shape, none of them visible to any
other kind of test, because in every case **every line of code involved was
correct**:

  * `CompanionSpeechHost` — an interface with an implementation, a KDoc usage
    example, and nothing that ever constructed one. `speechHost` was null for
    the life of the app, so every question Jarvis asked took over the screen
    instead of being asked on it. (`speech_host_test.py`)
  * `MediaButtonGate` — pure logic, unit-tested, mirrored here across all 400
    input combinations, with no caller and no `MediaSession` anywhere in the
    app. No media button event ever reached the process.
    (`media_button_test.py`)
  * `headsetMode` / `headsetButton` / `warmLink` — getters, defaults and a
    documentation page. Nothing in the app ever wrote one of them, and nothing
    read `warmLink` at all.
  * `PolicyStore.panic` — the kill switch. Read by four components, rendered by
    a screen, described in `docs/security.md`, written by nobody.
    (`policy_truth_table_test.py`)
  * The install-result broadcast — committed to an action no receiver filtered
    for, so `STATUS_PENDING_USER_ACTION` was dropped and the updater's install
    prompt never appeared. (`updater_install_test.py`)
  * Nine dangerous permissions — declared, checked for, never requested.
    (`runtime_permissions_test.py`)

Each of those now has a spec of its own that pins the specific fix. This file is
the general one: it looks for the *shape*, so the seventh is caught before it is
reported.

## What it can and cannot see

It is a static reader, not a compiler, and it says so. It cannot see reflection,
it cannot see a slot filled through an interface, and a name that appears in a
comment is not a caller — which is why every check strips comments first. It
catches three mechanical patterns that between them cover all six:

 1. **A global slot with nobody to fill it.** A non-private `var` on an `object`
    is never private state; it is a place for another component to plug in.
 2. **A setting with no writer, or with no reader.** Either half missing makes
    it a preference key with a documentation page.
 3. **A module with an executable spec and no caller.** The most dangerous
    version of all, because the passing spec is what makes it look finished.

Run:  python3 android-app/tools/no_empty_seams_test.py
      python3 -m pytest android-app/tools/no_empty_seams_test.py -q
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
KOTLIN = ANDROID / "app/src/main/kotlin/ai/jarvis/app"
TOOLS = ANDROID / "tools"
MANIFEST = ANDROID / "app/src/main/AndroidManifest.xml"


def code_only(source: str) -> str:
    """Comments are not callers. A KDoc usage example is exactly how
    `CompanionSpeechHost` looked wired for the life of the app."""
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
    return re.sub(r"//[^\n]*", " ", source)


SOURCES: dict[Path, str] = {
    p: code_only(p.read_text(encoding="utf-8")) for p in sorted(KOTLIN.rglob("*.kt"))
}
#: The manifest with its comments stripped, and the component names it really
#: declares.
#:
#: Both matter, and an audit proved why by mutation. The "an Android component
#: is called by the manifest" exemption used to test `name in MANIFEST_TEXT`
#: against the raw XML — so a class merely MENTIONED in a manifest comment was
#: exempted from the caller check. Six spec-named files took that exemption from
#: comment text alone, `ui/PermissionBridge.kt` among them: deleting its only
#: two callers, which reintroduces the whole of W2 and means no dangerous
#: permission is ever requested again, still passed 7/7.
#:
#: Which is this file's own docstring — "a name that appears in a comment is not
#: a caller, which is why every check strips comments first" — being false about
#: the one input it did not strip.
_MANIFEST_RAW = MANIFEST.read_text(encoding="utf-8")
MANIFEST_TEXT = re.sub(r"<!--.*?-->", " ", _MANIFEST_RAW, flags=re.S)

#: Every class the manifest actually declares, from the attribute rather than
#: from a substring: `android:name=".ui.SystemCheckActivity"` -> the last
#: dotted segment. A bare substring search also counts an unused `import` line
#: as a caller, and this build sets no `allWarningsAsErrors`.
MANIFEST_COMPONENTS = {
    value.rsplit(".", 1)[-1]
    for value in re.findall(r'android:name="([^"]+)"', MANIFEST_TEXT)
}


def elsewhere(pattern: str, but_not: Path) -> list[str]:
    """Every file other than [but_not] whose code matches [pattern]."""
    return [
        str(p.relative_to(KOTLIN))
        for p, src in SOURCES.items()
        if p != but_not and re.search(pattern, src)
    ]


# --- 1. global slots --------------------------------------------------------


def object_slots() -> list[tuple[Path, str, str]]:
    """Every non-private `var` declared directly on a top-level `object`.

    That is a precise marker for "somebody else fills this in": nobody makes a
    singleton's mutable field public for their own use. `AutomationBridge
    .dispatcher`, `CompanionMessageHandler.speechHost` and
    `CompanionMessageHandler.sender` are all this shape, and two of the three
    have been empty at some point in this repo's history.
    """
    found = []
    for path, src in SOURCES.items():
        for match in re.finditer(r"^object\s+(\w+)\s*(?::[^\n{]*)?\{", src, re.M):
            body = src[match.end():]
            close = re.search(r"^\}", body, re.M)
            if close:
                body = body[: close.start()]
            # Four spaces: a member of the object itself, not of a nested class.
            for var in re.finditer(r"^ {4}(?:@\w+\s+)*var\s+(\w+)", body, re.M):
                found.append((path, match.group(1), var.group(1)))
    return found


def test_every_global_slot_has_something_that_fills_it():
    unfilled = []
    for path, obj, name in object_slots():
        if elsewhere(rf"\b{obj}\.{name}\s*=[^=]", path):
            continue
        # A slot the declaring file fills through a function of its own is
        # fine — provided somebody outside calls that function. `ActionEnv
        # .jarvisServerHost` is set by `ActionEnv.refreshFromConfig`, which
        # `Builtins.standard` calls.
        setters = [
            fn
            for fn in re.findall(r"fun\s+(\w+)\s*\([^)]*\)[^{]*\{", SOURCES[path])
            if re.search(rf"\b{name}\s*=[^=]", SOURCES[path].split(f"fun {fn}", 1)[-1][:2000])
        ]
        if any(elsewhere(rf"\b{obj}\.{fn}\s*\(", path) for fn in setters):
            continue
        # Or the filler is an Android component co-located with the slot:
        # `NotificationBus` lives in `JarvisNotificationListener.kt` and the
        # listener sets `connected` from its own lifecycle callbacks. The
        # manifest is what "calls" that class, so the write is reachable.
        component = [n for n in top_level_names(SOURCES[path]) if n in MANIFEST_COMPONENTS]
        if component and re.search(rf"\b{obj}\.{name}\s*=[^=]|^\s+{name}\s*=[^=]",
                                   SOURCES[path], re.M):
            continue
        unfilled.append(f"{obj}.{name} ({path.relative_to(KOTLIN)})")
    assert not unfilled, (
        "these are slots for another component to fill, and nothing outside "
        "their own file ever fills them — the CompanionSpeechHost shape, which "
        "made every question Jarvis asked take over the screen: "
        + ", ".join(unfilled)
    )


def test_every_global_slot_is_read_by_somebody():
    """The other half. A slot everything writes and nothing reads is a
    different flavour of the same bug and just as silent."""
    unread = []
    for path, obj, name in object_slots():
        # Read from another file, or from inside the declaring object itself —
        # which is the normal case for a slot the object consults on every call.
        own = SOURCES[path]
        reads_here = len(re.findall(rf"\b{name}\b(?!\s*=[^=])", own)) > 1
        if reads_here or elsewhere(rf"\b{obj}\.{name}\b(?!\s*=[^=])", path):
            continue
        unread.append(f"{obj}.{name} ({path.relative_to(KOTLIN)})")
    assert not unread, f"global slots nothing ever reads: {', '.join(unread)}"


# --- 2. settings ------------------------------------------------------------

CONFIG = KOTLIN / "config/JarvisConfig.kt"
POLICY = KOTLIN / "automation/policy/PolicyStore.kt"

#: Settings whose reader or writer is deliberately somewhere this file cannot
#: see, with the reason. Every entry is a claim somebody has to defend.
SETTING_EXCEPTIONS: dict[str, str] = {
    # Written by the pairing flow and read by the channel through ChannelConfig,
    # which reads the SharedPreferences file directly rather than through
    # JarvisConfig — see channel/ChannelConfig.kt.
    "deviceId": "generated on first read; nothing should ever write it",
    # `wakeInCar`, `wakeAtHome`, `wakingHourStart` and `wakingHourEnd` were all
    # four listed here, with the reason "stored and not applied; no
    # home-presence signal exists — the screen says so". That was true and it
    # was an admission that `WakeWordGate` — a hundred lines of policy with a
    # unit test and a section of the settings screen — had no production caller
    # at all. `assist/WakeListenWatch.kt` is what reads them now, and
    # `wake_listen_gate_test.py` is what stops them going quiet again.
    # Consumed inside the settings screen itself, and legitimately: the switch
    # is read straight into UpdateChecker.check(installed, allowPrerelease),
    # which is the screen's own action rather than a round trip to nowhere.
    "allowPrereleaseUpdates": "read by checkForUpdates() and passed to UpdateChecker.check",
}

#: Screens that show settings.
#:
#: A setting one of these reads to seed a switch and writes back on save is NOT
#: thereby consumed. That round trip is the exact shape of the
#: headsetMode/warmLink bug this file exists for, and the first version of the
#: rule accepted it — an audit demonstrated it by adding a `headsetBoost` switch
#: that no audio code reads and watching the suite stay green.
SETTINGS_SCREENS = ("SettingsActivity.kt", "VoiceIdentityActivity.kt")


def declared_settings(path: Path) -> list[str]:
    src = SOURCES[path]
    return [
        m.group(1)
        for m in re.finditer(r"^ {4}(?:override\s+)?var\s+(\w+)\s*:", src, re.M)
    ]


def test_every_setting_can_be_changed():
    """A setting with a getter, a default and no writer is a documentation page.

    `headsetMode`, `headsetButton` and `warmLink` were all exactly this: the
    earpiece feature was reachable only by editing SharedPreferences by hand.
    """
    orphans = []
    for path in (CONFIG, POLICY):
        for name in declared_settings(path):
            if name in SETTING_EXCEPTIONS:
                continue
            if elsewhere(rf"\.{name}\s*=[^=]", path):
                continue
            orphans.append(f"{name} ({path.relative_to(KOTLIN)})")
    assert not orphans, (
        "nothing in the app ever writes these, so they are whatever their "
        "default is, forever: " + ", ".join(orphans)
    )


def test_every_setting_is_consulted():
    """And the mirror: a switch that changes nothing.

    The reader must be somewhere OTHER than the screen that writes it. A
    settings screen reads a value to seed its switch and writes it back on
    save; that round trip is not a consumer, and counting it as one is exactly
    how `headsetMode` and `warmLink` would have passed — which is the bug this
    file was written for. Anything genuinely screen-only belongs in
    [SETTING_EXCEPTIONS] with the reason, where somebody has to defend it.
    """
    ignored = []
    for path in (CONFIG, POLICY):
        for name in declared_settings(path):
            if name in SETTING_EXCEPTIONS:
                continue
            readers = [
                where
                for where in elsewhere(rf"\.{name}\b(?!\s*=[^=])", path)
                if not where.endswith(SETTINGS_SCREENS)
            ]
            if readers:
                continue
            ignored.append(f"{name} ({path.relative_to(KOTLIN)})")
    assert not ignored, (
        "these are stored and read back only by the screen that stores them, so "
        "setting them does nothing: " + ", ".join(ignored)
    )


# --- 3. modules with a spec and no caller -----------------------------------


def spec_named_sources() -> dict[str, list[Path]]:
    """Kotlin files named by an executable spec in `tools/`.

    A spec is a strong signal that somebody cared about a module's logic, which
    is exactly the population where "and then nothing called it" is most
    dangerous — a green suite is what makes it look done.
    """
    named: dict[str, list[Path]] = {}
    for spec in sorted(TOOLS.glob("*_test.py")):
        text = spec.read_text(encoding="utf-8")
        for match in re.finditer(r"([\w/]+)\.kt", text):
            stem = match.group(1).split("/")[-1]
            for path in SOURCES:
                if path.stem == stem:
                    named.setdefault(spec.name, []).append(path)
    return named


def top_level_names(src: str) -> list[str]:
    return re.findall(
        r"^(?:internal\s+|open\s+|abstract\s+|sealed\s+|data\s+|enum\s+)*"
        r"(?:class|object|interface)\s+([A-Z]\w*)",
        src,
        re.M,
    )


def test_every_module_with_a_spec_has_a_caller():
    """The worst version of the shape, and the one that hid `MediaButtonGate`
    for the life of the app: 400 tested input combinations, a documentation
    page describing the feature as shipped, and nothing in the app that could
    reach it."""
    uncalled = []
    for spec, paths in spec_named_sources().items():
        for path in set(paths):
            names = top_level_names(SOURCES[path])
            if not names:
                continue
            # An Android component is "called" by the manifest — by its
            # `android:name` attribute, never by a mention in a comment.
            if any(name in MANIFEST_COMPONENTS for name in names):
                continue
            if any(elsewhere(rf"\b{name}\b", path) for name in names):
                continue
            uncalled.append(f"{path.relative_to(KOTLIN)} (spec: {spec})")
    assert not uncalled, (
        "these have an executable spec and nothing in the app refers to them "
        "at all, so the spec is proving the behaviour of unreachable code: "
        + ", ".join(sorted(uncalled))
    )


# --- 4. the six, by name ----------------------------------------------------
#
# The rules above are general and therefore approximate. These are the exact
# seams that have already been found empty, pinned individually so a refactor
# cannot quietly re-empty one while the general rules still pass.

KNOWN_SEAMS: list[tuple[str, str, str]] = [
    (
        "CompanionMessageHandler.speechHost",
        r"CompanionMessageHandler\.speechHost\s*=\s*it",
        "a question asked while a conversation is on screen takes over the "
        "screen instead of being asked on it",
    ),
    (
        "AutomationBridge.dispatcher",
        r"AutomationBridge\.dispatcher\s*=",
        "every device_command from the server is answered `unsupported`",
    ),
    (
        "MediaButtonGate.decide",
        r"MediaButtonGate\.decide\(",
        "no headset button press reaches the gate that decides what it means",
    ),
    (
        "PolicyStore.panic",
        r"JarvisAutomationService\.panic\(",
        "the kill switch cannot be pulled, and cannot be released",
    ),
    (
        "JarvisConfig.warmLink",
        r"config\.warmLink\s*&&",
        "warm link is a stored preference that changes nothing",
    ),
    (
        "PermissionGateway",
        r"UiPermissionGateway\(",
        "no dangerous Android permission is ever requested",
    ),
    (
        "PolicyStore.setPolicy",
        r"\.setPolicy\(",
        "the user cannot say which actions may run without approval — the "
        "store, its Tier-3 guard and the whole UserPolicy vocabulary exist "
        "with nothing able to write a value into them",
    ),
    (
        "ApprovalBridge.raised",
        r"ApprovalBridge\.raised\(",
        "nothing can tell whether the consent prompt reached the screen, so a "
        "background activity start the platform silently dropped is "
        "indistinguishable from one the user is reading",
    ),
    (
        # `decide` and not `shouldListen`: the only caller of `shouldListen` is
        # `decide`, in this same file, and this check deliberately excludes the
        # declaring file so a seam cannot certify itself. What has to exist
        # OUTSIDE `WakeWordGate.kt` is somebody asking it. That
        # `decide` still routes through `shouldListen` — rather than
        # reimplementing the policy the unit test covers — is checked by
        # `wake_listen_gate_test.py`.
        "WakeWordGate.decide",
        r"\.decide\(\s*\n?\s*atHome",
        "the always-on battery policy is unwired again: the gate, its four "
        "settings and its whole section of the settings screen go back to "
        "being a documentation page, and DEVIATIONS.md's claim that the car-BT "
        "policy turns detection on for the drive and off afterwards goes back "
        "to being false",
    ),
    (
        "WakeListenWatch",
        r"WakeListenWatch\(this, config\)",
        "nothing gathers the signals the gate needs, so the gate has nothing "
        "to decide from even if something asks it",
    ),
    (
        "ConversationRegistry",
        r"ConversationRegistry\.current\(",
        "every surface starts a conversation of its own again — a text turn "
        "drops the voice turn, the wake orb and the assist card lose each "
        "other, and the conversation_id the server hands this device for "
        "`companion.handoff` is dropped on the floor",
    ),
    (
        "TurnFocus",
        r"TurnFocus\(context\)",
        "no part of this app requests audio focus, so Jarvis talks over the "
        "user's music and is never told when a call takes the audio mid-turn",
    ),
    (
        "CallGuard",
        r"CallGuard\(this\)",
        "a call is discovered only by failing to open the recorder, and "
        "hanging up is an edge nobody is watching — so the phone stays deaf "
        "until a backoff or the quarter-hourly alarm happens to land",
    ),
]


#: Declaration headers, blanked before the patterns above are matched.
#:
#: `class UiPermissionGateway(context: Context)` otherwise satisfies a search
#: for `UiPermissionGateway(` all by itself — a seam proving itself filled by
#: existing, which is the exact confusion this file was written about.
_DECL = re.compile(r"\b(?:class|object|interface)\s+\w+")


def test_the_seams_that_have_already_been_found_empty_are_still_filled():
    # Excluding the file that DECLARES the seam's target, so a seam cannot
    # certify itself from an uncalled factory of its own — which is what
    # "pinned individually" has to mean to be worth anything.
    empty = []
    for name, pattern, consequence in KNOWN_SEAMS:
        target = name.split(".")[0]
        filled = False
        for path, src in SOURCES.items():
            if target in top_level_names(src):
                continue
            if re.search(pattern, _DECL.sub("KOTLIN_DECLARATION", src)):
                filled = True
                break
        if not filled:
            empty.append(f"{name} — {consequence}")
    assert not empty, "these seams are empty again: " + "; ".join(empty)


def test_the_install_result_broadcast_has_a_receiver():
    """Not a Kotlin reference at all: a string that has to match between a
    `commit()` and an `<intent-filter>`. Nothing else in this file would see it
    go missing."""
    # Actions are named by constant far more often than by literal — the
    # install broadcast is `setAction(ACTION_INSTALL_RESULT)` — so resolve the
    # constants first. A check that only saw literals would have watched this
    # exact bug go past.
    consts: dict[str, str] = {}
    for src in SOURCES.values():
        consts.update(re.findall(r'const val (\w+) = "(ai\.jarvis\.app[\w.]+)"', src))

    actions = set()
    for src in SOURCES.values():
        for ref in re.findall(r'(?:setAction|Intent)\(\s*"?([\w.]+)"?\s*\)', src):
            actions.add(consts.get(ref, ref))
        actions.update(re.findall(r'setAction\((\w+)\)', src))
    actions = {consts.get(a, a) for a in actions}
    filters = set(re.findall(r'<action android:name="([\w.]+)"\s*/>', MANIFEST_TEXT))
    # Only our own actions; a system action is filtered for by the system.
    ours = {a for a in actions if a.startswith("ai.jarvis.app")}
    # Handled somewhere, by literal or — far more often — by the constant that
    # names it: a Service comparing `intent.action` in a `when`, a receiver
    # branching on it. Those need no manifest filter, because the intent that
    # carries them names its component. Only a genuine broadcast does.
    by_name = {v: k for k, v in consts.items()}
    handled_in_code = set()
    for action in ours:
        needles = [re.escape(action)]
        if action in by_name:
            needles.append(rf"\b{by_name[action]}\b")
        for needle in needles:
            if any(
                re.search(rf'"?{needle}"?\s*(?:->|==|!=)', src)
                or re.search(rf'[=!]=\s*(?:\w+\.)?"?{needle}"?', src)
                for src in SOURCES.values()
            ):
                handled_in_code.add(action)
                break
    orphans = sorted(ours - filters - handled_in_code)
    assert not orphans, (
        "these actions are sent and nothing filters for them, so the broadcast "
        "goes nowhere: " + ", ".join(orphans)
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
    slots = object_slots()
    settings = len(declared_settings(CONFIG)) + len(declared_settings(POLICY))
    print(
        f"\n{len(tests) - failures}/{len(tests)} checks passed "
        f"({len(SOURCES)} Kotlin files, {len(slots)} global slots, "
        f"{settings} settings, {len(KNOWN_SEAMS)} named seams)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
