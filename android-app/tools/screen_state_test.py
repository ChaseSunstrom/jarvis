#!/usr/bin/env python3
"""Executable spec: two screens with no answer for the ordinary case.

## 1. Settings threw away edits on the way out

`SettingsActivity.save()` ran from exactly one place: the SAVE pill at the
bottom of a ScrollView several screens long. The console tab strip sits across
the TOP of that same screen and went straight to `startActivity`. So editing the
server URL and then tapping a tab — the most natural thing to do on a screen
with a nav bar over it — discarded the edit, silently, with no warning and no
way back. Back did the same.

Eleven controls across four types, and none of them told anybody. The fix is one
snapshot compared against another, rather than a dirty flag on each control:
eleven flags is eleven places to forget one, and a control missing from a
snapshot is a visible omission where a missing flag is not.

The dialog's SAVE runs the same validation the pill does and only leaves if it
passed, so leaving cannot store an invalid URL by the side door.

## 2. The console had no loading state and no error state

`ManagementActivity` had no `onReceivedError`, no `onReceivedHttpError`, and no
progress indicator of any kind. An unreachable console therefore rendered
**Chromium's** white "webpage not available" page — system fonts, a Chrome error
code, a RELOAD button that is not this app's — full-bleed inside an all-black
Jarvis, while the tab strip above it still highlighted a tab it had never
loaded. The only thing on offer was the app's own RELOAD, next to Chrome's.

And a slow console was indistinguishable from a dead one for as long as it took,
because nothing said a fetch was in flight.

The two failures want completely different remedies and so are kept apart: a
network that did not answer is a server to start or a VPN to connect; a 401 is a
token to re-pair. One screen saying "something went wrong" for both is how a
stale token gets diagnosed as a broken network.

Run:  python3 android-app/tools/screen_state_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
KOTLIN = ANDROID / "app/src/main/kotlin/ai/jarvis/app"

SETTINGS = KOTLIN / "SettingsActivity.kt"
MANAGEMENT = KOTLIN / "ManagementActivity.kt"
CONFIG = KOTLIN / "config/JarvisConfig.kt"


def code(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


# ---------------------------------------------------------------------------
# 1. unsaved settings
# ---------------------------------------------------------------------------


def test_every_way_off_the_settings_screen_goes_through_the_dirty_check() -> None:
    """The two exits, and both of them used to discard silently."""
    src = code(SETTINGS)
    assert "private fun leaveIfSaved(" in src, (
        "the settings screen has no dirty check, so tapping a console tab or "
        "pressing Back discards every edited field"
    )
    tab_strip = re.search(r"ConsoleFrame\.tabBar\(this, current = null.*?\},", src, re.S)
    assert tab_strip, "the settings screen has lost its tab strip"
    assert "leaveIfSaved" in tab_strip.group(0), (
        "the tab strip still goes straight to startActivity, which is the "
        "reported disappearing-settings bug exactly"
    )
    back = re.search(r"override fun onBackPressed\(\) \{.*?\n    \}", src, re.S)
    assert back, "the settings screen does not handle Back"
    assert "leaveIfSaved" in back.group(0), (
        "Back leaves without asking, so the other half of the bug is still there"
    )


def test_the_dirty_check_compares_every_control() -> None:
    """A snapshot with a control missing is a control whose edits are still
    silently discarded — the same bug, narrower."""
    src = code(SETTINGS)
    saved = re.search(r"private fun savedSnapshot\(\): String = listOf\((.*?)\n    \)", src, re.S)
    edited = re.search(r"private fun editedSnapshot\(\): String = listOf\((.*?)\n    \)", src, re.S)
    assert saved and edited, "the settings screen has no snapshot pair"
    # The controls the screen declares, from its own fields.
    controls = set(re.findall(r"private lateinit var (\w+): (?:EditText|Switch)", src))
    # Fields the save path writes are the population that matters.
    save = re.search(r"private fun save\(\): Boolean \{.*?\n    \}", src, re.S)
    assert save, "the settings screen has no save()"
    written = set(re.findall(r"config\.(\w+)\s*=", save.group(0)))
    missing_saved = sorted(n for n in written if n not in saved.group(1))
    assert not missing_saved, (
        "save() stores these and the dirty check does not compare them, so "
        "editing one and leaving discards it with no warning: "
        + ", ".join(missing_saved)
    )
    missing_edited = sorted(
        c for c in controls if c not in edited.group(1) and c != "urlField"
    )
    # urlField is compared through ServerUrl.normalize rather than by name.
    assert not missing_edited or "ServerUrl.normalize(urlField" in edited.group(1), (
        "a control on this screen is not in the edited snapshot: "
        + ", ".join(missing_edited)
    )


def test_leaving_by_saving_only_leaves_if_the_save_worked() -> None:
    """`save()` refuses an invalid URL and an empty token. If SAVE-then-leave
    left anyway, the side door would store nothing and the screen would still
    close — which is the original bug with an extra dialog."""
    src = code(SETTINGS)
    assert re.search(r"private fun save\(\): Boolean", src), (
        "save() no longer reports whether it stored anything, so the dirty "
        "check cannot tell a refused save from a successful one"
    )
    save = re.search(r"private fun save\(\): Boolean \{.*?\n    \}", src, re.S).group(0)
    assert save.count("return false") >= 2, (
        "save() no longer refuses an invalid URL or an empty token"
    )
    assert "return true" in save, "save() never reports success"
    leave = re.search(r"private fun leaveIfSaved\(.*?\n    \}", src, re.S).group(0)
    assert "if (save()) go()" in leave, (
        "the dialog's SAVE leaves whether or not the save was accepted"
    )
    for label in ("DISCARD", "KEEP EDITING"):
        assert label in leave, f"the dialog offers no {label}"


def test_the_normalisation_matches_what_save_will_store() -> None:
    """Otherwise the screen is 'dirty' the moment it opens.

    An empty pipeline box becomes `DEFAULT_PIPELINE` on save, and `warmLink` /
    `headsetButton` read `headsetMode` in their own getters. Comparing raw
    against stored would report a change nobody made and prompt on every exit,
    which trains the user to tap DISCARD.
    """
    src = code(SETTINGS)
    edited = re.search(
        r"private fun editedSnapshot\(\): String = listOf\((.*?)\n    \)", src, re.S
    ).group(1)
    assert "DEFAULT_PIPELINE" in edited, (
        "an empty pipeline box reads as an edit against the default it is "
        "about to become"
    )
    assert "headsetMode.isChecked && headsetButton.isChecked" in edited, (
        "the two switches that read headsetMode in their getters are compared "
        "as if they did not, so the screen is dirty on open whenever headset "
        "mode is off"
    )
    # And the getters really do behave that way, or the mirroring above is
    # compensating for something that is no longer true.
    config = code(CONFIG)
    assert re.search(r"var warmLink[\s\S]{0,200}headsetMode &&", config), (
        "JarvisConfig.warmLink no longer reads headsetMode, so the dirty "
        "check is mirroring a rule that has gone"
    )


# ---------------------------------------------------------------------------
# 2. the console's own failures
# ---------------------------------------------------------------------------


def test_the_console_has_an_error_state_of_its_own() -> None:
    src = code(MANAGEMENT)
    for override, why in (
        ("override fun onReceivedError(",
         "an unreachable console renders Chromium's white error page inside an "
         "all-black app"),
        ("override fun onReceivedHttpError(",
         "a 401 from an expired token looks exactly like a broken console"),
    ):
        assert override in src, f"ManagementActivity has no {override}: {why}"
    assert "private fun showError(" in src and "private fun showLoading(" in src, (
        "there is no panel to show instead of Chromium's page, or nothing says "
        "a fetch is in flight"
    )


def test_only_the_main_frame_becomes_an_error_screen() -> None:
    """`onReceivedError` fires for every failed sub-resource, including the ones
    `shouldInterceptRequest` deliberately blocks. Turning a blocked tracker into
    a full-screen "cannot reach your server" would be a worse lie than
    Chromium's page."""
    src = code(MANAGEMENT)
    for name in ("onReceivedError", "onReceivedHttpError"):
        block = re.search(rf"override fun {name}\(.*?\n        \}}", src, re.S)
        assert block, f"{name} is gone"
        assert "request.isForMainFrame" in block.group(0), (
            f"{name} treats a failed sub-resource as a failed page"
        )


def test_the_two_failures_are_told_apart() -> None:
    """One is a network to fix, the other a token to re-pair. One message for
    both is how a stale token gets diagnosed as a broken network."""
    src = code(MANAGEMENT)
    http = re.search(r"override fun onReceivedHttpError\(.*?\n        \}", src, re.S).group(0)
    assert "401, 403 ->" in http, (
        "an expired token is reported as a generic server error, with no "
        "mention of re-pairing"
    )
    assert "404 ->" in http, (
        "a 404 does not mention that the address may be jarvis-core, which has "
        "no management UI — the single most likely cause"
    )
    net = re.search(r"override fun onReceivedError\(.*?\n        \}", src, re.S).group(0)
    assert "serverOrigin" in net, (
        "the unreachable message does not name the host it could not reach"
    )


def test_chromiums_page_is_cleared_rather_than_covered() -> None:
    """The platform has already rendered its own error document by the time
    `onReceivedError` runs, so a panel over the top still sits on a white page
    — visible around the edges and behind every scroll."""
    src = code(MANAGEMENT)
    show = re.search(r"private fun showError\(.*?\n    \}", src, re.S).group(0)
    assert "loadData(" in show, (
        "the error panel is drawn over Chromium's page rather than replacing it"
    )
    # And NOT with loadUrl: every loadUrl from this activity carries the bearer
    # header (console_parity_test.py enforces it, because a navigation without
    # it lands on a login page in a WebView nobody can type into). Clearing a
    # document is not a navigation and must not look like one.
    assert "loadUrl(" not in show, (
        "the blank is loaded with loadUrl, which is the call that must always "
        "carry the bearer header"
    )
    finished = re.search(r"override fun onPageFinished\(.*?\n            view\.clearHistory\(\)",
                         src, re.S)
    assert finished and "failed" in finished.group(0), (
        "onPageFinished takes the error panel down again — the platform calls "
        "it after onReceivedError AND again for the blank, so the message "
        "would flash and vanish"
    )


def test_a_reload_clears_the_previous_failure() -> None:
    """An error panel that survives a successful retry is a screen stuck on a
    problem that is over."""
    src = code(MANAGEMENT)
    load = re.search(r"private fun load\(next: ConsoleTab\) \{.*?\n    \}", src, re.S).group(0)
    assert "failed = false" in load, "a fresh navigation keeps the old failure"
    assert "showLoading(" in load, "a fresh navigation says nothing while it runs"
    assert "private fun reload()" in src and "load(tab)" in src, (
        "TRY AGAIN does not re-issue the authenticated navigation for the tab "
        "the user is actually on"
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
