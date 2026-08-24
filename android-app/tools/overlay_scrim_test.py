#!/usr/bin/env python3
"""Executable spec for the assist overlay's readability.

## Why this exists

*"it is hard to read text/view the entire orb as text behind the orb is still
rendering, can we make a blur around the text and orb so it can be understood
easier?"*

The overlay is a transparent window over whatever the user was doing, so the
app underneath draws through the orb and through every line of text. The fix
is a blur where the platform has one and a radial scrim everywhere, and the
scrim is a handful of numbers that decide whether the words are readable.
Numbers can be checked; a screenshot on one emulator profile cannot say much.

Two properties, and the second is the one that will rot:

1. **It is dark enough where the words are.** Checked as real WCAG contrast
   against the worst case — white content behind — by compositing the scrim
   over white and measuring the actual text colours against the result.
2. **It has no edge.** Two earlier versions of this surface were a dark
   rounded card with a cyan stroke, and both were removed because the frame
   became the first thing you saw. A gradient that has not reached zero by the
   window's boundary is that card again, drawn a different way.

And one invariant that is easy to lose: **legibility must not depend on API
31**. Cross-window blur is 31+, and false even there under battery saver, with
the developer option off, or on hardware that cannot do it. If the scrim ever
ends up inside an SDK_INT guard, every phone below 31 silently goes back to
unreadable — with nothing failing.

Run:  python3 android-app/tools/overlay_scrim_test.py
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
KOTLIN = ANDROID / "app/src/main/kotlin/ai/jarvis/app"

SCRIM = KOTLIN / "ui/ReadabilityScrim.kt"
OVERLAY = KOTLIN / "assist/AssistOverlay.kt"
ACTIVITY = KOTLIN / "JarvisAssistActivity.kt"
#: The third orb surface. It draws the orb FULL-BLEED behind the question, so
#: every line sits on top of the plates — brightest exactly where the text is
#: largest. Its opaque window background does not help: the competing thing is
#: in front of it, not behind. It was left out when the other two got a ground,
#: which is how three surfaces a user meets interchangeably come to disagree.
ASK = KOTLIN / "companion/CompanionAskActivity.kt"

#: Every surface that draws the orb with words over it. A fourth belongs here.
ORB_SURFACES = (OVERLAY, ACTIVITY, ASK)
JARVIS_UI = KOTLIN / "ui/JarvisUi.kt"

#: WCAG AA for body text.
AA = 4.5

#: The worst thing that can be behind a transparent overlay: a white page.
WORST_CASE_BEHIND = (255, 255, 255)


def source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def scrim_numbers() -> dict:
    """The gradient, as the four stops and the colour the Kotlin actually uses."""
    src = source(SCRIM)
    strength = float(re.search(r"strength:\s*Float\s*=\s*([\d.]+)f", src).group(1))
    focus_y = float(re.search(r"focusY:\s*Float\s*=\s*([\d.]+)f", src).group(1))
    radius_factor = float(
        re.search(r"maxOf\(bounds\.width\(\), bounds\.height\(\)\) \* ([\d.]+)f", src).group(1)
    )
    stops = [float(v) for v in re.findall(r"([\d.]+)f", re.search(
        r"floatArrayOf\(([^)]*)\)", src).group(1))]
    fractions = [float(v) for v in re.findall(
        r"core \* ([\d.]+)f", src)]
    rgb = tuple(
        int(v) for v in re.search(
            r"Color\.argb\(core, (\d+), (\d+), (\d+)\)", src).groups()
    )
    return {
        "strength": strength,
        "focus_y": focus_y,
        "radius_factor": radius_factor,
        "stops": stops,
        # The alpha multiplier at each stop: 1.0, then the ones written as
        # `core * x`, then 0 for the transparent end.
        "alphas": [1.0] + fractions + [0.0],
        "rgb": rgb,
    }


def alpha_at(numbers: dict, distance: float) -> float:
    """Scrim alpha 0..1 at `distance` expressed as a fraction of the radius."""
    stops, alphas = numbers["stops"], numbers["alphas"]
    if len(stops) != len(alphas):
        raise ValueError(f"{len(stops)} stops but {len(alphas)} alpha values")
    if distance <= stops[0]:
        return numbers["strength"] * alphas[0]
    if distance >= stops[-1]:
        return numbers["strength"] * alphas[-1]
    for i in range(1, len(stops)):
        if distance <= stops[i]:
            span = stops[i] - stops[i - 1]
            t = 0.0 if span == 0 else (distance - stops[i - 1]) / span
            blended = alphas[i - 1] + (alphas[i] - alphas[i - 1]) * t
            return numbers["strength"] * blended
    return 0.0


def composite(fg: tuple, alpha: float, bg: tuple) -> tuple:
    return tuple(fg[i] * alpha + bg[i] * (1 - alpha) for i in range(3))


def luminance(rgb: tuple) -> float:
    channels = [c / 255 for c in rgb]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: tuple, b: tuple) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def text_colours() -> dict:
    """What the overlay writes its two lines in.

    The transcript colour is read from the OVERLAY, not from `JarvisUi`: this
    surface overrides it, because the shared one is the palette's quiet colour
    and needs a nearly opaque ground to be legible over a stranger's app.
    Reading the shared one would measure a colour that is not on screen.
    """
    ui = source(JARVIS_UI)
    overlay = source(OVERLAY)
    token = re.search(r"setTextColor\(JarvisUi\.([A-Z_]+)\)", overlay)
    if not token:
        raise ValueError(
            "AssistOverlay no longer sets its own transcript colour, so it is "
            "drawing the palette's dimmest one over arbitrary content"
        )
    # JarvisUi's colours are aliases of the generated JarvisTokens (design/build.py),
    # so the hex is read from the constant the alias points at.
    alias = re.search(rf"const val {token.group(1)} = JarvisTokens\.Color\.([A-Z_0-9]+)", ui)
    generated = source(KOTLIN / "ui/theme/JarvisTokens.kt")
    value = None
    if alias:
        value = re.search(rf"const val {alias.group(1)} = 0x..([0-9A-Fa-f]{{6}})", generated)
    if value is None:
        value = re.search(rf"const val {token.group(1)} = 0x..([0-9A-Fa-f]{{6}})", ui)
    if value is None:
        raise ValueError(f"JarvisUi has no colour {token.group(1)}")
    hexv = value.group(1)
    return {
        # The response, which is the sentence that matters most.
        "response": (255, 255, 255),
        "transcript": tuple(int(hexv[i : i + 2], 16) for i in (0, 2, 4)),
    }


def check_the_words_are_readable_over_anything() -> list[str]:
    """Contrast where the text sits, against white content behind."""
    failures = []
    numbers = scrim_numbers()
    try:
        colours = text_colours()
    except ValueError as e:
        # A missing colour is a finding, not a crash: this runs in CI beside
        # thirty other specs and a traceback reads as the spec being broken
        # rather than as the app being wrong.
        return [str(e)]

    # Where the text actually is, as a fraction of the gradient's radius.
    #
    # The column is orb (top), caption, tools, transcript, response. The focus
    # sits at focusY of the height, so the text below it is roughly a third to
    # two-thirds of the way out to the radius. Sampled across that span rather
    # than at one point, because a gradient that is fine at its centre and
    # gone by the first line is the failure being looked for.
    for label, where in (("the first line", 0.35), ("the middle", 0.5), ("the last line", 0.68)):
        alpha = alpha_at(numbers, where)
        ground = composite(numbers["rgb"], alpha, WORST_CASE_BEHIND)
        for name, colour in colours.items():
            ratio = contrast(colour, ground)
            if ratio < AA:
                failures.append(
                    f"the {name} at {label} of the scrim is {ratio:.2f}:1 over white "
                    f"content behind (scrim alpha {alpha:.2f}), under WCAG AA's {AA}:1 — "
                    "which is the case the overlay exists in, since it floats over "
                    "whatever the user was already looking at"
                )
    return failures


def check_the_scrim_has_no_edge() -> list[str]:
    """A gradient still visible at the window's boundary is a card again."""
    failures = []
    numbers = scrim_numbers()

    # The furthest any pixel of a rectangle sits from a centre placed at
    # focusY, in units of the gradient's radius: the far corner.
    #
    # Worst case is the widest realistic card. The window is capped at 340dp
    # wide and the content is roughly as tall, so a square is the shape that
    # puts its corners furthest out relative to `maxOf(width, height)`.
    half_diagonal = math.hypot(0.5, max(numbers["focus_y"], 1 - numbers["focus_y"]))
    corner = half_diagonal / numbers["radius_factor"]
    alpha = alpha_at(numbers, corner)
    if alpha > 0.02:
        failures.append(
            f"the scrim is still {alpha:.3f} opaque at the card's corner, so it ends on "
            "a visible rectangle — which is the panel that has already been removed "
            "from this surface twice"
        )
    if numbers["alphas"][-1] != 0.0:
        failures.append("the gradient's last stop is not fully transparent")
    return failures


def check_legibility_does_not_depend_on_the_platform() -> list[str]:
    """The scrim must be unconditional; only the blur may be version-gated."""
    failures = []
    for path in ORB_SURFACES:
        src = source(path)
        if "ReadabilityScrim()" not in src:
            failures.append(
                f"{path.name} does not draw a ReadabilityScrim, so its text is over "
                "whatever the user was doing with nothing behind it"
            )
            continue
        # The construction must not sit inside an SDK_INT branch. Checked by
        # looking at what precedes it in the same function: a version guard
        # anywhere above it in the block would make every phone under API 31
        # unreadable, and nothing would fail.
        head = src[: src.index("ReadabilityScrim()")]
        block = head.rsplit("private fun", 1)[-1]
        if "Build.VERSION.SDK_INT" in block:
            failures.append(
                f"{path.name} builds its scrim under a Build.VERSION.SDK_INT check. "
                "Cross-window blur is 31+ and refusable; the scrim is what makes this "
                "readable on everything else, and must not be gated with it."
            )

        # The blur is only meaningful where something else is showing THROUGH
        # the window. The question screen is opaque and full-screen by design —
        # it lights up a locked phone — so there is nothing behind it to blur,
        # and asking for one would be a no-op that reads as intent.
        if path is ASK:
            continue
        if "FLAG_BLUR_BEHIND" not in src:
            failures.append(f"{path.name} never asks for a blur behind the card")
        elif "Build.VERSION_CODES.S" not in src:
            failures.append(
                f"{path.name} sets FLAG_BLUR_BEHIND without an API 31 guard — "
                "blurBehindRadius does not exist below it"
            )
    return failures


def check_the_two_surfaces_agree() -> list[str]:
    """The popup and the overlay are two routes to one orb."""
    failures = []
    radii = {}
    for path in (OVERLAY, ACTIVITY):
        found = re.search(r"BLUR_DP = (\d+)", source(path))
        if not found:
            failures.append(f"{path.name} states no blur radius")
        else:
            radii[path.name] = int(found.group(1))
    if len(set(radii.values())) > 1:
        failures.append(
            "the wake-word overlay and the assist popup blur by different amounts "
            f"({radii}) — they are two routes to the same orb and a user meets both"
        )
    return failures


def main() -> int:
    for path in (SCRIM, JARVIS_UI, *ORB_SURFACES):
        if not path.is_file():
            print(f"FAIL  {path} is missing", file=sys.stderr)
            return 1

    failures = (
        check_the_words_are_readable_over_anything()
        + check_the_scrim_has_no_edge()
        + check_legibility_does_not_depend_on_the_platform()
        + check_the_two_surfaces_agree()
    )
    for failure in failures:
        print(f"FAIL  {failure}", file=sys.stderr)
    if failures:
        print(f"\n{len(failures)} failure(s)", file=sys.stderr)
        return 1

    numbers = scrim_numbers()
    edge = alpha_at(numbers, math.hypot(0.5, 1 - numbers["focus_y"]) / numbers["radius_factor"])
    print(
        "overlay scrim: both lines clear WCAG AA over white content behind, "
        f"and the gradient is {edge:.3f} opaque at the corner — no edge, no card"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
