#!/usr/bin/env python3
"""Executable spec: a voice assistant that could not describe its own screen.

Grep `app/src/main/kotlin` for `contentDescription`, `announceForAccessibility`
or `accessibilityLiveRegion`, and until this file existed the only hits were in
`automation/accessibility/` — the module that **reads other apps' screens** for
the automation engine. Jarvis could drive another app's UI on a blind user's
behalf and could not say a word about its own.

Specifically:

  * **The orb was nothing.** `JarvisOrbView` draws everything itself, so there
    is no text for TalkBack to find. It is the largest thing on four screens and
    the *only* thing on the wake-word overlay, and it was announced as an
    unlabelled `View`. A blind user who said "Hey Jarvis" got a screen with no
    content on it.
  * **State transitions were silent.** LISTENING → PROCESSING → RESPONDING is
    the entire feedback a turn gives, drawn as a caption nothing read out. Being
    in the accessibility tree is not the same as being announced: a description
    is read on focus, and nothing focuses a caption.
  * **Tool activity was three fragments.** Each row is a name, its arguments and
    an outcome in three `TextView`s. Read one at a time TalkBack says "weather",
    "kitchen", "412ms" as three unrelated things; the row is the sentence.
  * **The consent and question screens.** The two screens where getting it wrong
    is irreversible. `ApprovalActivity`'s action id is read as one run-on word
    (`media.play_on_speaker`), its auto-deny countdown ran to zero in silence,
    and `CompanionAskActivity`'s question text changes under the user when the
    keyguard gate lifts — with nobody told.

## What this file can and cannot see

It is a static reader. It cannot run TalkBack, it cannot tell a good label from
a bad one, and it deliberately does not try: a check that only counted calls
would be satisfied by `contentDescription = ""`, which is *worse* than no label
(TalkBack reads such a control as unlabelled AND says nothing about it).

What it does instead is hold each conversation surface to a named requirement —
this screen shows live state, therefore it must have a live region; this screen
asks for consent, therefore its question must be announced — and require that
labels go through `JarvisUi`, so a new screen inherits them instead of having to
remember. The list of surfaces is the check: adding a screen without adding it
here is the omission, and it is a visible one.

Run:  python3 android-app/tools/accessibility_labels_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
KOTLIN = ANDROID / "app/src/main/kotlin/ai/jarvis/app"
STRINGS = ANDROID / "app/src/main/res/values/strings.xml"

#: The module that reads OTHER apps' screens. Everything in it mentions
#: accessibility for a completely different reason and none of it labels
#: anything of ours.
AUTOMATION_A11Y = KOTLIN / "automation/accessibility"


def code(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


def kotlin_files() -> list[Path]:
    return sorted(KOTLIN.rglob("*.kt"))


# ---------------------------------------------------------------------------
# 1. the helpers exist and are the only way in
# ---------------------------------------------------------------------------


def test_jarvisui_offers_the_three_things_a_screen_needs() -> None:
    """One place, so a screen does not have to write `sendAccessibilityEvent`
    boilerplate — because a screen that has to will not."""
    src = code(KOTLIN / "ui/JarvisUi.kt")
    for fn, why in (
        ("fun liveRegion(", "nothing can mark a region TalkBack re-reads"),
        ("fun announce(", "nothing can say something that is not a text change"),
        ("fun describe(", "nothing can label a control"),
    ):
        assert fn in src, f"JarvisUi has no {fn.split('(')[0]}: {why}"
    live = re.search(r"fun liveRegion\(.*?\n    \}", src, re.S)
    assert live and "POLITE" in live.group(0), (
        "the live region is ASSERTIVE, which interrupts whatever the user is "
        "listening to — including Jarvis's own reply, several times a turn"
    )
    describe = re.search(r"fun describe\(.*?\n    \}", src, re.S)
    assert describe and "isNotBlank()" in describe.group(0), (
        "describe() can set an EMPTY content description, which TalkBack reads "
        "as an unlabelled control AND then says nothing about — worse than no "
        "description at all"
    )


def test_no_screen_sets_an_empty_content_description() -> None:
    """The check that a naive "count the calls" rule would have missed."""
    offenders = []
    for path in kotlin_files():
        if AUTOMATION_A11Y in path.parents:
            continue
        if re.search(r'contentDescription\s*=\s*""', code(path)):
            offenders.append(str(path.relative_to(KOTLIN)))
    assert not offenders, (
        "an empty content description is a description, and TalkBack announces "
        "the control as unlabelled and silent: " + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 2. the orb
# ---------------------------------------------------------------------------


def test_the_orb_says_what_it_is_and_what_it_is_doing() -> None:
    """A custom View that paints everything has no text to find."""
    src = code(KOTLIN / "ui/JarvisOrbView.kt")
    assert "contentDescription" in src, (
        "the orb is unlabelled again — on the wake overlay it is the ONLY thing "
        "on screen, so that is a surface with no content at all"
    )
    assert "announceForAccessibility" in src, (
        "the orb's state changes are not announced. A description is read on "
        "focus and nothing focuses an orb, so the whole of a turn's feedback "
        "would be silent."
    )
    set_mode = re.search(r"fun setMode\(newMode: Mode\) \{.*?\n        blendFrom", src, re.S)
    assert set_mode and "describeSelf()" in set_mode.group(0), (
        "changing the orb's mode does not update what it says it is doing"
    )
    label = re.search(r"fun setStateLabel\(.*?\n    \}", src, re.S)
    assert label and "describeSelf()" in label.group(0), (
        "the state caption changes without the description following it"
    )
    assert "isAttachedToWindow" in src, (
        "an announcement from a detached view is dropped by the platform, and "
        "nothing here notices"
    )


# ---------------------------------------------------------------------------
# 3. every surface that shows live state has a live region
# ---------------------------------------------------------------------------

#: file -> what it shows that changes under the user, and therefore must be
#: announced without them touching anything.
#:
#: This list IS the check. A new conversation surface that is not in it ships
#: unlabelled, and adding one here is how the next author finds out.
LIVE_SURFACES: dict[str, str] = {
    "ui/JarvisUi.kt":
        "transcriptView and responseView — the words of the conversation, on "
        "every surface that has one",
    "JarvisAssistActivity.kt":
        "the state caption: the one line saying whether Jarvis is listening, "
        "thinking or speaking",
    "companion/CompanionAskActivity.kt":
        "the question, which is replaced when the keyguard gate lifts",
    "ApprovalActivity.kt":
        "the auto-deny countdown, and the line saying why the buttons are dead",
    "ManagementActivity.kt":
        "whether the console loaded, failed, or is still coming",
}


def test_every_live_surface_marks_its_live_region() -> None:
    missing = []
    for name, what in LIVE_SURFACES.items():
        src = code(KOTLIN / name)
        if "JarvisUi.liveRegion(" not in src and "liveRegion(this)" not in src:
            missing.append(f"{name} ({what})")
    assert not missing, (
        "these change under the user with nothing announced, so a blind user "
        "gets a screen that appears to have stopped: " + "; ".join(missing)
    )


def test_the_conversation_words_are_live_wherever_they_are_drawn() -> None:
    """Done in `JarvisUi` rather than per screen, so the wake overlay, the
    assist card and the home screen cannot each forget separately."""
    src = code(KOTLIN / "ui/JarvisUi.kt")
    for fn in ("fun transcriptView(", "fun responseView("):
        block = re.search(re.escape(fn) + r".*?\n    \}", src, re.S)
        assert block, f"JarvisUi has no {fn.split('(')[0]}"
        assert "liveRegion(this)" in block.group(0), (
            f"{fn.split('(')[0]} is not a live region, so the words of the "
            "conversation are drawn and never spoken"
        )


# ---------------------------------------------------------------------------
# 4. rows that are sentences
# ---------------------------------------------------------------------------


def test_a_tool_row_is_read_as_one_thing() -> None:
    src = code(KOTLIN / "assist/ToolActivityView.kt")
    assert "JarvisUi.describe(" in src, (
        "the tool-activity rows are unlabelled, so what Jarvis is touching is "
        "invisible to a screen reader"
    )
    assert "isImportantForAccessibility = false" in src, (
        "the row's three text fragments are still read separately: TalkBack "
        "says 'weather', 'kitchen', '412ms' as three unrelated things"
    )
    assert "private fun spokenRow(" in src, (
        "there is no sentence for a row, only fragments"
    )
    spoken = re.search(r"private fun spokenRow\(.*?\n        \}", src, re.S)
    assert spoken and "failed" in spoken.group(0), (
        "the row's sentence does not say whether the call failed, which is the "
        "first thing a sighted user sees (the red dot) and the only thing that "
        "matters"
    )


# ---------------------------------------------------------------------------
# 5. the two screens where it is irreversible
# ---------------------------------------------------------------------------


def test_the_consent_screen_reads_its_action_as_words() -> None:
    """`media.play_on_speaker` is one run-on word to a speech synthesiser.

    This is the screen that decides whether something that cannot be undone
    happens, so mishearing WHICH thing is the failure that matters.
    """
    src = code(KOTLIN / "ApprovalActivity.kt")
    assert "JarvisUi.describe(" in src, "the approval screen labels nothing"
    assert re.search(r'replace\(Regex\("\[\._\]"\), " "\)', src), (
        "the action id is handed to TalkBack with its dots and underscores, so "
        "it is announced as one unpronounceable word"
    )
    assert "a11y_approval" in src, "the action is not announced as an approval"


def test_the_question_screen_announces_the_question() -> None:
    src = code(KOTLIN / "companion/CompanionAskActivity.kt")
    assert "JarvisUi.announce(" in src, (
        "the question screen announces nothing; the text is replaced when the "
        "phone is unlocked and a blind user is offered YES and NO under a "
        "sentence TalkBack read as 'Jarvis has a question'"
    )
    assert "a11y_question" in src, "the question is not announced AS a question"
    gate = re.search(r"private fun refreshGate\(\) \{.*?\n    \}", src, re.S)
    assert gate and "JarvisUi.announce(" in gate.group(0), (
        "the keyguard gate replaces the question text without saying so"
    )


# ---------------------------------------------------------------------------
# 6. the strings are strings
# ---------------------------------------------------------------------------


def test_the_spoken_labels_live_in_resources() -> None:
    """Spoken text is the text most in need of translating and the least likely
    to be proof-read, so it does not live in a Kotlin literal."""
    strings = STRINGS.read_text(encoding="utf-8")
    for name in (
        "a11y_orb",
        "a11y_orb_state",
        "a11y_tool_activity",
        "a11y_question",
        "a11y_approval",
    ):
        assert f'name="{name}"' in strings, f"strings.xml has no {name}"


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
          f"({len(LIVE_SURFACES)} live surfaces, {len(kotlin_files())} Kotlin files)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
