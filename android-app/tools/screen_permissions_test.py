#!/usr/bin/env python3
"""Executable spec for "why does nothing appear on my screen?".

Three reports, one shape: a permission that is declared in the manifest, so it
looks handled in review, and not actually held at runtime, so nothing works and
nothing says why.

  * *"jarvis doesnt ask to allow notifications"* — `POST_NOTIFICATIONS` has been
    in the manifest since the app was written. It became a **runtime**
    permission in Android 13, and no Activity ever requested it. A runtime
    permission nobody requests is one you do not have, so on a modern phone
    Jarvis could not show the listening notification, the wake-word alert, or a
    Tier-3 approval — and an approval that cannot be delivered times out and is
    denied.

  * *"it just sits in the notifications bar … I have to manually tap it"* —
    `USE_FULL_SCREEN_INTENT` is also declared. Android 14 grants it at install
    only to calling and alarm apps; everyone else is silently downgraded to a
    heads-up. `setFullScreenIntent` returns no error and the takeover simply
    does not happen.

  * The third route, `SYSTEM_ALERT_WINDOW`, is a Settings trip that was
    reported but never requested from anywhere a person would find it.

What this pins: that each of the three is *probed* rather than assumed, that
the runtime one is actually requested, that the checklist tells the truth about
which are essential, and that the screen says which of them is missing.

Run:  python3 android-app/tools/screen_permissions_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

POST_NOTIFICATIONS = "android.permission.POST_NOTIFICATIONS"
FULL_SCREEN = "android.permission.USE_FULL_SCREEN_INTENT"
OVERLAY = "android.permission.SYSTEM_ALERT_WINDOW"

#: The Settings deep link for each, and the API level below which the manifest
#: declaration is the whole story.
ROUTES = {
    "post_notifications": ("android.settings.APP_NOTIFICATION_SETTINGS", 33),
    "full_screen": ("android.settings.MANAGE_APP_USE_FULL_SCREEN_INTENT", 34),
}


def check_declared(android: Path) -> int:
    manifest = (android / "app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    failures = 0
    for permission in (POST_NOTIFICATIONS, FULL_SCREEN, OVERLAY):
        if permission not in manifest:
            print(f"FAIL  {permission} is not declared; nothing else can help")
            failures += 1
    return failures


def find_askers(android: Path, symbol: str) -> list[Path]:
    """Every file that names [symbol] inside a literal `requestPermissions` call.

    Deliberately literal: a file that mentions the permission in a comment, or
    checks for it and gives up, is not a file that asks for it. That distinction
    is the whole bug — nine permissions were declared, checked for, reported on,
    and never once requested.
    """
    root = android / "app/src/main/kotlin/ai/jarvis/app"
    askers = []
    for path in sorted(root.rglob("*.kt")):
        src = path.read_text(encoding="utf-8")
        if symbol not in src:
            continue
        if re.search(rf"requestPermissions\(\s*arrayOf\([^)]*{re.escape(symbol)}", src, re.S):
            askers.append(path)
    return askers


def check_a_runtime_permission_is_actually_requested(
    android: Path, symbol: str, consequence: str, guard: tuple[str, ...]
) -> int:
    """Declaring is not asking, and this is the bug that produced the report.

    Generalised from a hardcoded `POST_NOTIFICATIONS` check, because the same
    shape turned out to apply to nine more permissions. The exhaustive sweep —
    "every dangerous permission in the manifest has a requester" — lives in
    `runtime_permissions_test.py`, which owns the whole table; this stays here
    for the three screen routes that produced the original reports.
    """
    askers = find_askers(android, symbol)
    failures = 0
    if not askers:
        print(f"FAIL  nothing in the app requests {symbol} at runtime. {consequence}")
        return 1
    for path in askers:
        src = path.read_text(encoding="utf-8")
        is_activity = re.search(r"class\s+\w+\s*(\([^)]*\))?\s*:\s*[^\n{]*Activity", src)
        if not is_activity:
            print(f"FAIL  {path.name} requests a permission but is not an Activity")
            failures += 1
        # Asking on every resume forever is how people learn to hit Deny.
        if not any(g in src for g in guard):
            print(f"FAIL  {path.name} has no guard against re-prompting for {symbol}")
            failures += 1
    return failures


def check_the_runtime_one_is_actually_requested(android: Path) -> int:
    return check_a_runtime_permission_is_actually_requested(
        android,
        "POST_NOTIFICATIONS",
        "On Android 13+ that means no listening notification, no wake-word "
        "alert, and Tier-3 approvals that time out undelivered.",
        guard=("askedForNotifications", "REQ_NOTIFICATIONS"),
    )


def check_each_route_is_probed_not_assumed(android: Path) -> int:
    """A declared permission you cannot see the state of is one you cannot report."""
    src = (android / "app/src/main/kotlin/ai/jarvis/app/compat/GrapheneCompat.kt").read_text(
        encoding="utf-8"
    )
    failures = 0
    for fn, why in (
        ("canPostNotifications", "whether Jarvis may show anything at all"),
        ("canUseFullScreenIntent", "whether a wake word takes over the screen"),
        ("canDrawOverlays", "whether the orb can be drawn over other apps"),
    ):
        if f"fun {fn}(" not in src:
            print(f"FAIL  GrapheneCompat cannot report {why} ({fn} is missing)")
            failures += 1

    # The version gates are the whole subtlety: below them the manifest IS the
    # answer, above them it is not, and getting that backwards means reporting
    # a problem to everyone or to nobody.
    if "SDK_INT < 33" not in src:
        print("FAIL  the notification check is not gated at Android 13")
        failures += 1
    if "SDK_INT < 34" not in src:
        print("FAIL  the full-screen check is not gated at Android 14")
        failures += 1
    if "canUseFullScreenIntent()" not in src:
        print(
            "FAIL  the full-screen check does not ask NotificationManager; the "
            "manifest declaration is not the answer on 14+"
        )
        failures += 1
    # Only the full-screen one needs writing out here — it is API 34+, so the
    # `Settings` constant cannot be referenced on a lower compile path. The
    # notification route uses the ordinary constant from SettingsActivity, and
    # is checked there.
    if ROUTES["full_screen"][0] not in src:
        print(f"FAIL  no deep link to {ROUTES['full_screen'][0]}")
        failures += 1
    return failures


def check_the_checklist_ranks_them_honestly(android: Path) -> int:
    """Essential means "nothing works"; optional means "there is another way".

    Notifications are essential — without them Jarvis is mute in every surface
    it has. Full-screen and overlay are alternatives to each other, so neither
    can be the one that fails a checklist on its own.
    """
    src = (android / "app/src/main/kotlin/ai/jarvis/app/compat/GrapheneCompat.kt").read_text(
        encoding="utf-8"
    )
    failures = 0
    blocks = dict(
        re.findall(r"id = (ID_\w+),(.*?)\n        \),", src, re.S)
    )
    wanted = {
        "ID_POST_NOTIFICATIONS": True,
        # The disjunction: "can a wake word put anything in front of you".
        # Essential, because with neither grant the answer is no.
        "ID_ON_SCREEN": True,
        # Each individually optional BECAUSE either satisfies the one above.
        "ID_FULL_SCREEN": False,
        "ID_OVERLAY": False,
    }
    for name, essential in wanted.items():
        block = blocks.get(name)
        if block is None:
            print(f"FAIL  {name} is not on the checklist")
            failures += 1
            continue
        found = re.search(r"essential = (true|false)", block)
        if not found:
            print(f"FAIL  {name} does not say whether it is essential")
            failures += 1
        elif (found.group(1) == "true") != essential:
            print(
                f"FAIL  {name} is marked essential={found.group(1)}; expected "
                f"{str(essential).lower()}"
            )
            failures += 1
    return failures


def check_the_screen_says_which_is_missing(android: Path) -> int:
    """A status line that only mentions one of three grants answers nothing."""
    src = (android / "app/src/main/kotlin/ai/jarvis/app/SettingsActivity.kt").read_text(
        encoding="utf-8"
    )
    failures = 0
    status = re.search(r"private fun refreshOverlayStatus\(\).*?\n    \}", src, re.S)
    if not status:
        print("FAIL  Settings no longer reports what a wake word can do")
        return 1
    body = status.group(0)
    for probe in ("canPostNotifications", "canUseFullScreenIntent", "canDrawOverlays"):
        if probe not in body:
            print(f"FAIL  the wake-word status line ignores {probe}")
            failures += 1
    # Every fix the status line names must be one tap away.
    #
    # This used to demand three named buttons ON this screen. Settings carried
    # its own grid of raw Settings shortcuts — ASSISTANT, ACCESSIBILITY,
    # NOTIFICATIONS, OVERLAY, FULL SCREEN, NOTIFICATION SETTINGS, BATTERY, APP
    # INFO — with no indication of which were granted, two of them opening the
    # same screen as buttons already higher up, and two more with near-identical
    # names and unrelated meanings. That is not reachability, it is a maze.
    #
    # The invariant that actually matters: the ONE grant that changes how the
    # wake word appears is on this screen, and everything else is reachable
    # through the checklist, whose rows each carry their own settings action.
    if '"DISPLAY OVER APPS"' not in src:
        print("FAIL  Settings cannot grant 'display over other apps', which is the "
              "grant that decides whether the orb is drawn directly")
        failures += 1
    if "SystemCheckActivity" not in src:
        print("FAIL  Settings no longer offers the checklist, so the remaining "
              "grants are unreachable from here")
        failures += 1
    checklist = (android / "app/src/main/kotlin/ai/jarvis/app/ui/SystemCheckActivity.kt").read_text(
        encoding="utf-8"
    )
    if "GrapheneCompat.openSettingsFor" not in checklist:
        print("FAIL  the checklist rows no longer open anything; every grant it "
              "lists became unreachable at once")
        failures += 1
    return failures


def check_neither_grant_alone_is_load_bearing(android: Path) -> int:
    """The gap that let the phone sit broken with nothing saying so.

    Overlay and full-screen are each optional, correctly — either one lets a
    wake word reach the screen. But that made the state where NEITHER is granted
    invisible: nothing on the checklist was both essential and missing, so the
    home screen showed no banner, and the only symptom was that saying "Hey
    Jarvis" did nothing.

    The fix is a requirement whose `satisfied` is a disjunction, and this pins
    that it stays one — an `and` here would demand both, and a copy of either
    single check would put the hole straight back.
    """
    src = (android / "app/src/main/kotlin/ai/jarvis/app/compat/GrapheneCompat.kt").read_text(
        encoding="utf-8"
    )
    block = re.search(r"id = ID_ON_SCREEN,(.*?)\n        \),", src, re.S)
    if not block:
        print("FAIL  there is no 'can Jarvis appear on screen' requirement")
        return 1
    if not re.search(
        r"satisfied = status\.canDrawOverlays \|\| status\.fullScreenIntents", block.group(1)
    ):
        print(
            "FAIL  'appear on screen' is not the OR of the two grants; either "
            "half alone re-opens the hole it exists to close"
        )
        return 1
    return 0


def check_the_user_is_walked_through_it_once(android: Path) -> int:
    """A banner nobody reads is not a setup flow.

    Reported twice, a build apart: "the overlay isn't popping up still". Every
    one of these is a Settings screen the user has never heard of, so the app
    has to take them there rather than mention it.
    """
    src = (android / "app/src/main/kotlin/ai/jarvis/app/MainActivity.kt").read_text(
        encoding="utf-8"
    )
    failures = 0
    walk = re.search(
        r"private fun openSystemCheckOnceIfSetupIsIncomplete\(\).*?\n    \}", src, re.S
    )
    if not walk:
        print("FAIL  the home screen never opens the checklist by itself")
        return 1
    body = walk.group(0)
    if "missingEssentials" not in body:
        print("FAIL  the walkthrough does not key on what is actually missing")
        failures += 1
    if "setupChecklistShown" not in body:
        print("FAIL  the walkthrough has no once-only guard, so it is a nag loop")
        failures += 1
    if body.count("setupChecklistShown = true") < 2:
        print(
            "FAIL  the flag is not set on BOTH paths; a phone with everything "
            "granted would re-check on every resume forever"
        )
        failures += 1
    return failures


def check_the_wake_alert_does_not_overpromise(android: Path) -> int:
    """"Heard you" while nothing appears is the exact complaint."""
    src = (android / "app/src/main/kotlin/ai/jarvis/app/assist/WakeWordService.kt").read_text(
        encoding="utf-8"
    )
    heard = re.search(r"private fun showHeard\(.*?\n    \}", src, re.S)
    if not heard:
        print("FAIL  the wake-word alert is gone")
        return 1
    if "canUseFullScreenIntent" not in heard.group(0):
        print(
            "FAIL  the wake-word alert claims a takeover without checking whether "
            "the platform will perform one"
        )
        return 1
    return 0


def check_the_surfaces_are_not_boxes(android: Path) -> int:
    """The orb is the surface; a panel behind it is a frame around it.

    Reported as "it is surrounded by boxes, instead of just being the arc
    reactor circle". Both the floating overlay and the assist popup drew a dark
    rounded card with a cyan stroke, and the card was the first thing you saw.
    """
    failures = 0
    for rel, name in (
        ("assist/AssistOverlay.kt", "the floating overlay"),
        ("JarvisAssistActivity.kt", "the assist popup"),
    ):
        path = android / "app/src/main/kotlin/ai/jarvis/app" / rel
        src = path.read_text(encoding="utf-8")
        code = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
        code = re.sub(r"//[^\n]*", " ", code)
        if "JarvisUi.panel(" in code:
            print(f"FAIL  {name} still draws a panel behind the orb")
            failures += 1
        if "setShadowLayer" not in code:
            print(
                f"FAIL  {name} dropped its card without giving the text a shadow; "
                "it will be unreadable over a light app"
            )
            failures += 1
    return failures


def main() -> int:
    android = Path(__file__).resolve().parents[1]
    failures = (
        check_declared(android)
        + check_the_runtime_one_is_actually_requested(android)
        + check_each_route_is_probed_not_assumed(android)
        + check_the_checklist_ranks_them_honestly(android)
        + check_the_screen_says_which_is_missing(android)
        + check_neither_grant_alone_is_load_bearing(android)
        + check_the_user_is_walked_through_it_once(android)
        + check_the_wake_alert_does_not_overpromise(android)
        + check_the_surfaces_are_not_boxes(android)
    )
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(
        "screen permissions: all three routes declared, probed at the right API "
        "levels, ranked honestly on the checklist, requested where they must be, "
        "reported where the user can act, and neither surface is a box"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
