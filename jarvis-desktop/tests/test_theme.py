"""Executable spec: the desktop draws from the same palette as everything else.

## Why this exists

The console, the Android app and this agent are three surfaces of one
assistant. The phone's palette was pinned to ``jarvis-web/src/lib/tokens.ts`` by
``android-app/tools/design_token_test.py`` after three of its eight colours
turned out to match a token and five were near misses nobody could see, because
the two were never on screen together. The desktop's two dialogs were in the
same state and worse: they did not match each other either — system grey with
``TkDefaultFont`` for the consent prompt, system grey with ``TkFixedFont`` for
the companion question.

So this is that test, for this surface. What is pinned is that the surfaces
AGREE, not that either file is right, which is why the expected values are read
out of ``tokens.ts`` rather than written down a third time here.

## Contrast

Every colour text is drawn in is checked for WCAG AA against the ground it is
actually drawn on. That check is not decoration: on Android it found ``FAINT``
at 4.38:1 — under AA — and it was the colour every hint on every screen was
written in. The pairs below are stated explicitly because "which ground" is the
whole question: ``ACCENT_INK`` is a near-black that is illegible on the ground
and correct on a filled button, and a test that checked it against ``BG`` would
be checking something nobody ever draws.

## What is deliberately not pinned

The type scale. ``--jv-fs-*`` are ``rem``; Tk wants points, and a point is not a
fraction of a root font size. A pin that has to be hand-converted is a pin that
will be wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jarvis_desktop import theme

DESKTOP = Path(__file__).resolve().parents[1]
REPO = DESKTOP.parent
TOKENS = REPO / "design/tokens.json"
THEME = DESKTOP / "jarvis_desktop/tokens.py"

#: WCAG AA for body text. This chrome is small and monospace, so the large-text
#: allowance (3:1) is not the one that applies.
AA = 4.5

#: ``(colour, the ground it is drawn on)`` for every colour that is ever text.
#: The button fills appear as grounds, not as text: nobody writes in them.
TEXT_ON_GROUND = (
    ("ACCENT", "BG"),
    ("TEXT", "BG"),
    ("TEXT_BRIGHT", "BG"),
    ("TEXT_DIM", "BG"),
    ("TEXT_FAINT", "BG"),
    ("WARN", "BG"),
    # The read-only slab and the answer field are drawn on PANEL, not BG.
    ("TEXT", "PANEL"),
    ("TEXT_BRIGHT", "PANEL"),
    ("ACCENT_DEEP", "PANEL"),
    # And the ink on each filled button, which is the pairing that matters for
    # the two answers to "may I run this on your machine".
    ("ACCENT_INK", "OK"),
    ("ACCENT_INK", "DANGER"),
    ("ACCENT_INK", "ACCENT"),
)


def web_tokens() -> dict[str, str]:
    """``design/tokens.json`` colour leaves -> ``{'--jv-accent': '4fe3ff'}``. Hex only.

    The JSON is the source of truth; ``tokens.ts`` and ``tokens.py`` are both
    generated from it, so agreeing with the JSON is agreeing with the console.
    ``rgba(...)`` tokens are skipped: Tk has no translucent widget background.
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


def theme_colours() -> dict[str, tuple[str, str]]:
    """``NAME = "#rrggbb"  # --jv-token`` -> ``{NAME: (rgb, token)}``.

    The token comes from the trailing comment or from the ``#:`` comment block
    directly above, because a colour with no stated token is exactly what this
    spec exists to prevent — a fourth palette, growing back one constant at a
    time. Only a CONTIGUOUS run of comment lines is walked, so a colour that
    lost its token cannot quietly adopt the one belonging to the constant above
    it; that mistake made a deleted token report as a mismatch against somebody
    else's colour instead of as the missing token it was.
    """
    lines = THEME.read_text(encoding="utf-8").splitlines()
    out: dict[str, tuple[str, str]] = {}
    for index, line in enumerate(lines):
        match = re.match(
            r'([A-Z][A-Z_]*) = "#([0-9A-Fa-f]{6})"(?:\s*#\s*(--jv-[a-z-]+))?', line
        )
        if not match:
            continue
        name, rgb, token = match.group(1), match.group(2).lower(), match.group(3)
        if token is None:
            for back in range(index - 1, -1, -1):
                above = lines[back].strip()
                if not above.startswith("#"):
                    break
                found = re.search(r"(--jv-[a-z-]+)", above)
                if found:
                    token = found.group(1)
                    break
        out[name] = (rgb, token or "")
    return out


def luminance(rgb: str) -> float:
    channels = [int(rgb[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


# --- the palette is one palette ---------------------------------------------


def test_the_web_tokens_are_readable():
    """A guard on the guard: if `tokens.ts` moves or its shape changes, every
    check below would pass vacuously against an empty dict."""
    tokens = web_tokens()
    assert TOKENS.is_file(), f"{TOKENS} is missing"
    assert len(tokens) >= 10
    assert tokens["--jv-accent"] == "4fe3ff"


def test_the_theme_declares_colours_at_all():
    colours = theme_colours()
    assert len(colours) >= 10, "tokens.py declares no colours, or not as NAME = \"#rrggbb\""


def test_every_desktop_colour_names_a_token():
    """A colour with no ``--jv-*`` beside it is a private colour the console
    knows nothing about, which is how the phone's palette drifted."""
    missing = [name for name, (_rgb, token) in sorted(theme_colours().items()) if not token]
    assert not missing, (
        f"{missing} name no --jv-* token, so nothing can tell whether they match "
        "the console"
    )


def test_the_desktop_and_the_console_agree():
    tokens = web_tokens()
    wrong = []
    for name, (rgb, token) in sorted(theme_colours().items()):
        want = tokens.get(token)
        if want is None:
            wrong.append(f"theme.{name} names {token}, which the console does not declare")
        elif want != rgb:
            wrong.append(f"theme.{name} is #{rgb} but {token} is #{want}")
    assert not wrong, (
        "the desktop and the console draw the same idea in different colours: "
        + "; ".join(wrong)
    )


def test_the_constants_and_the_source_text_are_the_same_values():
    """The parser above reads the file; the dialogs import the module. This is
    the one assertion that ties the two together."""
    for name, (rgb, _token) in theme_colours().items():
        assert getattr(theme, name).lstrip("#").lower() == rgb


# --- and it is legible ------------------------------------------------------


@pytest.mark.parametrize(("fg", "ground"), TEXT_ON_GROUND)
def test_every_text_colour_clears_wcag_aa_on_its_ground(fg, ground):
    colours = theme_colours()
    ratio = contrast(colours[fg][0], colours[ground][0])
    assert ratio >= AA, (
        f"theme.{fg} on theme.{ground} is {ratio:.2f}:1, under WCAG AA's {AA}:1. "
        "That is not a preference — it is the colour some sentence in the consent "
        "prompt is written in."
    )


def test_every_button_fill_is_legible_with_its_own_ink():
    """Nothing may be added to :data:`theme.BUTTON_KINDS` without checking it:
    the two that matter are the answers to a Tier-3 prompt."""
    for kind, (fill, ink) in theme.BUTTON_KINDS.items():
        ratio = contrast(fill.lstrip("#"), ink.lstrip("#"))
        assert ratio >= AA, f"the {kind} button is {ratio:.2f}:1, under AA"


def test_the_chrome_is_monospace():
    """``--jv-font-chrome`` is a monospace stack on the other two surfaces, and
    a proportional dialog does not read as the same product."""
    assert theme.MONO == "TkFixedFont"
