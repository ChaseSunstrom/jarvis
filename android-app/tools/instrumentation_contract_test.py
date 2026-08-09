#!/usr/bin/env python3
"""Executable spec for the instrumented (on-device) test suite.

`app/src/androidTest` runs only on an emulator, which no CI job outside
`.github/workflows/e2e.yml` has, and which this container does not have at all.
That leaves a whole suite whose assumptions about the app can rot silently for
weeks: a button renamed, a toast reworded, an error written to a different view.
The instrumented test then either fails thirty minutes into an emulator job for
a reason that has nothing to do with the change, or — much worse — keeps passing
while asserting something the app can no longer produce.

This file is the cheap half of that, runnable in the fast lane on every push. It
checks the *contracts between* the instrumented suite and the app, statically:

  1. Every literal the suite expects to find on screen exists in the shipping
     source. A test that greps for "TAP TO SPEAK" is worthless the moment the
     button says something else, and the failure it produces on an emulator does
     not say so.
  2. `MainActivity.onError` still writes the view `ConversationE2ETest` reads.
     This is a REGRESSION GUARD: that test used to assert the orb was not in its
     ERROR state by reading the talk button's label, which `onError` never
     touches and `onMode` never sets to "ERROR" — so the assertion could not
     fail on any build. The error sink has to stay a view a test can read.
  3. The debug-only test hooks stay debug-only, stay unable to answer a consent
     prompt, and stay out of the release artefact.
  4. The debug network-security config remains a superset of the shipping one.
     The two files say "KEEP IN STEP" in a comment, which is not a mechanism.
  5. Every instrumented test class carries `JarvisTestRule`, so the state reset
     and the background-crash check apply to all of them rather than to whoever
     remembered.

None of this replaces running the suite on a device. It catches the failures
that are knowable without one, thirty minutes earlier.

Known limit of check 1, stated so nobody over-trusts it: it asks whether the
text exists ANYWHERE a screen could render it, not whether it is still on the
screen the test drives. "SYSTEM CHECK" labels both a Settings button and the
title of the screen it opens, so renaming only the button still passes. Proved
by the mutation set in the review notes — 14 of 15 deliberate breakages are
caught, and that is the one that is not.

Run:  python3 android-app/tools/instrumentation_contract_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
MAIN = APP / "src" / "main"
DEBUG = APP / "src" / "debug"
ANDROID_TEST = APP / "src" / "androidTest" / "kotlin" / "ai" / "jarvis" / "app"
BUILD_GRADLE = APP / "build.gradle.kts"

MAIN_KOTLIN = MAIN / "kotlin"

TEST_PACKAGE = "ai.jarvis.app.testing"


def kotlin_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.kt"))


def read(path: Path) -> str:
    assert path.is_file(), f"missing {path}"
    return path.read_text(encoding="utf-8")


def main_sources() -> str:
    """Every shipping Kotlin file, concatenated."""
    return "\n".join(read(p) for p in kotlin_files(MAIN_KOTLIN))


def instrumented_test_files() -> list[Path]:
    """The instrumented test CLASSES, not their support code."""
    return sorted(p for p in ANDROID_TEST.glob("*.kt"))


def code_only(src: str) -> str:
    """Kotlin with comment lines removed.

    Line-based on purpose: a real tokeniser would be the wrong amount of
    machinery here, and every comment this needs to drop is either a `//` line
    or a KDoc line starting with `*`. A trailing comment on a code line is left
    alone, which can only ever make a check MORE conservative.
    """
    kept = []
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("//", "*", "/*", "*/")):
            continue
        kept.append(line)
    return "\n".join(kept)


# --- 1. every on-screen literal the suite expects still exists --------------

# Call sites whose FIRST string argument is "text I expect the app to render",
# and HOW the suite matches it — which is the same distinction UiAutomator makes
# and therefore the right one to check against:
#
#   exact    `By.text(textIgnoringCase(x))` matches a WHOLE accessibility node.
#            The app must contain that exact string literal.
#   contains `By.text(containingIgnoringCase(x))`, and the toast helpers, match
#            a substring. The app must contain a literal that includes it.
#
# Deliberately narrow: a blanket scan of every literal in the suite would sweep
# up failure messages, JSON payloads and the REMEMBER_WORDS deny-list — which
# must NOT appear in the app — and the check would be noise.
ONSCREEN_CALLS = {
    "assertOnScreen": "contains",
    "findByText": "exact",
    "tap": "exact",
    "findButton": "exact",
    "textIgnoringCase": "exact",
    "containingIgnoringCase": "contains",
    "Toasts.expect": "contains",
    "Toasts.observe": "contains",
}

# `Toasts.expectAnyOf` takes a vararg of alternatives; all of them are app text,
# and it matches by substring.
VARARG_CALLS = {"Toasts.expectAnyOf": "contains"}

# Literal lists that are iterated as UI labels and looked up with an exact
# matcher. Named explicitly because a general "any listOf() in a test file" rule
# would also match ERROR_MARKERS and REMEMBER_WORDS, whose whole point is that
# they are not app labels.
LABEL_LISTS = (
    re.compile(r"for\s*\(\s*label\s+in\s+listOf\((?P<body>[^)]*)\)", re.S),
    re.compile(r"SYSTEM_ACCESS_BUTTONS\s*=\s*listOf\((?P<body>[^)]*)\)", re.S),
)

STRING_LITERAL = re.compile(r'"((?:[^"\\\n]|\\.)*)"')


def _balanced_arg_slice(src: str, open_paren: int) -> str:
    """The text between `(` at [open_paren] and its matching `)`."""
    depth = 0
    for i in range(open_paren, len(src)):
        ch = src[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return src[open_paren + 1 : i]
    return src[open_paren + 1 :]


def _call_sites(src: str, name: str):
    """Every `name(` that is a CALL, not a declaration."""
    for m in re.finditer(rf"(?<![A-Za-z0-9_]){re.escape(name)}\s*\(", src):
        line_start = src.rfind("\n", 0, m.start()) + 1
        prefix = src[line_start : m.start()]
        if re.search(r"\bfun\s+$", prefix):
            continue  # the declaration of the helper itself
        yield _balanced_arg_slice(src, m.end() - 1)


def expected_onscreen_literals() -> dict[str, tuple[set[str], set[str]]]:
    """{literal: (match modes, files that expect it)} across the suite."""
    wanted: dict[str, tuple[set[str], set[str]]] = {}

    def add(literal: str, mode: str, path: Path) -> None:
        if not literal or "$" in literal or "\\" in literal:
            # Interpolated or escaped: not a literal the app can be grepped for.
            return
        modes, files = wanted.setdefault(literal, (set(), set()))
        modes.add(mode)
        files.add(path.name)

    for path in instrumented_test_files():
        src = read(path)
        for name, mode in ONSCREEN_CALLS.items():
            for args in _call_sites(src, name):
                # Only when the expected text is the FIRST argument. Every one
                # of these helpers takes `(text, why)`, and `why` is a message
                # for a human reading a CI log — not something the app renders.
                if not args.lstrip().startswith('"'):
                    continue
                first = STRING_LITERAL.search(args)
                if first:
                    add(first.group(1), mode, path)
        for name, mode in VARARG_CALLS.items():
            for args in _call_sites(src, name):
                if not args.lstrip().startswith('"'):
                    continue
                for m in STRING_LITERAL.finditer(args):
                    add(m.group(1), mode, path)
        for pattern in LABEL_LISTS:
            for m in pattern.finditer(src):
                for lit in STRING_LITERAL.finditer(m.group("body")):
                    add(lit.group(1), "exact", path)
    return wanted


# Sub-packages of ai.jarvis.app that build a screen. Everything else is
# protocol, policy or plumbing, and a string literal there is a wire value, not
# something a user reads. Splitting them is what makes the check able to notice
# a renamed DENY button: `automation/policy/ActionTier.kt` parses the wire word
# "DENY", so a haystack of the WHOLE app would report the button as still
# present after somebody renamed it to "REFUSE".
UI_SUBPACKAGES = {"ui", "companion"}
NON_UI_SUBPACKAGES = {
    "assist",
    "audio",
    "automation",
    "channel",
    "compat",
    "config",
    "crash",
}


def test_every_shipping_subpackage_is_classified_as_ui_or_not():
    """A new package must be sorted into one list or the other, deliberately.

    Otherwise the haystack below silently stops covering a screen — and this
    check's failure mode is a false PASS, which is the worst kind.
    """
    root = MAIN_KOTLIN / "ai" / "jarvis" / "app"
    found = {p.name for p in root.iterdir() if p.is_dir()}
    unclassified = found - UI_SUBPACKAGES - NON_UI_SUBPACKAGES
    assert not unclassified, (
        f"new package(s) under ai.jarvis.app: {sorted(unclassified)}. Add each "
        "to UI_SUBPACKAGES (it draws a screen) or NON_UI_SUBPACKAGES (it does "
        "not), so the on-screen literal check knows where to look."
    )
    gone = (UI_SUBPACKAGES | NON_UI_SUBPACKAGES) - found
    assert not gone, (
        f"these packages are classified but no longer exist: {sorted(gone)}. A "
        "stale UI entry means the haystack silently lost a screen."
    )


def ui_kotlin_files() -> list[Path]:
    """Activities in the root package, plus `ui/`, `companion/` — and anything
    not yet classified.

    Including the unclassified is deliberate. A package added by somebody else
    should produce exactly ONE failure — "classify this" — and not also a
    second, confusing one from the literal check because a label it owns went
    missing from the haystack. Erring toward a larger haystack costs precision,
    never a false failure.
    """
    root = MAIN_KOTLIN / "ai" / "jarvis" / "app"
    known = UI_SUBPACKAGES | NON_UI_SUBPACKAGES
    files = [p for p in sorted(root.glob("*.kt"))]
    for package in sorted(p.name for p in root.iterdir() if p.is_dir()):
        if package in UI_SUBPACKAGES or package not in known:
            files += kotlin_files(root / package)
    return files


def shipping_string_literals() -> list[str]:
    """The CONTENTS of every string literal in the screen-building Kotlin.

    Deliberately literals rather than raw source text. Half the labels this
    suite looks for are short, generic tokens — "DENY", "RAW", "SETTINGS" — and
    those appear all over the source as identifiers (`JarvisUi.DENY`,
    `Decision.DENY`, `SettingsActivity`). Searching raw text would report a
    renamed button as still present, which is the exact rot this check exists to
    catch. Only a string literal can end up on screen.
    """
    out: list[str] = []
    for path in ui_kotlin_files():
        for m in STRING_LITERAL.finditer(code_only(read(path))):
            out.append(m.group(1))
    return out


def test_every_expected_onscreen_literal_exists_in_the_shipping_app():
    """A test that greps for text the app never renders proves nothing."""
    wanted = expected_onscreen_literals()
    assert len(wanted) >= 15, (
        "the extractor found only "
        f"{len(wanted)} on-screen literals across {len(instrumented_test_files())} test "
        "classes, which means it stopped matching the suite rather than that "
        "the suite stopped asserting"
    )
    haystack = shipping_string_literals()
    exact = set(haystack)
    missing = {}
    for literal, (modes, files) in wanted.items():
        # "exact" is the stricter claim, so when the suite makes it anywhere it
        # is the one that has to hold: `By.text(quote(x))` matches a whole node,
        # and "DENY" being a substring of "AUTO-DENY IN 42s" would not find the
        # button.
        satisfied = (
            literal in exact
            if "exact" in modes
            else any(literal in s for s in haystack)
        )
        if not satisfied:
            missing[literal] = (sorted(modes), sorted(files))
    assert not missing, (
        "the instrumented suite waits for text no shipping string literal "
        "provides, so those assertions can only time out on a device:\n"
        + "\n".join(
            f"  {lit!r} ({'/'.join(modes)} match) expected by {', '.join(files)}"
            for lit, (modes, files) in sorted(missing.items())
        )
    )


def test_the_extractor_actually_sees_the_security_critical_strings():
    """Guards the check above against silently matching nothing."""
    wanted = expected_onscreen_literals()
    for literal in ("TIER 3", "DENY", "RAW", "not remembered", "JARVIS ASKS", "NOT NOW"):
        assert literal in wanted, (
            f"{literal!r} is asserted by the instrumented suite but the "
            "extractor did not pick it up; the patterns in ONSCREEN_CALLS have "
            "drifted from how the tests are written"
        )


# --- 2. the error sink ConversationE2ETest reads ----------------------------


def test_main_activity_writes_errors_to_a_view_a_test_can_read():
    """`onError` must keep writing `responseView`.

    REGRESSION GUARD. `JarvisOrbView` paints its caption onto a Canvas and
    exposes no getter, so the only readable evidence that the pipeline failed is
    the text `MainActivity.onError` puts on screen. If that moves to somewhere
    with no accessibility node and no view, every "…and it was not an error"
    assertion in ConversationE2ETest becomes unfalsifiable without anybody
    editing a test.
    """
    src = read(MAIN_KOTLIN / "ai" / "jarvis" / "app" / "MainActivity.kt")
    body = src.split("override fun onError(", 1)
    assert len(body) == 2, "MainActivity no longer overrides onError"
    tail = body[1][:400]
    assert "responseView.text" in tail, (
        "MainActivity.onError must write responseView — ConversationE2ETest "
        "reads that view to prove the turn was not an error. Found instead:\n"
        + tail.split("\n\n", 1)[0]
    )


def test_the_talk_button_label_is_never_an_error_state():
    """The reason the test may not assert ERROR against the button label.

    `onMode` is the only writer of the talk button's text, and it is called with
    a closed set of labels. None of them is an error state, so `label.contains(
    "ERROR")` is a check that cannot fail. Written down here so that if somebody
    ever DOES route an error through `onMode`, this test fails and points at the
    assertion that could then legitimately be reinstated.
    """
    convo = read(
        MAIN_KOTLIN / "ai" / "jarvis" / "app" / "assist" / "JarvisConversation.kt"
    )
    labels = set(re.findall(r'ui\.onMode\([^,]+,\s*"([^"]*)"\)', convo))
    assert labels, "no ui.onMode(...) call sites found in JarvisConversation"
    assert "ERROR" not in labels, (
        "JarvisConversation now reports an ERROR mode, so MainActivity's talk "
        "button CAN read ERROR and ConversationE2ETest may assert against it "
        f"again. Labels: {sorted(labels)}"
    )

    e2e = read(ANDROID_TEST / "ConversationE2ETest.kt")
    assert "stateLabel" not in e2e, (
        "ConversationE2ETest reads the talk button's label again. While "
        f"onMode only ever reports {sorted(labels)}, an ERROR assertion against "
        "that label is vacuous — assert against responseView instead."
    )


# --- 3. the debug-only hooks stay debug-only --------------------------------


def test_no_shipping_source_references_the_test_hooks():
    """`src/main` must have no path to `ai.jarvis.app.testing`.

    The seam in `assist/MicStreamer.kt` is a `var` the hooks WRITE; main must
    never name the package that writes it, or the hooks would be reachable from
    a release build regardless of which source set they are compiled in.
    """
    offenders = []
    for path in kotlin_files(MAIN_KOTLIN):
        # Comments stripped: `assist/MicStreamer.kt` names the package in its
        # KDoc to say who writes its test seam and why, which is documentation
        # of the boundary rather than a hole in it.
        text = code_only(read(path))
        if TEST_PACKAGE in text or "ai/jarvis/app/testing" in text:
            offenders.append(str(path.relative_to(APP)))
    assert not offenders, (
        f"shipping sources reference {TEST_PACKAGE}: {offenders}. "
        "The test hooks exist only in src/debug and must stay unreachable."
    )


def test_the_only_debug_manifest_components_are_test_hooks():
    """Anything in the debug manifest is invisible to review of the real one."""
    manifest = read(DEBUG / "AndroidManifest.xml")
    declared = re.findall(r'android:name="([^"]+)"', manifest)
    components = [n for n in declared if n.startswith("ai.jarvis.app")]
    assert components, "the debug manifest declares nothing; is it still needed?"
    for name in components:
        assert name.startswith(TEST_PACKAGE + "."), (
            f"the debug manifest declares {name}, which is not a test hook. A "
            "component that exists only in debug builds is a component nobody "
            "reviews against the shipping manifest."
        )
    assert 'android:exported="false"' in manifest, (
        "a debug-only component must not be exported"
    )


def test_the_hooks_cannot_answer_or_weaken_a_consent_prompt():
    """The one property that makes TestHooks acceptable at all.

    A hook that could approve a Tier-3 action would make ConsentGateTest a test
    of the hook rather than of the gate. Checked structurally rather than
    trusted to a comment: the file must not name the approval bridge, must not
    write the policy store, and must not touch the kill switches or the arming
    delay.
    """
    src = read(DEBUG / "kotlin" / "ai" / "jarvis" / "app" / "testing" / "TestHooks.kt")
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith(("*", "/*", "//"))
    )
    forbidden = {
        "ApprovalBridge": "could answer a consent prompt",
        "ApprovalActivity": "could drive the consent screen directly",
        "ApprovalVerdict": "could manufacture an approval",
        "ALLOW_ALWAYS": "could remember an answer",
        ".remember(": "could write a standing policy",
        "setPolicy": "could write a standing policy",
        "automationEnabled": "could flip the master kill switch",
        "panic": "could flip the panic switch",
        "ARM_MS": "could shorten the tap-jacking arming delay",
    }
    found = {token: why for token, why in forbidden.items() if token in code}
    assert not found, (
        "TestHooks gained a way to weaken the consent gate: "
        + "; ".join(f"{t} ({w})" for t, w in sorted(found.items()))
    )
    # …and the observation hook is still there, still read-only. `.all()` is
    # PolicyStore's only reader; a hook that grew a writer would have tripped
    # `setPolicy` / `.remember(` above, and one that lost its reader would make
    # ConsentGateTest's "no standing answer was stored" assertion untestable.
    assert re.search(r"PolicyStore\([^)]*\)\s*\n?\s*\.all\(\)", code), (
        "TestHooks no longer reads the policy store through PolicyStore.all(); "
        "ConsentGateTest depends on that to prove a Tier-3 answer was not "
        "remembered"
    )


def test_the_microphone_seam_is_read_only_behind_a_compile_time_constant():
    """`MicStreamer.debugPcmSource` — the one seam in shipping code.

    `assertNoTestHooksInRelease` cannot cover this: the seam lives in
    `ai.jarvis.app.assist`, not in `ai.jarvis.app.testing`, so it is in the
    release APK by construction. What makes it safe is narrower and worth
    pinning down, because both halves are one careless edit away from being
    untrue:

      1. It is read exactly once, inside `if (BuildConfig.DEBUG)`. `DEBUG` is a
         `static final boolean`, so in a release build kotlinc constant-folds
         the branch away and no release code path reads the field at all —
         verified against the bytecode: release `start()` is `aconst_null;
         astore_1` where the read would be. (R8 is OFF for release in this
         build, so the *members* do ship; what makes them harmless is that
         nothing reads them, not that they were stripped.)
      2. It is written only from `src/debug`. A second writer in `src/main`
         would be a live, reachable audio-injection point in the shipping app.
    """
    mic = MAIN_KOTLIN / "ai" / "jarvis" / "app" / "assist" / "MicStreamer.kt"
    code = code_only(read(mic))
    reads = [
        line.strip()
        for line in code.splitlines()
        if "debugPcmSource" in line and "var debugPcmSource" not in line
    ]
    assert len(reads) == 1, (
        f"MicStreamer reads/writes debugPcmSource on {len(reads)} lines; there "
        f"must be exactly one, guarded read. Lines: {reads}"
    )
    assert "BuildConfig.DEBUG" in reads[0], (
        "the debugPcmSource read is no longer guarded by BuildConfig.DEBUG, so "
        f"a release build can take audio from it: {reads[0]!r}"
    )
    assert re.search(r"if\s*\(\s*BuildConfig\.DEBUG\s*\)", reads[0]), (
        f"the guard is not a plain `if (BuildConfig.DEBUG)`, so kotlinc may not "
        f"constant-fold it out of a release build: {reads[0]!r}"
    )

    # Only src/debug may write it.
    writers = []
    for root in (MAIN_KOTLIN, APP / "src" / "androidTest" / "kotlin"):
        for path in kotlin_files(root):
            for n, line in enumerate(code_only(read(path)).splitlines(), 1):
                if re.search(r"debugPcmSource\s*=", line):
                    writers.append(f"{path.relative_to(APP)}:{n}")
    assert not writers, (
        "debugPcmSource is written outside src/debug — a reachable "
        f"audio-injection point in the shipping app: {writers}"
    )


def test_the_release_leak_guard_is_wired_and_looks_in_the_right_places():
    """The build must fail if the hooks ever reach a release artefact."""
    gradle = read(BUILD_GRADLE)
    assert "assertNoTestHooksInRelease" in gradle, "the release-leak guard is gone"

    # Against the WIRING, not against the file: `bundleRelease` also appears in
    # the task's own KDoc, and a check that merely greps the file would call the
    # guard wired after somebody removed it from the predicate.
    wiring = re.search(
        r"tasks\.matching\s*\{(?P<predicate>.*?)\}\s*\.configureEach\s*\{(?P<body>.*?)\}",
        gradle,
        re.S,
    )
    assert wiring, "assertNoTestHooksInRelease is no longer wired with tasks.matching { … }"
    assert "finalizedBy(assertNoTestHooksInRelease)" in wiring.group("body"), (
        "the guard is defined but the matched tasks do not run it"
    )
    for task in ("assembleRelease", "bundleRelease"):
        assert task in wiring.group("predicate"), (
            f"the release-leak guard does not match {task}; an APK-only check "
            f"misses an app bundle entirely. Predicate: {wiring.group('predicate').strip()}"
        )
    assert "Charsets.UTF_16LE" in gradle, (
        "the guard searches UTF-8 only. A binary AndroidManifest.xml stores "
        "component names as UTF-16, so a debug-only <activity> moved into the "
        "main manifest would pass a UTF-8-only scan."
    )
    assert 'ai/jarvis/app/testing/' in gradle, "the DEX descriptor needle is gone"
    assert "finalizedBy" in gradle, "the guard is defined but never runs"


# --- 4. the debug network-security config is a superset ---------------------

DOMAIN = re.compile(r"<domain[^>]*>([^<]+)</domain>")


def test_debug_network_config_is_a_superset_of_the_shipping_one():
    """The two files say KEEP IN STEP in a comment. This is the mechanism.

    Resource qualifiers select a winner, they do not merge, so the debug config
    REPLACES the shipping one wholesale. A host added to the shipping file and
    not to the debug copy produces the most confusing possible symptom: a debug
    build that refuses a connection the release build allows.
    """
    ship = read(MAIN / "res" / "xml" / "network_security_config.xml")
    dbg = read(DEBUG / "res" / "xml" / "network_security_config.xml")

    ship_domains = {d.strip() for d in DOMAIN.findall(ship)}
    dbg_domains = {d.strip() for d in DOMAIN.findall(dbg)}
    assert ship_domains, "the shipping config lists no domains"
    missing = ship_domains - dbg_domains
    assert not missing, (
        f"src/debug's network_security_config.xml is missing {sorted(missing)}, "
        "which the shipping config permits. Add them there too."
    )

    # The extras exist for the emulator and nothing else.
    extra = dbg_domains - ship_domains
    assert extra <= {"10.0.2.2", "10.0.2.15", "10.0.2.16"}, (
        f"the debug config relaxes cleartext for {sorted(extra - {'10.0.2.2', '10.0.2.15', '10.0.2.16'})}, "
        "which is not an emulator address. A debug exemption is still an "
        "exemption somebody has to justify."
    )

    # Neither variant may open the base config up.
    for name, text in (("main", ship), ("debug", dbg)):
        assert 'cleartextTrafficPermitted="false"' in text.split("<domain-config", 1)[0], (
            f"the {name} base-config no longer denies cleartext by default"
        )


def test_the_emulator_host_alias_is_reachable_by_the_default_harness_url():
    """`Harness.DEFAULT_URL` must be a host the debug config permits.

    The instrumented suite's default is the emulator's host alias. If that were
    not on the debug cleartext list, `ConversationE2ETest` would fail inside
    OkHttp with a platform error rather than with the harness's own message.
    """
    harness = read(ANDROID_TEST / "support" / "Harness.kt")
    m = re.search(r'DEFAULT_URL\s*=\s*"http://([^:/"]+)', harness)
    assert m, "Harness.DEFAULT_URL is no longer a plain http:// URL"
    host = m.group(1)
    dbg = read(DEBUG / "res" / "xml" / "network_security_config.xml")
    assert host in {d.strip() for d in DOMAIN.findall(dbg)}, (
        f"Harness.DEFAULT_URL points at {host}, which the debug "
        "network-security config does not permit cleartext to"
    )


# --- 5. every instrumented class gets the reset and the crash check ---------


def test_every_instrumented_test_class_uses_the_shared_rule():
    """`JarvisTestRule` is the state reset AND the background-crash assertion.

    A class without it inherits whatever the previous class left behind — a
    configured server, a policy entry, an audit log — and a crash on the
    WebSocket reader thread during its tests goes unreported.
    """
    offenders = []
    for path in instrumented_test_files():
        src = read(path)
        if "JarvisTestRule()" not in src or "@get:Rule" not in src:
            offenders.append(path.name)
    assert not offenders, (
        f"instrumented test classes without @get:Rule JarvisTestRule(): {offenders}"
    )


def test_every_instrumented_test_class_captures_at_least_one_screenshot():
    """Capturing the UI is half of why this suite exists.

    A CI run whose artefact directory is empty cannot tell you whether the app
    looked right, only that some assertions passed.
    """
    offenders = [
        p.name for p in instrumented_test_files() if "Screenshots.take" not in read(p)
    ]
    assert not offenders, f"instrumented classes that capture nothing: {offenders}"


def test_no_instrumented_test_sleeps_where_a_wait_would_do():
    """`Thread.sleep` in a test class is a guess about a CI machine's speed.

    Exactly one is allowed, and it is documented: BootAnimationTest has to reach
    a moment INSIDE a 1400ms animation, which is a claim about elapsed time.
    """
    offenders = []
    for path in instrumented_test_files():
        for n, line in enumerate(read(path).splitlines(), 1):
            if "Thread.sleep" in line and not line.lstrip().startswith(("*", "//")):
                offenders.append(f"{path.name}:{n}")
    assert offenders == ["BootAnimationTest.kt:123"] or all(
        o.startswith("BootAnimationTest.kt") for o in offenders
    ), (
        "a fixed sleep outside BootAnimationTest is a flake waiting for a slow "
        f"emulator; use Waits.until / Waits.untilPresent. Found: {offenders}"
    )


def main() -> int:
    tests = [
        (n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    literals = expected_onscreen_literals()
    print(
        f"\n{len(tests) - failures}/{len(tests)} checks passed "
        f"({len(instrumented_test_files())} instrumented test classes, "
        f"{len(literals)} on-screen literals verified against the shipping app)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
