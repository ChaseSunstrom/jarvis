#!/usr/bin/env python3
"""Executable spec for how the app reaches the window insets controller.

The trap, in full, because it costs a whole emulator run to find:

    val w = activity.window
    w.insetsController?.let { c -> c.hide(...) }

crashes on Android 11 when it is called before `setContentView`. On API 30

    // PhoneWindow.java
    public WindowInsetsController getInsetsController() {
        return mDecor.getWindowInsetsController();
    }

has no null check, and `mDecor` does not exist until something installs the
decor — `setContentView` or `getDecorView()`. The NPE is thrown *inside the
getter*, so the `?.` on the Kotlin side never gets a chance to run, and the
Activity dies in `onCreate`. Verbatim from the first emulator run of the
instrumented suite (`e2e · android emulator (API 30)`, run 31307142545):

    java.lang.RuntimeException: Unable to start activity
      ComponentInfo{ai.jarvis.app/ai.jarvis.app.MainActivity}:
    java.lang.NullPointerException: Attempt to invoke virtual method
      'android.view.WindowInsetsController
       com.android.internal.policy.DecorView.getWindowInsetsController()'
      on a null object reference
      at com.android.internal.policy.PhoneWindow.getInsetsController(PhoneWindow.java:3880)
      at ai.jarvis.app.ui.JarvisUi.immersive(JarvisUi.kt:46)
      at ai.jarvis.app.MainActivity.onCreate(MainActivity.kt:70)

Every screen that goes immersive does so before `setContentView` on purpose —
the assist popup and the consent prompt must be edge-to-edge on their FIRST
frame, not one frame later. So the ordering stays and the access changes:
`window.decorView.windowInsetsController`. Asking the window for its decor
installs it, and a `DecorView` that is not attached to a window yet returns a
`PendingInsetsController`, which replays `hide()` and `systemBarsBehavior` onto
the real controller the moment the window is attached.

This file is cheap and static, and it exists because nothing else in the fast
lane can see the bug: it does not stop the app compiling, no JVM test in
`src/test` starts an Activity, and every API-31+ device happens to survive it
(`Activity.getSplashScreen()` installs the decor first).

Run:  python3 android-app/tools/window_insets_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
MAIN_KOTLIN = APP / "src" / "main" / "kotlin"
JARVIS_UI = MAIN_KOTLIN / "ai" / "jarvis" / "app" / "ui" / "JarvisUi.kt"


def kotlin_files() -> list[Path]:
    return sorted(MAIN_KOTLIN.rglob("*.kt"))


def code_only(src: str) -> str:
    """Kotlin with whole-line comments dropped.

    The KDoc on `JarvisUi.immersive` quotes the crashing call and the platform
    source that produces it; that is documentation of the trap, not the trap.
    """
    return "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*", "*/"))
    )


# `x.insetsController` where x is anything but a View. `Window.insetsController`
# is the only property with that name in the platform, so any match is a Window.
WINDOW_INSETS_CONTROLLER = re.compile(r"(?<![\w.])(\w+)\.insetsController\b")


def test_no_shipping_source_reads_window_insetsController():
    """The property that NPEs before the decor exists must not be used at all.

    Not "must not be used before setContentView" — that is a whole-program
    dataflow question a grep cannot answer, and the safe access costs nothing
    anywhere.
    """
    offenders = []
    for path in kotlin_files():
        for n, line in enumerate(code_only(path.read_text(encoding="utf-8")).splitlines(), 1):
            m = WINDOW_INSETS_CONTROLLER.search(line)
            if m:
                offenders.append(f"{path.relative_to(APP)}:{n}: {line.strip()}")
    assert not offenders, (
        "Window.insetsController is read in shipping code. On API 30 that getter "
        "dereferences the decor view with no null check, so a call from onCreate "
        "before setContentView is an NPE the Kotlin `?.` cannot catch and the "
        "Activity never starts. Use `window.decorView.windowInsetsController` "
        "instead — it installs the decor and returns a pending controller that "
        "replays onto the real one.\n  " + "\n  ".join(offenders)
    )


def test_jarvis_ui_immersive_goes_through_the_decor_view():
    """REGRESSION GUARD for the exact line that crashed on the emulator.

    The check above only says the broken property is absent, which would also be
    true of an `immersive()` that stopped hiding the system bars altogether. This
    one says the replacement is present and is still doing the work.
    """
    src = JARVIS_UI.read_text(encoding="utf-8")
    parts = src.split("fun immersive(", 1)
    assert len(parts) == 2, "JarvisUi no longer declares immersive()"
    body = parts[1][:1400]

    assert "decorView.windowInsetsController" in body, (
        "JarvisUi.immersive no longer fetches the controller through "
        "window.decorView.windowInsetsController. Every caller runs it before "
        "setContentView, which is the case Window.insetsController crashes on."
    )
    assert "hide(" in body, (
        "JarvisUi.immersive no longer hides anything; immersive mode is the "
        "whole point of the function"
    )
    assert "BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE" in body, (
        "JarvisUi.immersive no longer asks for swipe-to-reveal bars, so the "
        "system bars would come back permanently on the first swipe"
    )


def test_every_caller_still_routes_through_jarvis_ui():
    """One helper, so one fix covers every screen.

    All three immersive screens (home, assist popup, companion prompt) had the
    same crash from the same shared line. A screen that hand-rolls its own
    edge-to-edge setup would be outside the guard above.
    """
    callers = []
    for path in kotlin_files():
        if path == JARVIS_UI:
            continue
        text = code_only(path.read_text(encoding="utf-8"))
        if "JarvisUi.immersive(" in text:
            callers.append(path.name)
    assert len(callers) >= 3, (
        f"expected at least the three immersive screens to call "
        f"JarvisUi.immersive; found {sorted(callers)}. If a screen stopped "
        "using the helper, check it is not setting up insets by hand."
    )


def test_setDecorFitsSystemWindows_still_precedes_the_controller():
    """Order matters, and it is not obvious from either call.

    `setDecorFitsSystemWindows(false)` is what makes the layout edge-to-edge;
    hiding the bars without it leaves the content laid out inside insets that
    are no longer there. It is also safe before the decor exists (PhoneWindow
    stores the flag and applies it when the view root arrives), which is why
    this one does NOT need the decor-view treatment.
    """
    body = JARVIS_UI.read_text(encoding="utf-8").split("fun immersive(", 1)[1][:1400]
    fits = body.find("setDecorFitsSystemWindows")
    controller = body.find("decorView.windowInsetsController")
    assert fits != -1, "immersive() no longer calls setDecorFitsSystemWindows"
    assert fits < controller, (
        "setDecorFitsSystemWindows must come before the insets controller is "
        "touched, or the first frame is laid out for bars that are being hidden"
    )


def test_every_ordinary_screen_reserves_the_system_bars():
    """`targetSdk = 35` means Android 15 lays the window out BEHIND the bars.

    Reported as *"the tabs on the settings for the android app are too high up,
    and I can't click on them"*. The nav strip is the first thing in the layout,
    so it was drawn under the status bar — and a tap up there belongs to the
    system, not to the button beneath it.

    `android:statusBarColor` and `android:navigationBarColor`, which
    `Theme.JarvisBase` sets and which used to reserve that space, are deprecated
    and ignored from API 35. Nothing warns; the screen just moves up.

    So every Activity either hides the bars ([immersive]) or pads for them
    ([fitSystemBars]). Doing neither is the bug, and it is invisible on any
    device below Android 15 and in every screenshot taken on one.
    """
    bare = []
    for path in kotlin_files():
        src = path.read_text(encoding="utf-8")
        if "setContentView(" not in src or path.name == "JarvisUi.kt":
            continue
        if "JarvisUi.immersive(" in src or "JarvisUi.fitSystemBars(" in src:
            continue
        bare.append(path.name)
    assert not bare, (
        "these call setContentView and neither hide the system bars nor pad for "
        "them, so on Android 15 their top row is under the status bar and cannot "
        "be tapped: " + ", ".join(sorted(bare))
    )


def test_the_inset_helper_covers_the_cutout_and_the_old_api():
    """Two ways to get this subtly wrong, both silent."""
    src = JARVIS_UI.read_text(encoding="utf-8")
    body = src.split("fun fitSystemBars(", 1)
    assert len(body) == 2, "JarvisUi.fitSystemBars is gone"
    body = body[1][:1600]
    assert "displayCutout()" in body, (
        "fitSystemBars ignores the display cutout, so a punch-hole in landscape "
        "eats the edge exactly as the status bar did"
    )
    assert "systemWindowInsetTop" in body, (
        "fitSystemBars has no pre-API-30 branch; getInsets(int) does not exist "
        "on API 29, which is this app's minSdk"
    )
    assert "requestApplyInsets" in body, (
        "a root that is already attached has had its insets dispatched and the "
        "new listener has just missed them"
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
