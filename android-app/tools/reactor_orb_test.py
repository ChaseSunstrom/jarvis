#!/usr/bin/env python3
"""Executable spec for the arc reactor — the one object, on all three surfaces.

The report this file exists for: *"the notification thing (with the orb) is
different than the actual orb that spawns with the wake word, why is this?"* and
then *"can you make the orb on the web view match the siri one but more arc
reactor looking? (same with the main app view on android)"*.

There were two orbs. The floating overlay window drew three coloured blobs
drifting inside a glowing ball; the app's own screens drew rings, ticks and a
radar sweep around a flat disc; and the web console drew a third thing that was
neither. Same state machine, three looks — and nothing anywhere said they were
supposed to agree, so they did not.

They are one object now, drawn by one implementation per platform:

  * `ReactorOrb.kt` — the renderer. Both Android views own an instance.
  * `Orb.svelte` — the same reactor as a fragment shader, because a browser can
    do a real sphere normal for free and a Canvas cannot.

Two implementations is one more than nobody wants, and it is the floor: Skia and
GLSL are different machines. So what is pinned here is the *contract* between
them — the geometry, the palette, the rates and the draw order — because that is
exactly what drifts when two files have to be retuned in step by hand.

Also pinned: the geometry BUDGET. Every radius is a multiple of the ball's, and
the outermost of them times the largest scale the view can ask for has to stay
inside the view. That is not a nicety. A View's canvas is clipped to its bounds
by its parent, so the moment the outermost primitive exceeds them the orb
acquires a hard rectangular edge — which is the "there's still a box around the
orb" report, and it has now been caused twice by moving one of these numbers
without moving the one that budgets against it.

Run:  python3 android-app/tools/reactor_orb_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
REPO = ANDROID.parent

REACTOR = ANDROID / "app/src/main/kotlin/ai/jarvis/app/ui/ReactorOrb.kt"
SIRI_VIEW = ANDROID / "app/src/main/kotlin/ai/jarvis/app/ui/SiriOrbView.kt"
HUD_VIEW = ANDROID / "app/src/main/kotlin/ai/jarvis/app/ui/JarvisOrbView.kt"
PALETTE = ANDROID / "app/src/main/kotlin/ai/jarvis/app/ui/SiriPalette.kt"
WEB_ORB = REPO / "jarvis-web/src/lib/components/Orb.svelte"


# =========================================================================
# The renderer's own numbers, mirrored
# =========================================================================

#: Every geometric constant the two implementations must agree on, as a
#: multiple of the ball's radius. Kotlin's name -> GLSL's name.
GEOMETRY = {
    "BLOB_FRACTION": "BLOB_FRACTION",
    "ORBIT_FRACTION": "ORBIT_FRACTION",
    "CORE_FRACTION": "CORE_FRACTION",
    "CORE_LEVEL_GAIN": "CORE_LEVEL_GAIN",
    "SPOKE_INNER": "SPOKE_INNER",
    "SPOKE_OUTER": "SPOKE_OUTER",
    "SPOKE_GAP_DEG": "SPOKE_GAP_DEG",
    "SPOKE_SPIN_RATIO": "SPOKE_SPIN_RATIO",
    "INNER_RIM_FACTOR": "INNER_RIM_FACTOR",
    "TURBULENCE_FACTOR": "TURBULENCE_FACTOR",
    "MID_DASH_FACTOR": "MID_DASH_FACTOR",
    "FINE_DASH_FACTOR": "FINE_DASH_FACTOR",
    "GAUGE_FACTOR": "GAUGE_FACTOR",
    "SWEEP_INNER_FACTOR": "SWEEP_INNER_FACTOR",
    "OUTER_FACTOR": "OUTER_FACTOR",
    "MINOR_TICK": "MINOR_TICK",
    "MAJOR_TICK": "MAJOR_TICK",
    "HALO_FRACTION": "HALO_FRACTION",
    "SPOKE_COUNT": "SPOKE_COUNT",
}

#: What the numbers have to BE, so a coordinated edit to both files still has to
#: come past this file. Two mirrors that agree with each other and with nothing
#: else is a tautology.
EXPECTED = {
    "BLOB_FRACTION": 0.80,
    "ORBIT_FRACTION": 0.30,
    "CORE_FRACTION": 0.30,
    "CORE_LEVEL_GAIN": 0.10,
    "SPOKE_INNER": 0.42,
    "SPOKE_OUTER": 0.92,
    "SPOKE_COUNT": 10,
    "SPOKE_GAP_DEG": 9.0,
    "SPOKE_SPIN_RATIO": 0.35,
    "INNER_RIM_FACTOR": 1.05,
    "TURBULENCE_FACTOR": 1.14,
    "MID_DASH_FACTOR": 1.22,
    "FINE_DASH_FACTOR": 1.36,
    "GAUGE_FACTOR": 1.50,
    "SWEEP_INNER_FACTOR": 1.10,
    "OUTER_FACTOR": 1.70,
    "MINOR_TICK": 0.08,
    "MAJOR_TICK": 0.15,
    "HALO_FRACTION": 1.30,
}


def kotlin_consts(src: str) -> dict[str, float]:
    """`const val NAME = 1.5f` -> {NAME: 1.5}. Numeric literals only."""
    out: dict[str, float] = {}
    for name, raw in re.findall(
        r"const val ([A-Z][A-Z0-9_]*)\s*(?::\s*\w+\s*)?=\s*(-?[0-9_]+\.?[0-9]*)[fFlL]?\s*$",
        src,
        re.M,
    ):
        text = raw.replace("_", "")
        out[name] = float(text) if "." in text else int(text)
    return out


def glsl_consts(src: str) -> dict[str, float]:
    """`const float NAME = 1.5;` -> {NAME: 1.5}."""
    return {
        name: float(raw)
        for name, raw in re.findall(
            r"const (?:float|int) ([A-Z][A-Z0-9_]*)\s*=\s*(-?[0-9]+\.?[0-9]*)\s*;", src
        )
    }


def check_the_two_renderers_agree() -> list[str]:
    failures = []
    kotlin = kotlin_consts(REACTOR.read_text(encoding="utf-8"))
    glsl = glsl_consts(WEB_ORB.read_text(encoding="utf-8"))

    for kname, gname in GEOMETRY.items():
        want = EXPECTED[kname]
        if kname not in kotlin:
            failures.append(f"ReactorOrb has no {kname}")
        elif abs(kotlin[kname] - want) > 1e-6:
            failures.append(f"ReactorOrb.{kname} is {kotlin[kname]}, this spec says {want}")
        if gname not in glsl:
            failures.append(f"the web shader has no {gname}")
        elif abs(glsl[gname] - want) > 1e-6:
            failures.append(f"the web shader's {gname} is {glsl[gname]}, this spec says {want}")
    return failures


def check_the_geometry_budget_holds() -> list[str]:
    """Nothing may be drawn outside what the caller sized the ball against.

    This is the "box around the orb" invariant, stated as arithmetic. The ball
    is sized `half_the_view / (OUTER_FACTOR * maxScale)`, so every primitive has
    to fit inside OUTER_FACTOR — and the two that most easily do not are the
    gauge's major ticks, which stick out past the ring they mark, and the coil
    annulus, which has no business leaving the ball.
    """
    failures = []
    outer = EXPECTED["OUTER_FACTOR"]

    reach = {
        "the inner rim": EXPECTED["INNER_RIM_FACTOR"],
        "the turbulence band": EXPECTED["TURBULENCE_FACTOR"] * 1.02,  # its own wobble
        "the mid dashes": EXPECTED["MID_DASH_FACTOR"],
        "the fine dashes": EXPECTED["FINE_DASH_FACTOR"],
        "the gauge's minor ticks": EXPECTED["GAUGE_FACTOR"] + EXPECTED["MINOR_TICK"] / 2,
        "the gauge's major ticks": EXPECTED["GAUGE_FACTOR"] + EXPECTED["MAJOR_TICK"] / 2,
    }
    for what, at in reach.items():
        if at > outer:
            failures.append(
                f"{what} reaches {at:.4f} x the ball, past OUTER_FACTOR ({outer}). "
                "The caller budgets the view against OUTER_FACTOR, so this is drawn "
                "outside the bounds and the parent's clip turns it into a box."
            )

    # The coils live INSIDE the ball, and the ball's own edge is where the glass
    # is: a coil poking through it reads as a printing error rather than depth.
    if EXPECTED["SPOKE_OUTER"] >= 1.0:
        failures.append("the coil annulus reaches the ball's own edge or past it")
    if EXPECTED["SPOKE_INNER"] >= EXPECTED["SPOKE_OUTER"]:
        failures.append("the coil annulus is inside out")
    # ...and they must not be swallowed by the core, which would leave a ring of
    # coils visible only at rest and gone the moment anybody spoke.
    core_max = EXPECTED["CORE_FRACTION"] + EXPECTED["CORE_LEVEL_GAIN"]
    if core_max >= EXPECTED["SPOKE_INNER"]:
        failures.append(
            f"at full microphone level the core reaches {core_max:.3f} x the ball and "
            f"swallows the coils, which start at {EXPECTED['SPOKE_INNER']}"
        )

    # The radar sweep needs an annulus to live in.
    if EXPECTED["SWEEP_INNER_FACTOR"] >= EXPECTED["GAUGE_FACTOR"]:
        failures.append("the radar sweep's annulus has no width")
    # The rings have to stay in order, or the boot sequence's outward reveal
    # plays inward.
    order = [
        ("INNER_RIM_FACTOR", EXPECTED["INNER_RIM_FACTOR"]),
        ("MID_DASH_FACTOR", EXPECTED["MID_DASH_FACTOR"]),
        ("FINE_DASH_FACTOR", EXPECTED["FINE_DASH_FACTOR"]),
        ("GAUGE_FACTOR", EXPECTED["GAUGE_FACTOR"]),
        ("OUTER_FACTOR", EXPECTED["OUTER_FACTOR"]),
    ]
    for (an, av), (bn, bv) in zip(order, order[1:]):
        if av >= bv:
            failures.append(f"{an} ({av}) is not inside {bn} ({bv}); the rings are out of order")

    # The saved layer must not clip the blobs, which are the furthest thing
    # inside it: a centre at ORBIT_FRACTION plus a radius of BLOB_FRACTION.
    src = REACTOR.read_text(encoding="utf-8")
    if "const val LAYER_PAD = ORBIT_FRACTION + BLOB_FRACTION" not in src:
        failures.append(
            "LAYER_PAD is no longer derived from the blob geometry, so a retuned "
            "blob can be clipped square by the layer it is drawn into"
        )
    return failures


def check_the_web_ball_fits_its_viewport() -> list[str]:
    """The shader has no parent to clip it, but it does have a viewport.

    `uv` is normalised so the shorter half-dimension is 1.0, so BALL times
    OUTER_FACTOR times the largest scale main() can ask for has to stay under it
    — otherwise the boundary ring runs off the left and right edges and the
    reactor reads as two arcs.
    """
    src = WEB_ORB.read_text(encoding="utf-8")
    glsl = glsl_consts(src)
    ball = glsl.get("BALL")
    if ball is None:
        return ["the web shader has no BALL constant"]

    breath = re.search(r"breath = 1\.0 \+ ([0-9.]+) \* sin\(uBreath\)", src)
    swell = re.search(r"R = BALL \* breath \* \(1\.0 \+ ([0-9.]+) \* lvl\)", src)
    if not breath or not swell:
        return ["cannot find the web shader's scale terms; the budget cannot be checked"]
    max_scale = (1 + float(breath.group(1))) * (1 + float(swell.group(1)))
    reach = ball * EXPECTED["OUTER_FACTOR"] * max_scale
    if reach > 1.0:
        return [
            f"the web orb's outer ring reaches {reach:.4f} of the half-viewport at full "
            f"breath and volume (BALL={ball}, scale={max_scale:.4f}). Over 1.0 it is "
            "clipped and the ring becomes two arcs."
        ]
    return []


# =========================================================================
# The palette is one table
# =========================================================================


def parse_kotlin_palette(fn: str) -> dict[str, list[str]]:
    text = PALETTE.read_text(encoding="utf-8")
    body = re.search(rf"fun {fn}\(tone: Tone\).*?\n    \}}", text, re.S)
    if not body:
        return {}
    return {
        tone: [h.lower() for h in re.findall(r"0x[0-9A-Fa-f]{2}([0-9A-Fa-f]{6})", arm)]
        for tone, arm in re.findall(r"Tone\.(\w+)\s*->\s*(.+)", body.group(0))
    }


def check_the_web_shader_wears_the_same_colours() -> list[str]:
    """One palette, two languages.

    The shader cannot read `SiriPalette`, and a browser and a phone showing
    different colours for the same state is the most visible possible drift — so
    the shader carries the hexes in a comment beside each `vec3`, and this
    checks the comment against the Kotlin AND the `vec3` against the comment.
    Checking only the comment would let a typo in the floats through; checking
    only the floats would mean re-deriving them here and calling it agreement.
    """
    failures = []
    blobs = parse_kotlin_palette("blobs")
    cores = parse_kotlin_palette("core")
    src = WEB_ORB.read_text(encoding="utf-8")

    # The web console has four states; ERROR is the phone's alone (the console
    # says so in words, in a banner, which a colour cannot).
    for tone, prefix in (
        ("IDLE", "i"),
        ("LISTENING", "l"),
        ("THINKING", "t"),
        ("SPEAKING", "s"),
    ):
        want = blobs.get(tone, []) + cores.get(tone, [])
        if len(want) != 4:
            failures.append(f"SiriPalette has no complete entry for {tone}")
            continue
        comment = re.search(
            rf"//\s*{tone.lower()}\s+#([0-9A-Fa-f]{{6}})\s+#([0-9A-Fa-f]{{6}})\s+"
            rf"#([0-9A-Fa-f]{{6}})\s*/\s*#([0-9A-Fa-f]{{6}})",
            src,
        )
        if not comment:
            failures.append(
                f"the web shader does not name {tone}'s four hexes, so nothing can "
                "check its floats against the phone's palette"
            )
            continue
        got = [g.lower() for g in comment.groups()]
        if got != want:
            failures.append(
                f"{tone}: the web shader says {got} and SiriPalette says {want}"
            )
            continue
        # And the floats have to be those hexes, to within a byte.
        for i, name in enumerate([f"{prefix}0", f"{prefix}1", f"{prefix}2", f"{prefix}c"]):
            vec = re.search(
                rf"vec3 {name} = vec3\(([0-9.]+), ([0-9.]+), ([0-9.]+)\);", src
            )
            if not vec:
                failures.append(f"the web shader has no {name}")
                continue
            hexed = want[i]
            for ch in range(3):
                want_f = int(hexed[ch * 2 : ch * 2 + 2], 16) / 255.0
                got_f = float(vec.group(ch + 1))
                if abs(got_f - want_f) > 1.0 / 255.0:
                    failures.append(
                        f"{name} channel {ch} is {got_f}, but #{hexed} says "
                        f"{want_f:.3f} — the browser would show a different colour "
                        "from the phone for the same state"
                    )
    return failures


def check_the_rates_are_one_table() -> list[str]:
    """A shared object that moves at two speeds is two objects again."""
    failures = []
    palette = PALETTE.read_text(encoding="utf-8")
    hz = {
        tone: 1.0 / float(period)
        for tone, period in re.findall(r"Tone\.(\w+)\s*->\s*1f\s*/\s*([0-9.]+)f", palette)
    }
    web = WEB_ORB.read_text(encoding="utf-8")

    orbit = re.search(r"const ORBIT_HZ = \[([^\]]+)\]", web)
    breath = re.search(r"const BREATH_S = \[([^\]]+)\]", web)
    if not orbit or not breath:
        return ["the web orb no longer states its drift and breathing rates as tables"]

    def numbers(expr: str) -> list[float]:
        return [eval(term.strip(), {"__builtins__": {}}) for term in expr.split(",")]

    got_hz = numbers(orbit.group(1))
    got_breath = numbers(breath.group(1))
    for i, tone in enumerate(("IDLE", "LISTENING", "THINKING", "SPEAKING")):
        if tone not in hz:
            failures.append(f"SiriPalette has no orbit rate for {tone}")
            continue
        if abs(got_hz[i] - hz[tone]) > 1e-6:
            failures.append(
                f"{tone}: the web orb drifts at {got_hz[i]:.4f} Hz and the phone at "
                f"{hz[tone]:.4f} Hz"
            )
        if abs(1.0 / got_breath[i] - hz[tone]) > 1e-6:
            failures.append(
                f"{tone}: the web orb breathes every {got_breath[i]}s and the phone "
                f"drifts every {1 / hz[tone]:.3f}s; they are the same table"
            )

    # Chrome rotation: 20 deg/s at rest, 40 while a turn is live, stated on both
    # Android views and in the shader in radians.
    for path in (SIRI_VIEW, HUD_VIEW):
        src = path.read_text(encoding="utf-8")
        if "if (tone == SiriPalette.Tone.IDLE) 20f else 40f" not in src and (
            "if (mode == Mode.IDLE) 20f else 40f" not in src
        ):
            failures.append(
                f"{path.name} no longer turns its chrome at 20/40 degrees a second"
            )
    if "spin + dt * (0.35 + (smoothState > 0.5 ? 0.35 : 0.0))" not in web:
        failures.append(
            "the web orb's chrome no longer turns at 0.35/0.70 rad/s, which is the "
            "20/40 degrees a second the phone turns its own at"
        )
    return failures


# =========================================================================
# Draw order — the part that is invisible in a diff
# =========================================================================


def check_the_draw_order() -> list[str]:
    """Which side of the additive layer each element falls on.

    Every one of these is a real bug if it moves, and none of them is visible by
    reading the line on its own:

      * the **substrate** before the blobs, or additive blending has nothing to
        add to and the orb is whatever is behind it, tinted;
      * the **coils inside** the layer, so the drifting blob colours light them.
        Outside, the same shape is a flat overprint that reads as a decal stuck
        on the front of a ball;
      * the **chrome outside** it. Screen-blended against the blob field, a
        gauge tick washes to white wherever a blob passes under it — and the
        blobs are moving, so the chrome would flicker;
      * the **glass last**, because a cover under the thing it covers is not a
        cover.
    """
    src = REACTOR.read_text(encoding="utf-8")
    body = re.search(r"fun draw\(canvas: Canvas, f: Frame\) \{(.*?)\n    \}", src, re.S)
    if not body:
        return ["ReactorOrb has no draw()"]
    seq = body.group(1)

    def at(needle: str) -> int:
        return seq.find(needle)

    failures = []
    order = [
        ("drawHalo(", "the bloom"),
        ("canvas.saveLayer(", "the additive layer opening"),
        ("drawSubstrate(", "the dark ground"),
        ("drawBlob(", "the colour field"),
        ("drawSpokes(", "the coils"),
        ("drawCore(", "the hot centre"),
        ("drawGlass(", "the cover"),
        ("canvas.restoreToCount(layer)", "the additive layer closing"),
        ("drawChrome(", "the rings, ticks and sweep"),
    ]
    positions = []
    for needle, what in order:
        pos = at(needle)
        if pos < 0:
            failures.append(f"ReactorOrb.draw no longer draws {what} ({needle})")
        positions.append((pos, needle, what))
    if failures:
        return failures
    for (pa, na, wa), (pb, nb, wb) in zip(positions, positions[1:]):
        if pa > pb:
            failures.append(f"{wa} is drawn after {wb}; see this function's docstring")

    # The rim is the ball's own edge and belongs outside the layer with the
    # chrome — screen-blended it would vanish wherever a bright blob sits under
    # it, so the outline would come and go as the field drifts.
    if at("drawRim(") < at("canvas.restoreToCount(layer)"):
        failures.append("the ball's rim is drawn inside the additive layer")

    # The layer must be bounded to the ball, not to the canvas. A full-view
    # offscreen, sixty times a second, for a ball that occupies a fifth of it.
    if "canvas.saveLayer(null, null)" in seq:
        failures.append(
            "the additive layer is the size of the whole canvas. On the home screen "
            "that view is full-screen, so this allocates a screen-sized offscreen "
            "every frame."
        )
    return failures


def check_the_blend_is_confined() -> list[str]:
    failures = []
    src = REACTOR.read_text(encoding="utf-8")
    if "PorterDuff.Mode.SCREEN" not in src:
        failures.append("the blobs no longer blend additively, so they read as flat discs")
    if "saveLayer" not in src:
        failures.append(
            "the additive blend is not confined to a layer; over the overlay window it "
            "would brighten the app behind instead of the orb"
        )
    # The substrate and the glass must NOT be additive: screening a dark colour
    # onto anything is very nearly a no-op, so an additive substrate is no
    # substrate and an additive shadow is no shadow.
    for fn in ("drawSubstrate", "drawGlass"):
        body = src.split(f"private fun {fn}(", 1)[1][:1400]
        if "plain.shader" not in body:
            failures.append(
                f"{fn} no longer uses the non-additive paint. Screening a dark colour "
                "onto anything is a no-op, so it would draw nothing at all."
            )
    return failures


# =========================================================================
# Both Android surfaces really do use it
# =========================================================================


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
        # Both must size the ball against the renderer's own outermost radius
        # rather than a number that happens to have been right once.
        if "ReactorOrb.OUTER_FACTOR" not in src:
            failures.append(
                f"{path.name} sizes its orb against something other than "
                "ReactorOrb.OUTER_FACTOR, so a retuned ring can be clipped into a box"
            )
        # The halo is the only thing that can exceed the view; it must be told
        # where the edge is.
        if "f.maxRadius" not in src:
            failures.append(f"{path.name} does not clamp the halo to its own bounds")
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


def check_the_reactor_is_recognisable() -> list[str]:
    """The coils are the point.

    "Make it look more like an arc reactor" is one element: a ring of coils
    between the core and the rim. Rings and ticks make an instrument; the coils
    are what make it *that* instrument. If they are gone, the ask is not done —
    on either surface.
    """
    failures = []
    if "private fun drawSpokes" not in REACTOR.read_text(encoding="utf-8"):
        failures.append("the reactor has no coils; it is an orb with rings around it again")
    web = WEB_ORB.read_text(encoding="utf-8")
    if "coilBand" not in web:
        failures.append("the web orb has no coils")
    # And the browser's version has to be genuinely three-dimensional, which is
    # the whole reason it is a shader and not a canvas: a sphere normal, a
    # specular off the cover, and a fresnel limb.
    for needle, what in (
        ("sqrt(max(1.0 - q * q, 0.0))", "the sphere normal"),
        ("pow(max(dot(n, H), 0.0)", "the specular highlight"),
        ("pow(1.0 - z,", "the fresnel limb"),
    ):
        if needle not in web:
            failures.append(f"the web orb has lost {what} and is a flat disc again")
    return failures


def check_the_phases_are_integrated() -> list[str]:
    """`phase = t * rate` jumps by `t * delta` when the rate changes.

    Every rate here is per-state — the drift, the spin, the breathing — so all
    three have to be integrated against the clock rather than derived from it.
    On the phone that is `phase + dt * hz`; in the browser it cannot be done in
    a fragment shader at all, so the phases arrive as uniforms.
    """
    failures = []
    web = WEB_ORB.read_text(encoding="utf-8")
    for uniform in ("uPhase", "uSpin", "uBreath"):
        if f"uniform float {uniform};" not in web:
            failures.append(
                f"the web shader derives {uniform} from uTime again; a state change "
                "would jump the animation by several turns"
            )
    if "Math.min((nowMs - last) / 1000, 0.1)" not in web:
        failures.append(
            "the web orb's frame delta is unclamped, so a tab returning from the "
            "background advances every phase by however long it was away"
        )
    for path in (SIRI_VIEW, HUD_VIEW):
        src = path.read_text(encoding="utf-8")
        if "ReactorOrb.TWO_PI" not in src:
            failures.append(f"{path.name} no longer integrates a phase against the clock")
    return failures


def main() -> int:
    for path in (REACTOR, SIRI_VIEW, HUD_VIEW, PALETTE, WEB_ORB):
        if not path.is_file():
            print(f"FAIL  {path} is missing", file=sys.stderr)
            return 1

    failures = (
        check_the_two_renderers_agree()
        + check_the_geometry_budget_holds()
        + check_the_web_ball_fits_its_viewport()
        + check_the_web_shader_wears_the_same_colours()
        + check_the_rates_are_one_table()
        + check_the_draw_order()
        + check_the_blend_is_confined()
        + check_both_views_draw_the_same_object()
        + check_the_state_machines_are_one()
        + check_the_reactor_is_recognisable()
        + check_the_phases_are_integrated()
    )
    for failure in failures:
        print(f"FAIL  {failure}", file=sys.stderr)
    if failures:
        print(f"\n{len(failures)} failure(s)", file=sys.stderr)
        return 1
    print(
        f"reactor orb: {len(GEOMETRY)} proportions, 4 palettes, 3 rate tables, the "
        "draw order and the geometry budget agree across Skia and GLSL"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
