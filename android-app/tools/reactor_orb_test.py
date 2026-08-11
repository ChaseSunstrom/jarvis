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

import math
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
    "HOUSING_INNER": "HOUSING_INNER",
    "HOUSING_OUTER": "HOUSING_OUTER",
    "HUB_FACTOR": "HUB_FACTOR",
    "SEAT_SHADOW_SPAN": "SEAT_SHADOW_SPAN",
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

#: The light, and everything hung off it.
#:
#: The second half of the same report: *"I dont want just an orb clock, I want it
#: to look 3d and actually nice, similar to the rest of the AIs"*. Structure was
#: not the problem by then — there was a recess, there were plates. DEPTH was:
#: every shape was a gradient about the same centre, so brightness fell off with
#: radius alone, which is a flat target lit head-on.
#:
#: Both implementations light the object now, and they are lighting it from very
#: different machines: the shader has a real sphere normal and takes `dot(n, L)`
#: per pixel, while a Canvas has no per-pixel shader at all and has to fake that
#: cosine with gradients struck about a point offset toward the light. Faking it
#: from a DIFFERENT direction is the loudest drift available — the highlight is
#: the first thing anybody looks at, and up-left on the phone against up-right in
#: the browser needs no measuring to see. So the direction is one number here,
#: and the two things derived from it are derived rather than eyeballed.
LIGHT = (-0.46, 0.54, 0.70)

#: The fill, from the opposite corner. One light in a black room gives a
#: crescent moon; this is what leaves the far side readable, and its own small
#: catchlight is the second one — a glass ball on a desk has two highlights, a
#: drawing of a glass ball has one.
FILL = (0.60, -0.46, 0.64)

#: The Blinn-Phong exponents the shader's lobes use, and the Kotlin constant
#: each one sizes. A Canvas cannot raise anything to a power, so the phone's
#: highlights are gradients sized off these lobes — see
#: `check_the_two_renderers_are_lit_the_same`, which does that arithmetic rather
#: than trusting either side's number.
LOBES = {
    "SPECULAR_POWER": ("SPECULAR_R", 96.0, "the key's tight lobe"),
    "SPECULAR_WIDE_POWER": ("SPECULAR_WIDE_R", 16.0, "the sheen around it"),
    "FILL_SPECULAR_POWER": ("FILL_SPECULAR_R", 46.0, "the fill's catchlight"),
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
    "HOUSING_INNER": 0.34,
    "HOUSING_OUTER": 0.965,
    "HUB_FACTOR": 0.385,
    "SEAT_SHADOW_SPAN": 0.16,
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


def kotlin_body(src: str, fn: str) -> str:
    """The text of one `private fun`, up to the next member.

    Stops at the following KDoc as well as at the following `fun`, because the
    next function's doc comment is prose about a DIFFERENT function — and these
    docs name the paints, so swallowing one makes "does this body touch an
    additive paint?" answer yes for every function in the file.
    """
    head = f"private fun {fn}("
    if head not in src:
        return ""
    rest = src.split(head, 1)[1]
    ends = [rest.find(m) for m in ("\n    /**", "\n    private fun ", "\n    // ---")]
    ends = [e for e in ends if e >= 0]
    return rest[: min(ends)] if ends else rest


def check_the_coils_are_a_layered_assembly() -> list[str]:
    """The coils have to be an assembly with parts at different depths.

    The report: *"the arc reactor isnt layerd and doesnt really look like the
    arc reactor, once again, it looks weird"*. They were ten STROKED ARCS —
    each one a uniform band at a single radius, all of them additive, with
    nothing drawn behind them. That has no thickness to shade across and no
    housing to sit in, so the plates dissolved into the drifting colour field
    and the whole reactor read as one flat glowing washer.

    Four things fix it, and every one of them is invisible in a diff:

      * the plates are FILLED wedges between the two seat radii, not strokes,
        so they have a thickness at all;
      * a gradient ACROSS that thickness, bright at the inner edge, because
        that is the face the core lights;
      * a HOUSING drawn first and NOT additively. Screening a dark colour onto
        anything is very nearly a no-op, so an additive recess is no recess —
        this is the largest single part of "layered" and the easiest to undo by
        moving one paint;
      * a taper. The gap's arc LENGTH is held fixed rather than its angle, so it
        opens toward the middle and each plate is a keystone. Hold the angle
        instead and you get ten identical sectors, which is a pie chart.
    """
    failures = []
    kot = REACTOR.read_text(encoding="utf-8")
    web = WEB_ORB.read_text(encoding="utf-8")

    # --- the radial order, centre out: core, dark gap, hub, plates, lip ---
    order = [
        ("CORE_FRACTION", EXPECTED["CORE_FRACTION"]),
        ("HOUSING_INNER", EXPECTED["HOUSING_INNER"]),
        ("HUB_FACTOR", EXPECTED["HUB_FACTOR"]),
        ("SPOKE_INNER", EXPECTED["SPOKE_INNER"]),
        ("SPOKE_OUTER", EXPECTED["SPOKE_OUTER"]),
        ("HOUSING_OUTER", EXPECTED["HOUSING_OUTER"]),
    ]
    for (an, av), (bn, bv) in zip(order, order[1:]):
        if av >= bv:
            failures.append(
                f"{an} ({av}) is not inside {bn} ({bv}); the reactor reads centre out as "
                "core, dark gap, hub ring, plates, outer lip and that order is the layering"
            )
    if EXPECTED["HOUSING_OUTER"] >= 1.0:
        failures.append("the housing reaches the ball's own edge, where the glass is")

    # The dark gap has to be a gap you can see. Under about 3% of the ball it is
    # an antialiasing artefact and the core runs straight into the hub.
    gap = EXPECTED["HUB_FACTOR"] - EXPECTED["CORE_FRACTION"]
    if gap < 0.03:
        failures.append(
            f"the gap between the core ({EXPECTED['CORE_FRACTION']}) and the hub ring "
            f"({EXPECTED['HUB_FACTOR']}) is {gap:.3f} x the ball, too small to read as one"
        )

    # --- the taper -------------------------------------------------------
    span = 360.0 / EXPECTED["SPOKE_COUNT"]
    centre = (EXPECTED["SPOKE_INNER"] + EXPECTED["SPOKE_OUTER"]) / 2
    gap_arc = EXPECTED["SPOKE_GAP_DEG"] * centre
    plate_in = span - gap_arc / EXPECTED["SPOKE_INNER"]
    plate_out = span - gap_arc / EXPECTED["SPOKE_OUTER"]
    if plate_in <= 0:
        failures.append(
            f"the gap opens to {gap_arc / EXPECTED['SPOKE_INNER']:.1f} deg at the inner "
            f"seat, which is the whole {span:.1f} deg segment: the plates close up"
        )
    elif plate_out / plate_in < 1.15:
        failures.append(
            f"a plate spans {plate_in:.1f} deg at the inner seat and {plate_out:.1f} deg "
            "at the outer; under 1.15x that is not a visible taper and the plates read "
            "as ten identical sectors"
        )

    # --- Skia: filled wedges in a non-additive recess ---------------------
    spokes = kotlin_body(kot, "drawSpokes")
    if not spokes:
        failures.append("ReactorOrb has no drawSpokes")
        return failures
    if "canvas.drawArc(" in spokes:
        failures.append(
            "the plates are stroked arcs again. A stroked arc is a uniform band at one "
            "radius: no thickness to shade across, no taper, and nothing to recess."
        )
    for needle, what in (
        ("plate.arcTo(seatInRect", "the plate's inner edge"),
        ("plate.arcTo(seatOutRect", "the plate's outer edge"),
        ("canvas.drawPath(plate, additive)", "the filled plate"),
    ):
        if needle not in spokes:
            failures.append(f"drawSpokes no longer draws {what} ({needle})")
    if "gapArc / SPOKE_INNER" not in spokes or "gapArc / SPOKE_OUTER" not in spokes:
        failures.append(
            "drawSpokes no longer derives the gap per seat radius, so the plates have "
            "lost their keystone taper"
        )
    at_housing = spokes.find("drawHousing(")
    at_plates = spokes.find("canvas.drawPath(plate")
    if at_housing < 0:
        failures.append("drawSpokes no longer draws the housing; the plates sit in nothing")
    elif 0 <= at_plates < at_housing:
        failures.append(
            "the housing is drawn after the plates, so the recess is painted over the "
            "things meant to be sitting in it"
        )
    housing = kotlin_body(kot, "drawHousing")
    if "SEAT_SHADOW_ALPHA" not in spokes:
        failures.append(
            "the outer seat casts no shadow down the plates; without it the plates are "
            "level with the ring rather than under it"
        )
    # Nothing in the recess may use a screening paint. `plain.shader = null` on
    # its way out is enough to satisfy a check that only looks for the name, so
    # this looks at what the housing is actually PAINTED with.
    if "canvas.drawPath(annulus, plain)" not in housing:
        failures.append(
            "the recess is no longer filled with the non-additive paint. Screening a "
            "dark colour onto anything is very nearly a no-op, so an additive housing "
            "is no housing and the plates float in the blob field again."
        )
    if "additive" in housing:
        failures.append(
            "drawHousing touches an additive paint. The recess and the hub are the two "
            "things in the layer that take light away rather than adding it."
        )
    if "canvas.drawCircle(f.cx, f.cy, hubR, metal)" not in housing or "HUB_COLOR" not in housing:
        failures.append(
            "the housing has no metal hub ring struck between the core and the coils"
        )

    # --- GLSL: the same assembly, with the sphere normal doing the depth ---
    #
    # Matched as patterns rather than as literal lines: the shader is written by
    # hand against a different machine, and it renames its own locals. What has
    # to survive is the ARITHMETIC — a housing that subtracts, a hub struck by
    # the light, a gap divided by the radius so it widens inward, a ramp across
    # the plate's thickness and the seat's shadow at the end of it.
    for pattern, what in (
        (r"acc = mix\(acc, HOUSING,", "a housing that takes light away rather than adding it"),
        (r"ring\(q, HUB_FACTOR,", "the metal hub ring"),
        (r"gapArc / max\(\w+,", "the gap that widens inward, which is the plates' taper"),
        (
            r"across = clamp\(\(q\w* - SPOKE_INNER\)",
            "the gradient across the plate's thickness",
        ),
        (r"SEAT_SHADOW_SPAN, 1\.0, across", "the outer seat's shadow on the plates"),
    ):
        if not re.search(pattern, web):
            failures.append(f"the web orb has lost {what} ({pattern})")
    # The browser has a real normal; the recess and the machined ring are where
    # it is worth spending, because the phone can only fake that depth with a
    # flat gradient. The INVERTED term is the recess's own: it deepens where the
    # sphere turns away from the light, which is what occlusion does.
    diffuse = re.search(r"(\w+)\s*=\s*clamp\(\s*(?:dot\(n, L\)|ndl)\s*,\s*0\.0,\s*1\.0\)", web)
    if not diffuse:
        failures.append("the web orb's machined parts are no longer lit off the normal")
    elif not re.search(rf"1\.0 - (?:{diffuse.group(1)}|clamp\(dot\(n, L\), 0\.0, 1\.0\))", web):
        failures.append(
            "the web orb's recess no longer deepens away from the light, so it is as "
            "flat as the one the Canvas has to fake and the shader is spending a real "
            "sphere normal on nothing"
        )
    return failures


def glsl_light(src: str, name: str) -> tuple[float, float, float] | None:
    """One of the shader's light directions, however it happens to spell it.

    Reads the leading literal of each component, so a light that WANDERS —
    `normalize(vec3(-0.46 + kx, 0.54 + ky, 0.70))` — still states the direction
    it wanders about. What is pinned is that direction, not the spelling: a
    check that only recognised one form would fail the day the other side tidied
    its own file, and a spec that fails for cosmetic reasons is a spec people
    start ignoring.
    """
    inline = re.search(
        rf"\b{name}\s*=\s*normalize\(\s*vec3\(\s*"
        r"(-?[0-9.]+)[^,]*,\s*(-?[0-9.]+)[^,]*,\s*(-?[0-9.]+)[^)]*\)",
        src,
    )
    if inline:
        return tuple(float(g) for g in inline.groups())  # type: ignore[return-value]
    return None


def unit(v: tuple[float, ...]) -> tuple[float, ...]:
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v) if n else v


def check_the_two_renderers_are_lit_the_same() -> list[str]:
    """One light, and the two things derived from it.

    This is the check that the "make it 3d" pass cannot quietly come apart. The
    phone fakes `dot(n, L)` with offset gradients and the browser computes it;
    what they must share is WHERE THE LIGHT IS, because everything either of
    them does about depth — the highlight, the terminator, the fresnel's bias,
    which coil plate is brightest, which way the assembly's shadow falls — is
    that one vector, and all of it points the wrong way together if it moves.

    Two of the numbers are derived here rather than compared, so neither file
    can hold a plausible-looking value that is simply wrong:

      * the specular sits at `length(normalize(L + view).xy)` — where the
        half-vector meets a unit sphere. The shader gets that for free from its
        normal; the Canvas has to be TOLD, and this is the telling;
      * the highlight's radius is where a lobe of the shader's own exponent has
        fallen to 2%, and its middle stop is that lobe's half-intensity point.
        A Canvas cannot raise anything to a power, but it can be handed a
        gradient shaped like the answer.
    """
    failures = []
    kot = REACTOR.read_text(encoding="utf-8")
    kotlin = kotlin_consts(kot)
    web = WEB_ORB.read_text(encoding="utf-8")

    for rig, spec, glsl_name in (
        ("key", LIGHT, "L"),
        ("fill", FILL, "Fl"),
    ):
        prefix = "LIGHT" if rig == "key" else "FILL"
        for axis, want in zip("XYZ", spec):
            name = f"{prefix}_{axis}"
            got = kotlin.get(name)
            if got is None:
                failures.append(
                    f"ReactorOrb has no {name}; the {rig} light is somewhere else again"
                )
            elif abs(got - want) > 1e-6:
                failures.append(f"ReactorOrb.{name} is {got}, this spec says {want}")

        there = glsl_light(web, glsl_name)
        if there is None:
            failures.append(
                f"the web shader states no {rig} light this file can find, so nothing "
                "checks that the two surfaces are lit from the same place"
            )
        else:
            want_v = unit(spec)
            got_v = unit(there)
            if max(abs(a - b) for a, b in zip(want_v, got_v)) > 0.01:
                failures.append(
                    f"the web shader's {rig} light is {tuple(round(c, 4) for c in got_v)} "
                    f"and this spec says {tuple(round(c, 4) for c in want_v)}. The "
                    "highlight is the first thing anybody looks at; two surfaces lit "
                    "from two directions is the most visible drift there is."
                )

        # The flattened direction the Canvas actually uses has to BE that light,
        # normalised. Nothing in ReactorOrb may touch a light except through the
        # two of these, which is why they are derived here rather than eyeballed.
        flat = unit((spec[0], spec[1]))
        for name, want in ((f"{prefix}_DIR_X", flat[0]), (f"{prefix}_DIR_Y", flat[1])):
            got = kotlin.get(name)
            if got is None:
                failures.append(f"ReactorOrb has no {name}")
            elif abs(got - want) > 1e-3:
                failures.append(
                    f"ReactorOrb.{name} is {got}, but normalising the {rig} light's x "
                    f"and y gives {want:.4f}. The screen-space direction has to be the "
                    "light itself."
                )

    # ...and Skia's y points DOWN while the light is stated y-UP, so the one
    # helper that converts has to subtract. Get this backwards and the phone's
    # highlight is below the middle while the browser's is above it.
    if "f.cy - LIGHT_DIR_Y" not in kot:
        failures.append(
            "ReactorOrb no longer flips the light's y for Skia's downward axis, so the "
            "phone is lit from below and the browser from above"
        )

    # Each highlight sits where its own half-vector meets the sphere. The shader
    # gets that for free from the normal; the Canvas has to be TOLD, and this is
    # the telling — "about a third of the way out" is a guess, and a guess is
    # what puts the phone's highlight somewhere the browser's is not.
    for spec, name in ((LIGHT, "SPECULAR_OFFSET"), (FILL, "FILL_SPECULAR_OFFSET")):
        u = unit(spec)
        half = unit((u[0], u[1], u[2] + 1.0))
        want = math.hypot(half[0], half[1])
        got = kotlin.get(name)
        if got is None:
            failures.append(f"ReactorOrb has no {name}")
        elif abs(got - want) > 0.01:
            failures.append(
                f"ReactorOrb.{name} is {got}, but the half-vector between that light and "
                f"the viewer meets a unit sphere at {want:.3f} of its radius. That is "
                "where a highlight goes, and where the shader puts its own."
            )

    # ...and each is sized off the exponent of the lobe it stands in for. A
    # Canvas cannot raise anything to a power; it can be handed a gradient the
    # shape of the answer.
    exponents = set()
    for m in re.finditer(r"\bpow\(", web):
        args = terms(call_args(web, m.end() - 1))
        try:
            exponents.add(float(args[-1]))
        except (IndexError, ValueError):
            continue
    for power_name, (radius_name, want_power, what) in LOBES.items():
        power = kotlin.get(power_name)
        if power is None:
            failures.append(f"ReactorOrb has no {power_name} to size {what} against")
            continue
        if abs(power - want_power) > 1e-6:
            failures.append(f"ReactorOrb.{power_name} is {power}, this spec says {want_power}")
        if not any(abs(e - want_power) < 1e-6 for e in exponents):
            failures.append(
                f"the web shader raises nothing to {want_power:g}, so {what} is a "
                f"different size there from the gradient ReactorOrb sizes off it"
            )
        want_r = math.sqrt(2.0 * math.log(50.0) / want_power)
        got_r = kotlin.get(radius_name)
        if got_r is None:
            failures.append(f"ReactorOrb has no {radius_name}")
        elif abs(got_r - want_r) > 0.01:
            failures.append(
                f"ReactorOrb.{radius_name} is {got_r}, but a lobe of exponent "
                f"{want_power:g} has fallen to 2% by {want_r:.3f} of the ball's radius"
            )

    # One shared middle stop: the fraction of any such radius at which any such
    # lobe is at half intensity. It is the same number for every exponent —
    # sqrt(ln 2 / ln 50) — which is why one constant does for all three.
    want_half = math.sqrt(math.log(2.0) / math.log(50.0))
    got_half = kotlin.get("SPECULAR_HALF")
    if got_half is None:
        failures.append("ReactorOrb has no SPECULAR_HALF")
    elif abs(got_half - want_half) > 0.02:
        failures.append(
            f"ReactorOrb.SPECULAR_HALF is {got_half}; a Blinn-Phong lobe is at half "
            f"intensity by {want_half:.3f} of the radius at which it dies. The middle "
            "stop is what makes the falloff steep, and a highlight that is not steep "
            "is a smudge."
        )

    # The plate rule. Both sides take a cosine against the light; the phone once
    # per plate, the browser once per pixel. Base plus gain is 1 so the brightest
    # plate is exactly as bright as every plate used to be — the ring gains its
    # depth by the others giving some up, not by the assembly getting hotter.
    base = kotlin.get("PLATE_LIGHT_BASE")
    gain = kotlin.get("PLATE_LIGHT_GAIN")
    if base is None or gain is None:
        failures.append(
            "ReactorOrb states no plate-lighting rule, so its ten plates are ten "
            "identical brightnesses again — which is a printed ring however well each "
            "one is shaded across its own thickness"
        )
    else:
        if abs(base + gain - 1.0) > 1e-6:
            failures.append(
                f"PLATE_LIGHT_BASE + PLATE_LIGHT_GAIN is {base + gain}, not 1: the plate "
                "facing the light is no longer exactly as bright as the plates were "
                "before they were lit, so the whole assembly changed brightness"
            )
        if gain < 0.15:
            failures.append(
                f"PLATE_LIGHT_GAIN is {gain}; under about 0.15 the lit plate and the "
                "unlit one are the same plate and the ring is flat again"
            )
        if base < 0.35:
            failures.append(
                f"PLATE_LIGHT_BASE is {base}; the plate facing away from the light still "
                "has the core in front of it, and one that dark reads as a missing coil"
            )

    # The ball's shading is struck between the middle and the true diffuse pole.
    pole = math.hypot(*unit(LIGHT)[:2])
    offset = kotlin.get("SPHERE_LIGHT_OFFSET")
    if offset is None:
        failures.append(
            "ReactorOrb has no SPHERE_LIGHT_OFFSET, so its gradients are struck about "
            "the middle again and the orb is a disc with rings on it"
        )
    elif not 0.15 <= offset <= pole + 1e-6:
        failures.append(
            f"SPHERE_LIGHT_OFFSET is {offset}; it has to be between 0.15 (below which "
            f"there is no ball) and the true diffuse pole at {pole:.3f} (past which the "
            "falloff is all crowded into the limb and reads as a crescent moon)"
        )

    # Everything that moves has to stay under the threshold at which the two
    # surfaces would visibly disagree — the shader holds its light still.
    for name, cap, what in (
        ("SPECULAR_DRIFT", 0.05, "the highlight's drift"),
        ("SPHERE_WANDER", 0.25, "the lit point's wander"),
        ("HALO_BREATH", 0.10, "the bloom's breathing"),
    ):
        got = kotlin.get(name)
        if got is None:
            failures.append(f"ReactorOrb has no {name}")
        elif got > cap:
            failures.append(
                f"{name} is {got}: {what} is meant to be under {cap}, felt and not seen. "
                "Past that the phone is visibly animating something the browser holds "
                "still, and the two orbs stop being one object."
            )
    return failures


def check_the_ball_is_a_sphere() -> list[str]:
    """Skia has no per-pixel shader, so the sphere is stacked gradients.

    That is a real technique, not a compromise, and it has parts. Each of these
    is one line to delete and none of them is visible in a diff:

      * a gradient struck OFF CENTRE, toward the light. This one alone is the
        difference between a disc and a ball, and it is what nothing in this
        renderer had: every shape was concentric, so brightness fell off with
        radius and the orb read as rings printed on a circle;
      * a terminator, dropping away from that same point;
      * limb darkening, and a fresnel arc opposite the light — an edge brighter
        than the middle is what says "surface curving out of view";
      * the assembly's shadow on the ball, and one cosine per coil plate;
      * a bloom wider and dimmer than the object, so the light looks like it is
        in the air rather than painted on.
    """
    failures = []
    src = REACTOR.read_text(encoding="utf-8")

    sphere = kotlin_body(src, "drawSphereShading")
    if not sphere:
        return [
            "ReactorOrb has no drawSphereShading. Without it every gradient in the "
            "layer is struck about the ball's centre, brightness falls off with radius "
            "alone, and the orb is a flat disc with rings on it."
        ]
    if "litX(f, SPHERE_LIGHT_OFFSET" not in sphere:
        failures.append(
            "the ball's shading is no longer struck about a point offset toward the "
            "light; a radial gradient on the centre is a disc"
        )
    if "additive.shader = RadialGradient" not in sphere:
        failures.append("the ball has no lit near side")
    if "plain.shader = RadialGradient" not in sphere:
        failures.append(
            "the ball has no terminator. It has to be the non-additive paint: screening "
            "a dark colour onto anything is very nearly a no-op."
        )

    glass = kotlin_body(src, "drawGlass")
    if "FRESNEL_ALPHA" not in glass or "litX(f, FRESNEL_OFFSET" not in glass:
        failures.append(
            "the glass has no fresnel arc struck about the lit point, so the limb is "
            "darker than the middle everywhere and the ball has no surface"
        )
    if "litX(f, SPECULAR_OFFSET" not in glass:
        failures.append("the highlight is no longer offset toward the light")
    if "SPECULAR_DRIFT" not in glass:
        failures.append(
            "the highlight is nailed to one pixel again, which reads as a sticker on "
            "the glass rather than as glass"
        )
    if "SPECULAR_X" in src or "SPECULAR_Y" in src:
        failures.append(
            "the highlight's position is hard-coded again instead of being derived from "
            "the light, so it can disagree with everything else lit by it"
        )

    spokes = kotlin_body(src, "drawSpokes")
    if "PLATE_LIGHT_BASE + PLATE_LIGHT_GAIN" not in spokes:
        failures.append(
            "the plates are no longer lit per plate by their angle to the light; ten "
            "identical plates are a printed ring"
        )
    if "additive.alpha = 255" not in spokes:
        failures.append(
            "drawSpokes leaves the shared paint at the last plate's alpha, so the core, "
            "the glass and the next frame's blobs all inherit one plate's shading"
        )

    housing = kotlin_body(src, "drawHousing")
    if "HOUSING_SHADOW_ALPHA" not in housing:
        failures.append(
            "the coil assembly casts no shadow on the ball behind it, so it is dark "
            "rather than at a depth"
        )
    if "HOUSING_WALL_ALPHA" not in housing:
        failures.append(
            "the recess floor is uniformly dark again: a recess has a wall catching the "
            "light and a wall opposite it in shadow, and that ramp is the whole read"
        )
    if "litX(f, HUB_FACTOR" not in housing:
        failures.append(
            "the hub ring is struck along the diagonal of a box again rather than along "
            "the light, and a machined part lit from somewhere else reads as a decal"
        )

    halo = kotlin_body(src, "drawHalo")
    if "BLOOM_WIDE" not in halo:
        failures.append(
            "the bloom has lost its wide dim skirt. Light in air falls off for a long "
            "way at an intensity you would not look for; a single tight halo is a ring "
            "painted round the orb."
        )
    if halo.count("f.maxRadius") < 2:
        failures.append(
            "one of the bloom's passes is no longer clamped to the view's own bounds, "
            "and a clipped gradient is the bright SQUARE this whole file budgets against"
        )
    return failures


def call_args(src: str, at: int) -> str:
    """The text between the parens of the call whose `(` is at [at]."""
    depth = 0
    for i in range(at, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return src[at + 1 : i]
    return ""


def terms(text: str) -> list[str]:
    """A comma-separated argument list, split at the TOP level only."""
    depth = 0
    out = [""]
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("")
        else:
            out[-1] += ch
    return [t.strip() for t in out if t.strip()]


def items(text: str) -> int:
    """How many top-level arguments a list has.

    Kotlin allows a trailing comma and this file uses them, so an empty last
    term is not an argument.
    """
    return len(terms(text))


def check_no_gradient_can_throw() -> list[str]:
    """The failure mode of this file is a crash, not an ugly orb.

    `RadialGradient` throws on a radius of zero or less, and every gradient
    throws when its colours and its stops are different lengths — and the boot
    sequence starts the ball at exactly zero, so the first of those is a live
    path rather than a theoretical one. None of it is visible by reading the
    call: the stops are usually a named constant declared two hundred lines
    away, and the depth pass added four-stop gradients beside three-stop ones.

    Nobody working on this can see the result. So the arithmetic is checked
    here instead of on a phone.
    """
    failures = []
    src = REACTOR.read_text(encoding="utf-8")

    stop_arrays: dict[str, int] = {}
    for m in re.finditer(r"val ([A-Z][A-Z0-9_]*)\s*=\s*floatArrayOf\(", src):
        stop_arrays[m.group(1)] = items(call_args(src, m.end() - 1))

    for m in re.finditer(r"\b(RadialGradient|LinearGradient|SweepGradient)\(", src):
        args = call_args(src, m.end() - 1)
        if "intArrayOf(" not in args:
            continue
        colours = items(call_args(args, args.index("intArrayOf(") + len("intArrayOf")))
        after = args[args.index("intArrayOf(") :]
        if "floatArrayOf(" in after:
            stops = items(call_args(after, after.index("floatArrayOf(") + len("floatArrayOf")))
            where = "its inline stops"
        else:
            named = re.search(r"\b([A-Z][A-Z0-9_]*_STOPS)\b", after)
            if not named:
                failures.append(
                    f"a {m.group(1)} at offset {m.start()} passes no stops this file can "
                    "find, so nothing checks it against its colours"
                )
                continue
            stops = stop_arrays.get(named.group(1), -1)
            where = named.group(1)
            if stops < 0:
                failures.append(f"{where} is used as stops but never declared")
                continue
        if colours != stops:
            failures.append(
                f"a {m.group(1)} has {colours} colours and {stops} stops ({where}). "
                "Skia throws on that, and it throws on the main thread, sixty times a "
                "second, on a surface nobody can see from here."
            )
        if colours < 2:
            failures.append(f"a {m.group(1)} has {colours} colour(s); it needs at least 2")

    # ...and a radius of zero. Every function that builds a gradient has to have
    # measured something against MIN_DRAW_PX before it gets there.
    for m in re.finditer(r"\bRadialGradient\(", src):
        head = src.rfind("private fun ", 0, m.start())
        if head < 0 or "MIN_DRAW_PX" not in src[head : m.start()]:
            fn = re.match(r"private fun (\w+)", src[head:]) if head >= 0 else None
            failures.append(
                f"{fn.group(1) if fn else 'a function'} builds a RadialGradient without "
                "guarding its radius against MIN_DRAW_PX first. A radius of zero throws, "
                "and the boot sequence starts the ball at exactly zero."
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
    swell = re.search(r"R = (?:max\()?BALL \* breath \* \(1\.0 \+ ([0-9.]+) \* lvl\)", src)
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
    # The rate, and that the spin integrates it — either written into the
    # integration or lifted out into a `spinRate`, since the coils turn at
    # SPOKE_SPIN_RATIO of the same number and may want it named.
    if not re.search(r"0\.35 \+ \(smoothState > 0\.5 \? 0\.35 : 0\.0\)", web) or not re.search(
        r"spin = \(spin \+ dt \*", web
    ):
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
      * the **sphere's shading after the blobs and before the coils**: it is
        the colour field it has to shade, and the coils have their own
        per-plate lighting. Drawn last it would grey the plates and the core;
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
        ("drawSphereShading(", "the sphere's near side and terminator"),
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
    # The substrate, the housing and the glass must NOT be additive: screening a
    # dark colour onto anything is very nearly a no-op, so an additive substrate
    # is no substrate, an additive recess is no recess and an additive shadow is
    # no shadow.
    for fn in ("drawSubstrate", "drawHousing", "drawGlass"):
        # A missing function is a failure, not a traceback: this used to index
        # straight into the split and blow up, which reports nothing at all
        # about the other checks.
        body = kotlin_body(src, fn)
        if not body:
            failures.append(f"ReactorOrb has no {fn}")
        elif "plain.shader" not in body:
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
    # specular off the cover, and a fresnel limb. Patterns, not literals — the
    # shader clamps and names its own terms, and what is pinned is that it still
    # computes them.
    for pattern, what in (
        (r"sqrt\(max\(1\.0 - \w+ \* \w+, 0\.0\)\)", "the sphere normal"),
        (r"dot\(n, H\)", "the half-vector its specular stands on"),
        (r"pow\(\s*(?:ndh|clamp\(dot\(n, H\)|max\(dot\(n, H\))", "the specular highlight"),
        (r"pow\(\s*(?:grazing|1\.0 - n\.z|1\.0 - z)", "the fresnel limb"),
    ):
        if not re.search(pattern, web):
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
    # The blob phases may arrive as one float or as a vec3 of three — each blob
    # integrating its own is strictly better, since multiplying one shared phase
    # by 0.73 puts a jump in the second blob every time that phase wraps — but
    # either way they must be integrated on the CPU and arrive as uniforms.
    if not re.search(r"uniform (?:float uPhase|vec3 uPhases);", web):
        failures.append(
            "the web shader derives its blob phases from uTime again; a state change "
            "would jump the animation by several turns"
        )
    for uniform in ("uSpin", "uBreath"):
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


def check_the_shader_source_survives_being_a_template_literal() -> list[str]:
    """No backtick inside the GLSL.

    Orb.svelte holds its shader in a JS template literal, so ONE backtick ends
    the string early. What follows is then parsed as TypeScript, which is a
    syntax error somewhere far away, and — much worse when it happens to parse —
    a shader that compiles to nothing and a canvas that renders nothing. There
    is no visual test here to catch that.

    It is not hypothetical: this file's own house style writes identifiers in
    prose as `name`, and a comment added to the coil function did exactly that,
    took svelte-check from 28 errors to 32, and would have shipped a blank orb.
    Inside the shader, name functions as coilAt() and variables bare.
    """
    text = WEB_ORB.read_text(encoding="utf-8")
    failures: list[str] = []
    # The shader literals, not the component's own TypeScript: everything from
    # the first `const ... = \`` that contains GLSL through its closing
    # backtick. Located by the precision qualifier every fragment shader here
    # opens with, so this cannot drift onto some other string.
    for marker in ("precision highp float", "precision mediump float"):
        at = text.find(marker)
        if at < 0:
            continue
        opened = text.rfind("`", 0, at)
        closed = text.find("`", at)
        if opened < 0 or closed < 0:
            failures.append(
                f"the shader containing {marker!r} is not inside a template "
                "literal any more; this check no longer guards anything"
            )
            continue
        body = text[opened + 1 : closed]
        # `void main` sits near the END of the shader, so a stray backtick
        # anywhere above it closes the literal before this is inside. Testing
        # for the marker itself would be vacuous: it is what located `opened`.
        if "void main" not in body:
            failures.append(
                "a backtick inside the GLSL ends the template literal early. "
                "The shader then compiles to nothing and the orb renders as an "
                "empty canvas, with no error anybody sees. Write identifiers "
                "bare in shader comments, not in backticks."
            )
    return failures


def main() -> int:
    for path in (REACTOR, SIRI_VIEW, HUD_VIEW, PALETTE, WEB_ORB):
        if not path.is_file():
            print(f"FAIL  {path} is missing", file=sys.stderr)
            return 1

    failures = (
        check_the_two_renderers_agree()
        + check_the_shader_source_survives_being_a_template_literal()
        + check_the_geometry_budget_holds()
        + check_the_coils_are_a_layered_assembly()
        + check_the_two_renderers_are_lit_the_same()
        + check_the_ball_is_a_sphere()
        + check_no_gradient_can_throw()
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
        f"reactor orb: {len(GEOMETRY)} proportions, one light at {LIGHT}, 4 palettes, "
        "3 rate tables, the draw order and the geometry budget agree across Skia and "
        "GLSL"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
