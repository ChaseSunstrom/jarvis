#!/usr/bin/env python3
"""Executable spec for "the app and the console are the same thing".

The report, in the user's words: *"can you make sure the mobile app isn't a
different screen from the web view? they should be the same but with mobile
permission stuff etc. because right now it feels weird that it's kind of similar
but not really"*.

It was kind of similar but not really, and the reason is worth stating plainly
because it is the kind that accumulates rather than the kind anybody decides on.
The phone's home screen offered three buttons:

  * **MANAGE** opened the console — at its front door, with no way on to any of
    its other four sections, because the page's own nav lives inside a WebView
    whose links carry no bearer header.
  * **SETTINGS** opened a native screen about *this phone*, while the console
    has a tab of the same name about *the house*.
  * **AUTOMATIONS** opened a native screen listing the tasks this phone runs by
    itself — a genuinely different thing from the house's automations, which
    happens to share a word.

Three buttons, one of which reached a fifth of the console and two of which went
somewhere the browser has no equivalent of. Nothing was broken; it simply did
not add up to one product.

What is pinned here:

  1. **The phone's nav IS the console's nav** — the same sections, the same
     labels, in the same order, read from `+layout.svelte` and from
     `ConsoleTab.kt` and compared. A page added to one and not the other fails
     here rather than on somebody's phone.
  2. **Every tab's path is a real route** in the SvelteKit app.
  3. **The mobile half is named for what it is.** A button called "Settings"
     beside a tab called "SETTINGS" is the confusion itself.
  4. **The WebView is never handed a path from an intent.** It carries the
     user's bearer token, so a caller-supplied URL would let anything on the
     device that can start an activity aim an authenticated session anywhere.
     The intent carries a tab NAME; the path comes from the table.

Run:  python3 android-app/tools/console_parity_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
REPO = ANDROID.parent

TABS = ANDROID / "app/src/main/kotlin/ai/jarvis/app/ui/ConsoleTab.kt"
MANAGEMENT = ANDROID / "app/src/main/kotlin/ai/jarvis/app/ManagementActivity.kt"
MAIN = ANDROID / "app/src/main/kotlin/ai/jarvis/app/MainActivity.kt"
SETTINGS = ANDROID / "app/src/main/kotlin/ai/jarvis/app/SettingsActivity.kt"
FRAME = ANDROID / "app/src/main/kotlin/ai/jarvis/app/ui/ConsoleFrame.kt"
LAYOUT = REPO / "jarvis-web/src/routes/+layout.svelte"
ROUTES = REPO / "jarvis-web/src/routes"


def console_nav() -> list[tuple[str, str]]:
    """The console's own nav, in order: [(LABEL, /path), ...]."""
    src = LAYOUT.read_text(encoding="utf-8")
    block = re.search(r"const NAV = \[(.*?)\];", src, re.S)
    if not block:
        return []
    return [
        (label, href)
        for href, label in re.findall(
            r"\{\s*href:\s*'([^']+)',\s*label:\s*'([^']+)'", block.group(1)
        )
    ]


def phone_tabs() -> list[tuple[str, str]]:
    """`ConsoleTab`'s table, in declaration order."""
    src = TABS.read_text(encoding="utf-8")
    body = re.search(r"\) \{\n(.*?);\n\n    companion object", src, re.S)
    if not body:
        return []
    return re.findall(r'\w+\("([A-Z]+)",\s*"(/[a-z]*)"\)', body.group(1))


def check_the_two_navs_are_one() -> list[str]:
    failures = []
    web = console_nav()
    phone = phone_tabs()
    if not web:
        return ["cannot read the console's NAV table out of +layout.svelte"]
    if not phone:
        return ["cannot read ConsoleTab's table"]

    if phone != web:
        # Say exactly what diverged; "they differ" on a five-row table is a
        # message that costs the reader the comparison this already did.
        web_only = [t for t in web if t not in phone]
        phone_only = [t for t in phone if t not in web]
        if web_only:
            failures.append(
                f"the console has sections the phone does not: {web_only}. The phone "
                "would show a strictly smaller app than a browser at the same URL."
            )
        if phone_only:
            failures.append(
                f"the phone offers sections the console does not: {phone_only}. Those "
                "buttons open a page that is not there."
            )
        if not web_only and not phone_only:
            failures.append(
                f"the two navs hold the same sections in a different order: the console "
                f"says {[t[0] for t in web]} and the phone says {[t[0] for t in phone]}"
            )
    return failures


def check_every_tab_is_a_real_route() -> list[str]:
    """A label with no page behind it is a button that opens a 404."""
    failures = []
    for label, path in phone_tabs():
        page = ROUTES / path.lstrip("/") / "+page.svelte"
        if not page.is_file():
            failures.append(
                f"the phone's {label} tab points at {path}, and there is no "
                f"{page.relative_to(REPO)}"
            )
    return failures


def check_the_mobile_half_is_named_for_itself() -> list[str]:
    failures = []
    tabs = TABS.read_text(encoding="utf-8")
    main = MAIN.read_text(encoding="utf-8")

    phone_label = re.search(r'const val PHONE_LABEL = "([A-Z ]+)"', tabs)
    if not phone_label:
        return ["ConsoleTab has no name for the mobile half"]
    label = phone_label.group(1)
    if label in {name for name, _ in phone_tabs()}:
        failures.append(
            f'the native settings button is called "{label}", which is also one of the '
            "console's tabs. That collision is the confusion this whole change is about."
        )
    if f"ConsoleTab.PHONE_LABEL" not in main and "ConsoleTab.DEFAULT" not in main:
        failures.append(
            "MainActivity names a console destination itself rather than taking one "
            "from ConsoleTab, so the two can disagree"
        )
    # The home screen must NOT draw the console's nav.
    #
    # This check used to demand the opposite — that the grid be built from
    # ConsoleTab.entries so a new section could not be forgotten. That was the
    # right fix for a hand-written list of three buttons, and the wrong shape:
    # the console frame already carries this exact nav as a tab strip, so the
    # home screen was drawing a second copy of it and needed a spec to keep the
    # copy honest. One MANAGE button cannot drift from a table it does not read.
    if re.search(r"ConsoleTab\.entries", main):
        failures.append(
            "the home screen is enumerating the console's sections again. That nav "
            "lives in the console frame (see ConsoleFrame); a second copy of it on "
            "the home screen is the thing that has to be kept in step by hand."
        )
    if not re.search(r'JarvisUi\.ghost\([^,]+, "MANAGE"\)', main):
        failures.append(
            "the home screen has no MANAGE button, so the console is unreachable "
            "from the first screen of the app"
        )
    return failures


def check_the_phones_own_screens_do_not_borrow_the_consoles_words() -> list[str]:
    """The phone-local task list is not the house's automations.

    `AutomationsActivity` lists what THIS DEVICE does by itself — a geofence, a
    media button, a rule pushed to it — and it is reached from the phone's own
    settings. Calling it "Automations" next to a console tab called AUTOMATIONS
    is two different things wearing one name, which is most of what "kind of
    similar but not really" was measuring.
    """
    failures = []
    settings = SETTINGS.read_text(encoding="utf-8")
    more = re.search(r'JarvisUi\.label\(ctx, "More"\).*?\n        \)', settings, re.S)
    if not more:
        return ["the settings screen has no More section"]
    if 'JarvisUi.ghost(ctx, "AUTOMATIONS")' in settings:
        failures.append(
            "the phone's own task list is called AUTOMATIONS again, which is the name "
            "of one of the console's tabs and a different thing entirely"
        )
    if '"PHONE TASKS"' not in settings:
        failures.append("the phone's own task list has no name of its own on the settings screen")

    screen = ANDROID / "app/src/main/kotlin/ai/jarvis/app/automation/ui/AutomationsActivity.kt"
    if screen.is_file() and 'JarvisUi.title(this, "AUTOMATIONS")' in screen.read_text(
        encoding="utf-8"
    ):
        failures.append(
            "the phone's task list still titles itself AUTOMATIONS, so arriving there "
            "from the console's tab of the same name looks like the same page gone wrong"
        )
    return failures


def check_the_console_names_the_phones_screens_correctly() -> list[str]:
    """When one surface gives the other an instruction, it has to be followable.

    The console's pairing panel tells you where to scan the code. It said
    "SETTINGS -> SCAN QR" — and SETTINGS is now one of the console's own tabs,
    reached on the phone by a button that opens a web page with no camera in
    it. An instruction that names the wrong screen is worse than none: it is
    read as the app being out of date rather than the sentence being.
    """
    failures = []
    pairing = REPO / "jarvis-web/src/lib/components/Pairing.svelte"
    if not pairing.is_file():
        return ["the console has no pairing panel"]
    text = pairing.read_text(encoding="utf-8")
    tabs = TABS.read_text(encoding="utf-8")
    phone_label = re.search(r'const val PHONE_LABEL = "([A-Z ]+)"', tabs)
    label = phone_label.group(1) if phone_label else "PHONE"
    if "SCAN QR" not in text:
        return failures
    line = next((ln for ln in text.splitlines() if "SCAN QR" in ln), "")
    if label not in line:
        failures.append(
            f"the console tells the user to scan the pairing code somewhere other than "
            f"{label}: {line.strip()!r}"
        )
    return failures


def check_the_console_hides_the_nav_the_frame_already_draws() -> list[str]:
    """Two rows of tabs, one of which does not work.

    *"theres still duplicate tabs in the mobile app for the manage"* — and they
    were: ManagementActivity draws the console's sections as a native strip,
    and the page inside its WebView drew the same sections again, plus a
    JARVIS/CONSOLE wordmark an inch under the native title bar.

    The native one is the one that has to stay. A link tapped inside a WebView
    is a page-initiated navigation, and WebView does not attach
    `additionalHeaders` to those, so the page's own nav cannot carry the bearer
    token — it is the copy that looks right and does not work.

    The coupling is a User-Agent string in Kotlin and a regex in `app.html`,
    which is exactly the kind of pair that goes quietly wrong: change the UA and
    nothing fails, the duplicate nav just comes back.
    """
    failures = []
    src = MANAGEMENT.read_text(encoding="utf-8")
    app_html = REPO / "jarvis-web/src/app.html"
    layout = REPO / "jarvis-web/src/routes/+layout.svelte"

    agent = re.search(r'private val USER_AGENT =\s*\n\s*"([^"/]+)/', src)
    if not agent:
        return ["ManagementActivity no longer sets a User-Agent for its WebView"]
    marker = agent.group(1)

    if not app_html.is_file():
        return ["jarvis-web has no app.html"]
    html = app_html.read_text(encoding="utf-8")
    if "data-embed" not in html:
        failures.append("app.html leaves no embed marker for the server to fill")

    # Detected on the SERVER, from the request header. An inline script sniffing
    # navigator.userAgent is what the first attempt used, and this app's CSP is
    # `script-src: 'self'` with no unsafe-inline — so it was blocked, silently,
    # leaving a working page with no marker on it. Checked here because the CSP
    # and this detection live in different files and neither mentions the other.
    hooks = REPO / "jarvis-web/src/hooks.server.ts"
    if not hooks.is_file():
        failures.append("jarvis-web has no server hooks, so nothing can fill the marker")
    elif marker not in hooks.read_text(encoding="utf-8"):
        failures.append(
            f"hooks.server.ts does not look for {marker!r} in the User-Agent, so the "
            "console cannot tell it is inside the phone's console frame and draws a "
            "second copy of the frame's own nav"
        )
    if re.search(r"<script(?![^>]*\bsrc=)", html):
        failures.append(
            "app.html has an inline script. The CSP is script-src 'self' with no "
            "unsafe-inline, so it will not run — and a blocked inline script fails "
            "silently, leaving the page working and whatever it set missing."
        )

    if not layout.is_file():
        return failures + ["jarvis-web has no root layout"]
    css = layout.read_text(encoding="utf-8")
    if "data-embed='android'" not in css and 'data-embed="android"' not in css:
        failures.append(
            "the console's layout never reacts to the embed marker, so the marker is "
            "set and nothing is hidden"
        )
    else:
        # Every selector that mentions the marker, so a rule can be split across
        # a selector list without this reading only the first line of it. The
        # first draft sliced from the FIRST occurrence and stopped short of the
        # second selector in the very list it was checking.
        guarded = " ".join(re.findall(r"data-embed[^{}]*", css))
        # Bounded patterns, not substrings: `.brand` matches inside
        # `.brand-disabled`, so renaming the rule to something that no longer
        # hides anything read as a pass. Found by trying exactly that.
        for what, pattern in (
            ("nav[aria-label='Management sections']", r"nav\[aria-label='Management sections'\]"),
            (".brand", r"\.brand(?![-\w])"),
        ):
            if not re.search(pattern, guarded):
                failures.append(
                    f"the embedded console still draws {what}, which the native frame "
                    "already draws above it"
                )
    return failures


def check_the_webview_is_not_handed_a_url() -> list[str]:
    """The token rides on this navigation. The path must not come from a caller.

    `ManagementActivity` attaches `Authorization: Bearer <token>` to the
    navigation it initiates. If the path came from the intent, any component on
    the device that can start an activity could point an authenticated session
    at an arbitrary path — and the origin lock only constrains the host, not
    what is fetched from it.
    """
    failures = []
    src = MANAGEMENT.read_text(encoding="utf-8")

    if "ConsoleTab.of(intent?.getStringExtra(EXTRA_TAB))" not in src:
        failures.append(
            "ManagementActivity no longer resolves its tab through ConsoleTab.of, which "
            "is what keeps an intent from naming a path"
        )
    # The extra must be read exactly once, and only into ConsoleTab.of.
    for extra in re.findall(r"getStringExtra\((\w+)\)", src):
        if extra != "EXTRA_TAB":
            failures.append(f"ManagementActivity reads an unexpected extra: {extra}")
    if re.search(r"loadUrl\(\s*intent", src):
        failures.append("ManagementActivity loads a URL straight from its intent")
    # Every navigation this activity starts carries the bearer, because WebView
    # attaches additionalHeaders to the navigation it is given and nothing else.
    for call in re.findall(r"webView\?\.loadUrl\(([^\n]*)", src):
        if "Authorization" not in call:
            failures.append(
                f"a navigation without the bearer header: loadUrl({call.strip()}. The "
                "console would answer it with a login page, inside a WebView, with no "
                "way to type into it."
            )
    # ConsoleTab.of must be total: an unknown name is a typo or a hostile
    # caller, and either way the answer is a tab rather than an exception on a
    # screen the user just tapped a button to reach.
    tabs = TABS.read_text(encoding="utf-8")
    if "?: DEFAULT" not in tabs:
        failures.append("ConsoleTab.of can fail on an unknown name instead of falling back")
    if "valueOf(" in tabs:
        failures.append(
            "ConsoleTab.of uses valueOf, which throws on an unknown name — that is a "
            "crash reachable by any app on the device that can start an activity"
        )
    return failures


def check_the_console_screen_can_reach_every_section() -> list[str]:
    """Getting to Tools must not mean going back to the home screen.

    The console's own nav is inside the WebView, and following one of its links
    is a navigation without the bearer header. So the sections have to be
    reachable from chrome this app draws.
    """
    failures = []
    src = MANAGEMENT.read_text(encoding="utf-8")
    frame = FRAME.read_text(encoding="utf-8") if FRAME.is_file() else ""
    if not frame:
        return [f"there is no {FRAME.relative_to(REPO)}, so nothing builds the tab strip"]
    if "for (entry in ConsoleTab.entries)" not in frame:
        failures.append(
            "the tab strip is not built from ConsoleTab.entries, so a section added to "
            "the console would not appear on the phone"
        )
    if "ConsoleFrame.tabBar(" not in src:
        failures.append(
            "the console screen has no tab strip, so reaching any section other than "
            "the one you arrived at means going back to the home screen"
        )
    if "private fun markCurrentTab()" not in src:
        failures.append("the console screen does not show which section you are on")

    # The phone's own settings wear the same strip.
    #
    # This is the other half of "have the settings for the android app be in
    # that same web view look": the console's sections and the phone's own are
    # one frame with one nav, rather than a native screen you reach from
    # somewhere else and leave by going back. What is UNDER the strip on that
    # screen stays native, and has to — a page in a WebView cannot ask for
    # RECORD_AUDIO or take a battery exemption.
    settings = SETTINGS.read_text(encoding="utf-8")
    if "ConsoleFrame.tabBar(" not in settings:
        failures.append(
            "the phone's settings screen does not wear the console's nav, so it is a "
            "screen off to one side again rather than one of the sections"
        )
    if "onPhone = true" not in settings:
        failures.append(
            "the settings screen does not mark itself as the current section, so the "
            "strip above it says you are somewhere you are not"
        )

    # And the home screen still has nothing hidden past an edge — which is now
    # true because it has one button rather than because a grid was chosen over
    # a scroller. The scroller is right where it is, in the frame, where it
    # mirrors the browser's own nav bar and has six labels to fit.
    main = MAIN.read_text(encoding="utf-8")
    if "HorizontalScrollView" in main:
        failures.append(
            "the home screen's nav scrolls sideways, so whatever is past the right "
            "edge is reachable only by a swipe nothing advertises"
        )
    if "load(tab)" not in src:
        failures.append(
            "RELOAD no longer re-issues the CURRENT section, so it throws you back to "
            "the console's root"
        )

    # Back must not walk into an unauthenticated page.
    #
    # Every navigation this app starts carries the bearer header, and `goBack()`
    # re-issues the entry WITHOUT it. So a tab switch must not leave a
    # back-forward entry at all — otherwise back after two switches lands on
    # whatever the console serves an unauthenticated request, inside a WebView,
    # with the tab strip still claiming you are somewhere else.
    if "view.clearHistory()" not in src:
        failures.append(
            "a tab switch leaves a back-forward entry. Going back re-issues that "
            "navigation without the bearer header, so back lands on a login page "
            "inside a WebView with no way to type into it."
        )
    if "resettingHistory" not in src:
        failures.append(
            "nothing distinguishes an app-initiated navigation from the page's own, so "
            "the history reset either never fires or fires on every page the console "
            "renders"
        )
    return failures


def main() -> int:
    for path in (TABS, MANAGEMENT, MAIN, SETTINGS, LAYOUT):
        if not path.is_file():
            print(f"FAIL  {path} is missing", file=sys.stderr)
            return 1
    failures = (
        check_the_two_navs_are_one()
        + check_every_tab_is_a_real_route()
        + check_the_mobile_half_is_named_for_itself()
        + check_the_phones_own_screens_do_not_borrow_the_consoles_words()
        + check_the_console_names_the_phones_screens_correctly()
        + check_the_webview_is_not_handed_a_url()
        + check_the_console_hides_the_nav_the_frame_already_draws()
        + check_the_console_screen_can_reach_every_section()
    )
    for failure in failures:
        print(f"FAIL  {failure}", file=sys.stderr)
    if failures:
        print(f"\n{len(failures)} failure(s)", file=sys.stderr)
        return 1
    print(
        f"console parity: {len(phone_tabs())} sections, the same on the phone and in a "
        "browser, each a real route, with the mobile half named for itself and the "
        "authenticated WebView never handed a path"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
