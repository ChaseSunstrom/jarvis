#!/usr/bin/env python3
"""Executable spec: type and spacing get the treatment the colours already had.

`JarvisUi`'s colours are tokens, and `design_token_test.py` holds every one of
them against `jarvis-web/src/lib/tokens.ts`. That check exists because the phone
and the console were found to be running two palettes that merely *looked* alike
— three of eight matched and five were near misses nobody could see were misses,
one of them a hint colour at 4.38:1 on this ground, under WCAG AA, used for every
explanatory line on every screen.

Type and spacing had the identical problem and no such treatment. Sizes were
inline SP literals — 11, 12, 13, 14, 15, 20, 21, 22 — and paddings were
`dp(ctx, 10)`, `dp(ctx, 12)`, `dp(ctx, 14)`, `dp(ctx, 16)`, `dp(ctx, 20)`,
`dp(ctx, 24)`, scattered across every activity with nothing to say which was
which. "The same size as a hint" was a number somebody remembered.

It had already drifted: `CompanionAskActivity` drew its question at 21sp against
`JarvisUi.responseView`'s 20sp. Both are Jarvis speaking, on surfaces a user
meets interchangeably, and nobody could state why they differed — which is the
same sentence the colour comment ends with.

## What this file does NOT do

It does not impose a new rhythm. The steps are the ones that were already in
use, named — so this is a rename, and the check is that the names are used
rather than that the numbers changed. Nor does it forbid a literal outright: a
one-off size on a one-off surface is a legitimate thing, and a rule that
outlawed it would be routed around with a constant called `SIZE_19`. What it
requires is that the SHARED builders — the ones every screen goes through — use
the scale, so a new screen inherits it rather than picking a number.

Run:  python3 android-app/tools/type_scale_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
KOTLIN = ANDROID / "app/src/main/kotlin/ai/jarvis/app"
UI = KOTLIN / "ui/JarvisUi.kt"


def code(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


#: The shared builders. Every screen in the app draws through these, so this is
#: the population where a literal costs the most and is cheapest to remove.
SHARED_BUILDERS = (
    "transcriptView",
    "responseView",
    "title",
    "label",
    "hint",
    "mono",
    "field",
    "pill",
    "ghost",
    "consentButton",
    "checkRow",
    "banner",
)


def builder_bodies() -> dict[str, str]:
    src = code(UI)
    out: dict[str, str] = {}
    for name in SHARED_BUILDERS:
        match = re.search(rf"\n    fun {name}\((.*?)\n    \}}", src, re.S)
        if match:
            out[name] = match.group(0)
    return out


def test_the_scale_exists_and_is_named_for_the_job() -> None:
    src = code(UI)
    assert "object Type {" in src, "JarvisUi has no type scale"
    assert "object Space {" in src, "JarvisUi has no spacing scale"
    steps = re.findall(r"const val (\w+) = [\d.]+f", src)
    # Named for what they are FOR. A scale called SIZE_11/SIZE_12 is the
    # literals with extra steps: it still cannot answer "which one is a hint".
    assert not any(re.fullmatch(r"(SP|SIZE|TEXT)_?\d+", s) for s in steps), (
        "the type steps are named after their numbers, which tells a caller "
        "nothing it did not already know from the number"
    )
    for name in ("LABEL", "HINT", "BODY", "TITLE", "RESPONSE"):
        assert f"const val {name} = " in src, f"the type scale has no {name} step"
    for name in ("TIGHT", "ROW", "GAP", "SECTION", "SCREEN"):
        assert f"const val {name} = " in src, f"the spacing scale has no {name} step"


def test_every_shared_builder_sizes_its_text_from_the_scale() -> None:
    bodies = builder_bodies()
    assert len(bodies) == len(SHARED_BUILDERS), (
        "a shared builder is missing from JarvisUi: "
        + ", ".join(sorted(set(SHARED_BUILDERS) - set(bodies)))
    )
    offenders = []
    for name, body in bodies.items():
        for literal in re.findall(r"COMPLEX_UNIT_SP,\s*([\d.]+f)", body):
            offenders.append(f"{name} ({literal})")
    assert not offenders, (
        "these shared builders size their text with a literal, so every screen "
        "that uses them inherits a number instead of a step: "
        + ", ".join(offenders)
    )


def test_the_scale_kept_the_sizes_that_were_already_there() -> None:
    """A rename, not a redesign.

    If somebody later wants a bigger type scale that is a real decision to take
    deliberately — and the phone's steps are the console's, so it is a decision
    to take in both places at once. This check is what makes it deliberate.
    """
    src = code(UI)
    expected = {
        "LABEL": "11f",
        "HINT": "12f",
        "MONO": "13f",
        "BODY": "14f",
        "FIELD": "15f",
        "RESPONSE": "20f",
        "TITLE": "22f",
    }
    for name, value in expected.items():
        match = re.search(rf"const val {name} = ([\d.]+f)", src)
        assert match, f"the {name} step is gone"
        assert match.group(1) == value, (
            f"the {name} step moved from {value} to {match.group(1)}. That is a "
            "visual change to every screen at once, and the console's scale is "
            "the other half of it — change both, or neither."
        )


def test_the_question_screen_and_the_response_view_are_one_size() -> None:
    """The drift this file was written about.

    `CompanionAskActivity` drew Jarvis's question at 21sp; `responseView` draws
    Jarvis's answer at 20sp. Both are Jarvis speaking, on surfaces a user meets
    interchangeably.
    """
    ask = code(KOTLIN / "companion/CompanionAskActivity.kt")
    question = re.search(r"questionView = TextView\(ctx\).apply \{.*?\n        \}", ask, re.S)
    assert question, "the question view is gone"
    assert "JarvisUi.Type.RESPONSE" in question.group(0), (
        "the question is sized with a literal again, so it can drift from the "
        "answer it is the same kind of thing as"
    )


def test_the_column_default_is_a_step() -> None:
    """`column(padDp = 20)` is the screen margin every screen inherits."""
    src = code(UI)
    match = re.search(r"fun column\(context: Context, padDp: Int = ([^)]+)\)", src)
    assert match, "JarvisUi.column is gone"
    assert "Space." in match.group(1), (
        f"the screen margin is a literal ({match.group(1).strip()}), so every "
        "screen that overrides it is choosing a number rather than a step"
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
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed "
          f"({len(SHARED_BUILDERS)} shared builders)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
