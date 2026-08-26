#!/usr/bin/env python3
"""Executable spec for the arc reactor — the one instrument, on every surface.

The report this file exists for: *"the notification thing (with the orb) is
different than the actual orb that spawns with the wake word, why is this?"* —
there were two orbs, then three, and nothing anywhere said they were supposed
to agree, so they did not.

They are one instrument now (Reactor II, `docs/design/c2-reactor.html`): a
graduated bezel, a ring of blades, a counter-rotating coil, a level arc, and a
dark lens with two iris arcs and one hot dot. What drifts when two renderers
have to be retuned in step by hand is the *geometry*, so the geometry is a
file — `tests/contracts/reactor_geometry.json` — and every renderer is held to
it:

  * `jarvis-web/src/lib/ui/Reactor.svelte` — the web, checked here by reading
    its constants and by `reactor.test.ts` by rendering;
  * `ReactorOrb.kt` — the phone, drawn on Canvas. Its own reading of the
    contract lands with M51; until then this pins what is true of it today —
    that both Android views own one instance of it, and that the five states
    are one table (`SiriPalette`, pinned to `color.orb.*` by
    `design/build.py --check`).
  * At rest (M64) the instrument is the accent's on both surfaces: the web's
    `.reactor` block sets `--rx-live`/`--rx-deep` to `--jv-accent-deep` and
    `--rx-hot` to `--jv-accent` before any `[data-state]` overrides them, and
    `ReactorOrb.Palette` hands the Canvas the same two tokens for IDLE. The
    phone read `SiriPalette.blobs(IDLE)` directly, whose second colour is an
    indigo, so its resting lens was two hues where the console's is one.

Run:  python3 android-app/tools/reactor_orb_test.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
REPO = ANDROID.parent

CONTRACT = REPO / "tests/contracts/reactor_geometry.json"
WEB_REACTOR = REPO / "jarvis-web/src/lib/ui/Reactor.svelte"
REACTOR = ANDROID / "app/src/main/kotlin/ai/jarvis/app/ui/ReactorOrb.kt"
SIRI_VIEW = ANDROID / "app/src/main/kotlin/ai/jarvis/app/ui/SiriOrbView.kt"
HUD_VIEW = ANDROID / "app/src/main/kotlin/ai/jarvis/app/ui/JarvisOrbView.kt"
PALETTE = ANDROID / "app/src/main/kotlin/ai/jarvis/app/ui/SiriPalette.kt"

#: Contract key -> the constant's name in Reactor.svelte (and, from M51, in
#: ReactorOrb.kt). One table, so a renamed key fails here rather than drifting.
KEYS = {
    "ticks": "TICKS",
    "long_tick_every": "LONG_TICK_EVERY",
    "long_tick_len": "LONG_TICK_LEN",
    "short_tick_len": "SHORT_TICK_LEN",
    "blades": "BLADES",
    "blade_gap_deg": "BLADE_GAP_DEG",
    "r_blade": "R_BLADE",
    "blade_width_ratio": "BLADE_WIDTH_RATIO",
    "blade_width_min": "BLADE_WIDTH_MIN",
    "r_coil": "R_COIL",
    "r_level": "R_LEVEL",
    "level_width": "LEVEL_WIDTH",
    "r_core": "R_CORE",
    "iris_a_r": "IRIS_A_R",
    "iris_a_sweep": "IRIS_A_SWEEP",
    "iris_b_r": "IRIS_B_R",
    "iris_b_sweep": "IRIS_B_SWEEP",
    "r_think": "R_THINK",
    "dot_ratio": "DOT_RATIO",
    "dot_min": "DOT_MIN",
    "dot_glow_ratio": "DOT_GLOW_RATIO",
    "dot_glow_min": "DOT_GLOW_MIN",
    "idle_breath_level": "IDLE_BREATH_LEVEL",
}


def contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def constants(src: str, pattern: str) -> dict[str, float]:
    """`NAME = value` declarations matching `pattern` (which captures name, value)."""
    return {m.group(1): float(m.group(2)) for m in re.finditer(pattern, src)}


def check_the_web_reads_the_contract() -> list[str]:
    failures = []
    geo = contract()
    web = constants(WEB_REACTOR.read_text(encoding="utf-8"), r"const ([A-Z_]+) = ([0-9.]+);")
    for key, name in KEYS.items():
        if key not in geo:
            failures.append(f"reactor_geometry.json has no {key}")
        elif name not in web:
            failures.append(f"Reactor.svelte declares no {name} (contract {key})")
        elif abs(web[name] - float(geo[key])) > 1e-9:
            failures.append(f"Reactor.svelte {name} is {web[name]}, the contract says {geo[key]}")
    return failures


def check_the_geometry_is_an_instrument() -> list[str]:
    """The rings have to nest, or the boot's outward assembly plays inward."""
    failures = []
    geo = contract()
    order = ["r_think", "r_core", "r_level", "r_coil", "r_blade"]
    for inner, outer in zip(order, order[1:]):
        if geo[inner] >= geo[outer]:
            failures.append(f"{inner} ({geo[inner]}) is not inside {outer} ({geo[outer]})")
    if geo["r_blade"] >= 1.0:
        failures.append("the blades reach the bezel or past it")
    if geo["blades"] * geo["blade_gap_deg"] >= 360:
        failures.append("the gaps between the blades add up to more than the circle")
    if geo["ticks"] % geo["long_tick_every"]:
        failures.append("the long ticks do not divide the bezel evenly")
    for key in ("iris_a_r", "iris_b_r"):
        if not 0 < geo[key] < 1:
            failures.append(f"{key} is not inside the lens")
    return failures


def check_the_periods_are_tokens() -> list[str]:
    """A duration is a design value: every period names a motion.reactor token."""
    failures = []
    geo = contract()
    tokens = json.loads((REPO / "design/tokens.json").read_text(encoding="utf-8"))
    reactor = tokens["motion"]["reactor"]
    for what, ref in geo["periods"].items():
        parts = ref.split(".")
        if parts[:2] != ["motion", "reactor"] or parts[2] not in reactor:
            failures.append(f"period {what} names {ref}, which is not a motion.reactor token")
    return failures


def check_both_views_draw_the_same_object() -> list[str]:
    failures = []
    for path in (SIRI_VIEW, HUD_VIEW):
        src = path.read_text(encoding="utf-8")
        if "ReactorOrb(" not in src or "reactor.draw(canvas" not in src:
            failures.append(
                f"{path.name} does not draw ReactorOrb. Two hand-maintained copies of "
                "this is what produced two different-looking orbs in the first place."
            )
        # Neither view may keep its own copy of the reactor's primitives.
        for gone in ("private fun drawTicks", "private fun drawDashedRing", "private fun drawBlob"):
            if gone in src:
                failures.append(f"{path.name} still has its own {gone}")
        # Both must size the instrument against the renderer's own outermost
        # radius rather than a number that happens to have been right once.
        if "ReactorOrb.OUTER_FACTOR" not in src:
            failures.append(
                f"{path.name} sizes its reactor against something other than "
                "ReactorOrb.OUTER_FACTOR, so a retuned ring can be clipped into a box"
            )
    return failures


def check_the_state_machines_are_one() -> list[str]:
    """`Mode` carries its own tone, and its colour is the palette's.

    It used to be five ARGB literals on the enum and the same five in
    `SiriPalette`, mapped by a `when` in a third file. Three places, agreeing by
    hand.
    """
    failures = []
    hud = HUD_VIEW.read_text(encoding="utf-8")
    siri = SIRI_VIEW.read_text(encoding="utf-8")

    if "enum class Mode(val tone: SiriPalette.Tone)" not in hud:
        failures.append(
            "JarvisOrbView.Mode no longer carries its tone, so the mapping to the "
            "palette is somewhere else again"
        )
    if "val color: Int get() = SiriPalette.rim(tone)" not in hud:
        failures.append(
            "Mode.color is no longer derived from SiriPalette. A second copy of the "
            "five state colours is a second copy that can disagree."
        )
    if re.search(r"^\s{8}\w+\(0x", hud, re.M):
        failures.append(
            "JarvisOrbView.Mode has hard-coded colours again; they belong in SiriPalette"
        )
    if "fun setMode(mode: JarvisOrbView.Mode) = setTone(mode.tone)" not in siri:
        failures.append(
            "SiriOrbView maps modes to tones itself again rather than asking the mode"
        )

    # Every mode must still resolve to a distinct tone, which is the property the
    # `when` used to be checked for: two states that look alike are two states a
    # person cannot tell apart from across a room.
    modes = re.findall(r"^\s{8}(\w+)\(SiriPalette\.Tone\.(\w+)\)[,;]", hud, re.M)
    if len(modes) != 5:
        failures.append(f"JarvisOrbView.Mode has {len(modes)} states, expected 5")
    tones = [t for _, t in modes]
    if len(set(tones)) != len(tones):
        failures.append("two modes share one tone; those states are indistinguishable")
    return failures


def check_the_phone_reads_the_contract() -> list[str]:
    """From M51: ReactorOrb.kt declares the same constants as the contract.

    Before M51 the phone still draws the previous sphere, and this reports that
    plainly rather than failing the mirror suite for a milestone that has not
    started — the M51 verify script is what turns it into a failure.
    """
    src = REACTOR.read_text(encoding="utf-8")
    if "reactor_geometry.json" not in src:
        print("note: ReactorOrb.kt does not read the geometry contract yet (M51)")
        return []
    failures = []
    geo = contract()
    kotlin = constants(src, r"const val ([A-Z_]+)\s*=\s*([0-9.]+)f?\b")
    for key, name in KEYS.items():
        if name not in kotlin:
            failures.append(f"ReactorOrb.kt declares no {name} (contract {key})")
        elif abs(kotlin[name] - float(geo[key])) > 1e-6:
            failures.append(f"ReactorOrb.kt {name} is {kotlin[name]}, the contract says {geo[key]}")
    return failures


def check_rest_is_the_accents_on_both_surfaces() -> list[str]:
    """IDLE reads `accent.deep` (live and deep) and `accent` (the dot), as the web does.

    Pinned from both ends: the web's resting block must still name those two
    tokens, the phone's `ReactorOrb.Palette` must hand IDLE the same two, and
    both Android views must read the palette rather than `SiriPalette`
    directly — the drift this catches is one view resting in indigo again.
    """
    failures = []
    web = WEB_REACTOR.read_text(encoding="utf-8")
    rest = re.search(r"\.reactor \{(.*?)\}", web, re.S)
    if not rest:
        return ["Reactor.svelte has no `.reactor` block to read the resting palette from"]
    block = rest.group(1)
    for var, token in (
        ("--rx-live", "--jv-accent-deep"),
        ("--rx-deep", "--jv-accent-deep"),
        ("--rx-hot", "--jv-accent"),
    ):
        if not re.search(rf"{re.escape(var)}:\s*var\({re.escape(token)}\)", block):
            failures.append(f"Reactor.svelte's resting block does not set {var} to var({token})")

    src = REACTOR.read_text(encoding="utf-8")
    palette = re.search(r"object Palette \{(.*?)\n    \}", src, re.S)
    if not palette:
        return failures + ["ReactorOrb.kt has no Palette object; IDLE reads SiriPalette's indigo again"]
    body = palette.group(1)
    blobs = re.search(r"fun blobs\(.*?SiriPalette\.Tone\.IDLE\)\s*\{\s*\n\s*intArrayOf\(([^)]*)\)", body, re.S)
    if not blobs or set(c.strip() for c in blobs.group(1).split(",")) != {"JarvisTokens.Color.ACCENT_DEEP"}:
        failures.append("ReactorOrb.Palette.blobs(IDLE) is not accent-deep for live and deep alike")
    if not re.search(r"fun core\(.*?Tone\.IDLE\) JarvisTokens\.Color\.ACCENT\b", body, re.S):
        failures.append("ReactorOrb.Palette.core(IDLE) is not the accent")

    # `SiriPalette.rim(` is allowed: it is `Mode.color`, the chrome's tint, which
    # `check_the_state_machines_are_one` pins to exactly that expression.
    for path in (SIRI_VIEW, HUD_VIEW):
        view = path.read_text(encoding="utf-8")
        for direct in ("SiriPalette.blobs(", "SiriPalette.core("):
            if direct in view:
                failures.append(
                    f"{path.name} reads {direct}…) directly; the frame's colours come from "
                    "ReactorOrb.Palette so IDLE rests in the accent"
                )
        if "ReactorOrb.Palette" not in view:
            failures.append(f"{path.name} does not read ReactorOrb.Palette")
    return failures


def main() -> int:
    for path in (CONTRACT, WEB_REACTOR, REACTOR, SIRI_VIEW, HUD_VIEW, PALETTE):
        if not path.is_file():
            print(f"FAIL  {path} is missing", file=sys.stderr)
            return 1

    failures = (
        check_the_web_reads_the_contract()
        + check_the_geometry_is_an_instrument()
        + check_the_periods_are_tokens()
        + check_both_views_draw_the_same_object()
        + check_the_state_machines_are_one()
        + check_the_phone_reads_the_contract()
        + check_rest_is_the_accents_on_both_surfaces()
    )
    for failure in failures:
        print(f"FAIL  {failure}", file=sys.stderr)
    if failures:
        print(f"\n{len(failures)} failure(s)", file=sys.stderr)
        return 1
    print(
        f"reactor: {len(KEYS)} geometry constants agree with the contract on the web, "
        "8 periods are tokens, both Android views draw one renderer, 5 states are one table, "
        "rest is the accent's on both surfaces"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
