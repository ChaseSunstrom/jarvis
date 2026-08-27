#!/usr/bin/env python3
"""Executable spec: the phone and the console draw from one palette.

## Why this exists

*"why dont you just have a Manage button that will show the settings for it in
the web view without the extra tabs and such? and have the settings for the
android app be in that same web view look? so we can dedup the things"*

The phone's own settings sit inside the console's frame now, under the tab
strip the console's sections use. That makes one thing out of two, and it is
also the moment two palettes stop being harmless: they were never on screen
together before, so nobody could see that three of `JarvisUi`'s eight colours
matched a `--jv-*` token and five were near misses.

So the Kotlin names a token per colour and this checks it. The values now come
from one source, `design/tokens.json`, through the generated `JarvisTokens.kt`
(`python3 design/build.py`); what this pins is that `JarvisUi`'s aliases point
at the right tokens and that the generated constants still equal the source.

## What is deliberately not pinned

`SiriPalette` is the orb's colours and has its own mirror in
`reactor_orb_test.py`, which compares them against the shader. Those are state
colours for one object, not the app's chrome, and folding them in here would
give one file two jobs and the orb two specs.

## Contrast

Every text colour is also checked for WCAG AA against the ground it is drawn
on. This is not decoration: `FAINT` was `#5A7A86`, which is 4.38:1 on
`--jv-bg` — under AA — and it is the colour every hint on every screen of the
app is drawn in. It was found by this check, not by looking.

Run:  python3 android-app/tools/design_token_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
REPO = ANDROID.parent

JARVIS_UI = ANDROID / "app/src/main/kotlin/ai/jarvis/app/ui/JarvisUi.kt"
JARVIS_TOKENS = ANDROID / "app/src/main/kotlin/ai/jarvis/app/ui/theme/JarvisTokens.kt"
TOKENS = REPO / "design/tokens.json"

#: WCAG AA for body text. The chrome here is small and mostly monospace, so the
#: large-text allowance (3:1) is not the one that applies.
AA = 4.5


def web_tokens() -> dict[str, str]:
    """`design/tokens.json` colour leaves -> {'--jv-accent': '4fe3ff'}. Hex only.

    The source of truth is the JSON now; `tokens.ts` and `JarvisTokens.kt` are
    both generated from it by `design/build.py`, so comparing the phone against
    the JSON is comparing it against the console too. `rgba(...)` tokens are
    skipped rather than parsed: nothing on the phone names one.
    """
    import json

    data = json.loads(TOKENS.read_text(encoding="utf-8"))
    out: dict[str, str] = {}

    def walk(node: dict, path: list[str]) -> None:
        if "$value" in node:
            value = str(node["$value"])
            if re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                out["--jv-" + "-".join(path)] = value.lstrip("#").lower()
            return
        for key, child in node.items():
            if not key.startswith("$") and isinstance(child, dict):
                walk(child, path + [key])

    walk(data["color"], [])
    return out


def generated_colours() -> dict[str, tuple[str, str]]:
    """`const val NAME = 0xAARRGGBB.toInt() // --jv-token` in JarvisTokens.kt."""
    out: dict[str, tuple[str, str]] = {}
    for line in JARVIS_TOKENS.read_text(encoding="utf-8").splitlines():
        m = re.search(
            r"const val ([A-Z_0-9]+) = 0x([0-9A-Fa-f]{8})\.toInt\(\)\s*//\s*(--jv-[a-z0-9-]+)", line
        )
        if m:
            out[m.group(1)] = (m.group(2).lower()[2:], m.group(3))
    return out


def kotlin_colours(src: str) -> dict[str, tuple[str, str]]:
    """`const val NAME = JarvisTokens.Color.X // --jv-token` -> {NAME: (rgb, token)}.

    JarvisUi no longer holds a hex of its own: every colour is an alias of a
    generated constant, and the alias line still names its token so that a
    colour with no stated token — the thing this spec exists to prevent — is
    still a failure rather than a private palette growing back one constant at
    a time. The rgb comes from the generated file the alias points at.
    """
    generated = generated_colours()
    out: dict[str, tuple[str, str]] = {}
    for line in src.splitlines():
        m = re.search(
            r"const val ([A-Z_]+) = JarvisTokens\.Color\.([A-Z_0-9]+)(?:\s*//\s*(--jv-[a-z-]+))?",
            line,
        )
        if not m:
            continue
        name, target, token = m.group(1), m.group(2), m.group(3)
        rgb = generated.get(target, ("", ""))[0]
        out[name] = (rgb, token or "")
    literal = re.search(r"const val [A-Z_]+ = 0x[0-9A-Fa-f]{8}\.toInt\(\)", src)
    if literal:
        out["__LITERAL__"] = ("", "")
    return out


def luminance(rgb: str) -> float:
    channels = [int(rgb[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def check_every_colour_names_a_token() -> list[str]:
    failures = []
    colours = kotlin_colours(JARVIS_UI.read_text(encoding="utf-8"))
    if not colours:
        return ["JarvisUi declares no colours, or they are no longer const val ARGB ints"]
    for name, (_rgb, token) in sorted(colours.items()):
        if name == "__LITERAL__":
            failures.append(
                "JarvisUi declares a colour as a hex literal; every colour is an alias of "
                "the generated JarvisTokens (python3 design/build.py)"
            )
            continue
        if not token:
            failures.append(
                f"JarvisUi.{name} names no --jv-* token, so it is a private colour the "
                "console knows nothing about — which is how the two palettes drifted"
            )
    return failures


def check_the_two_palettes_agree() -> list[str]:
    failures = []
    colours = kotlin_colours(JARVIS_UI.read_text(encoding="utf-8"))
    tokens = web_tokens()
    if not tokens:
        return [f"no --jv-* hex tokens found in {TOKENS.relative_to(REPO)}"]

    for name, (rgb, token) in sorted(colours.items()):
        if not token or name == "__LITERAL__":
            continue  # already reported
        want = tokens.get(token)
        if want is None:
            failures.append(
                f"JarvisUi.{name} names {token}, which the console does not declare. "
                "A token that exists on one surface only is not a shared token."
            )
        elif want != rgb:
            failures.append(
                f"JarvisUi.{name} is #{rgb} and {token} is #{want}: the phone and the "
                "console draw the same idea in two colours, and they are now shown "
                "under one nav where that is visible"
            )
    return failures


def check_the_text_colours_are_legible() -> list[str]:
    """Every colour text is drawn in, against the ground it is drawn on.

    APPROVE and DENY are included: they are the consent prompt's two answers,
    and that is the single most consequential sentence this app ever shows.
    SURFACE is a fill rather than text and is checked the other way round — as
    the ground the body colour sits on.
    """
    failures = []
    colours = kotlin_colours(JARVIS_UI.read_text(encoding="utf-8"))
    ground = colours.get("BG", ("04070c", ""))[0]

    for name in ("ACCENT", "DIM", "FAINT", "APPROVE", "DENY", "GOLD"):
        entry = colours.get(name)
        if entry is None:
            failures.append(f"JarvisUi has no {name}")
            continue
        ratio = contrast(entry[0], ground)
        if ratio < AA:
            failures.append(
                f"JarvisUi.{name} (#{entry[0]}) is {ratio:.2f}:1 on the app's ground "
                f"(#{ground}), under WCAG AA's {AA}:1. That is not a preference — it is "
                "the colour some sentence on some screen is written in."
            )

    surface = colours.get("SURFACE")
    dim = colours.get("DIM")
    if surface and dim:
        ratio = contrast(dim[0], surface[0])
        if ratio < AA:
            failures.append(
                f"JarvisUi.DIM on JarvisUi.SURFACE is {ratio:.2f}:1, under AA — panels "
                "are where most of the app's text actually sits"
            )
    return failures


def main() -> int:
    for path in (JARVIS_UI, JARVIS_TOKENS, TOKENS):
        if not path.is_file():
            print(f"FAIL  {path} is missing", file=sys.stderr)
            return 1

    failures = (
        check_every_colour_names_a_token()
        + check_the_two_palettes_agree()
        + check_the_text_colours_are_legible()
    )
    for failure in failures:
        print(f"FAIL  {failure}", file=sys.stderr)
    if failures:
        print(f"\n{len(failures)} failure(s)", file=sys.stderr)
        return 1

    colours = kotlin_colours(JARVIS_UI.read_text(encoding="utf-8"))
    print(
        f"design tokens: {len(colours)} phone colours, each an alias of a generated token "
        f"design/tokens.json declares, all legible on the ground they are drawn on"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
