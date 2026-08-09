#!/usr/bin/env python3
"""Executable spec for the Android geofence maths.

Mirrors `app/src/main/kotlin/ai/jarvis/app/automation/triggers/GeofenceMath.kt`.
GrapheneOS has no Play Services, so there is no fused geofencing API: Jarvis
polls a coarse fix and decides for itself whether the phone is inside a circle.
Two things make that decision non-obvious, and both are here:

  * HYSTERESIS. A fix that jitters across the boundary would otherwise fire
    "arrived home" / "left home" over and over. Enter needs distance <=
    radius - hysteresis, exit needs distance >= radius + hysteresis, and inside
    the band the previous state is kept.
  * ACCURACY. A 500 m network fix says nothing useful about a 100 m circle, so
    such a fix is discarded rather than believed. A fix with no accuracy at all
    is believed — refusing every fix would mean never firing.

And one product decision: the FIRST fix only establishes where you are. It
never reports a transition, so restarting the phone inside the geofence does
not announce that you have just arrived home.

Run:  python3 android-app/tools/geofence_test.py
  or: python3 -m pytest android-app/tools/geofence_test.py -q
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

# WGS84 mean radius. Same constant as the Kotlin.
EARTH_RADIUS_M = 6_371_008.8

UNKNOWN, INSIDE, OUTSIDE = "UNKNOWN", "INSIDE", "OUTSIDE"
ENTER, EXIT = "ENTER", "EXIT"

# Below this, a hysteresis band would swallow the whole circle.
MIN_RADIUS_M = 10.0
DEFAULT_HYSTERESIS_M = 50.0


# --- the rules, mirrored from GeofenceMath.kt -------------------------------


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    # min(1.0, …) keeps floating point from handing asin a value above 1 for
    # antipodal points, which would be a NaN distance.
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def effective_hysteresis(radius_m: float, hysteresis_m: float) -> float:
    """Clamped so the enter threshold can never collapse to zero or below.

    A 100 m circle with 200 m of hysteresis would need a fix at the exact
    centre to ever report "inside"; capping at half the radius keeps the inner
    threshold at radius/2 or better.
    """
    if hysteresis_m <= 0:
        return 0.0
    return min(hysteresis_m, radius_m / 2.0)


def is_fix_usable(accuracy_m: float | None, radius_m: float) -> bool:
    """A fix is believed when its error is smaller than the circle it is being
    tested against. Unknown accuracy (None, NaN or negative) is believed."""
    if accuracy_m is None:
        return True
    if isinstance(accuracy_m, float) and math.isnan(accuracy_m):
        return True
    if accuracy_m < 0:
        return True
    return accuracy_m <= radius_m


def classify(distance_m: float, radius_m: float, hysteresis_m: float, previous: str) -> str:
    """State after this fix. Inside the band, the previous state survives."""
    h = effective_hysteresis(radius_m, hysteresis_m)
    if distance_m <= radius_m - h:
        return INSIDE
    if distance_m >= radius_m + h:
        return OUTSIDE
    return previous


def update(
    previous: str,
    distance_m: float,
    radius_m: float,
    hysteresis_m: float = DEFAULT_HYSTERESIS_M,
    accuracy_m: float | None = None,
) -> tuple[str, str | None]:
    """(new state, transition or None).

    A transition is only reported when the state actually changed AND we had a
    state to change from — the first fix is a baseline, not an arrival.
    """
    if radius_m < MIN_RADIUS_M:
        return previous, None  # a circle that small is noise, not a place
    if not is_fix_usable(accuracy_m, radius_m):
        return previous, None
    state = classify(distance_m, radius_m, hysteresis_m, previous)
    if state == previous or previous == UNKNOWN:
        return state, None
    return state, (ENTER if state == INSIDE else EXIT)


# --- tests ------------------------------------------------------------------


def close(got: float, want: float, tol: float, name: str):
    if abs(got - want) > tol:
        raise AssertionError(f"{name}: got {got:.3f}, want {want:.3f} (+/-{tol})")


def check(name: str, got, want):
    if got != want:
        raise AssertionError(f"{name}: got {got!r}, want {want!r}")


# --- haversine: checked against closed-form answers, not against itself ------


def test_zero_distance():
    close(haversine_m(51.5, -0.12, 51.5, -0.12), 0.0, 1e-6, "same point")


def test_one_degree_of_latitude():
    """One degree of arc anywhere on a sphere is R * pi / 180."""
    expected = EARTH_RADIUS_M * math.pi / 180.0
    close(haversine_m(0, 0, 1, 0), expected, 0.001, "1 deg latitude at equator")
    close(haversine_m(45, 20, 46, 20), expected, 0.001, "1 deg latitude at 45N")
    close(haversine_m(-10, -30, -11, -30), expected, 0.001, "1 deg latitude south")


def test_one_degree_of_longitude_shrinks_with_latitude():
    """cos(lat) scaling — the classic bug is forgetting it and treating a
    degree of longitude as constant.

    At the equator a line of longitude IS a great circle, so the answer is
    exact. Away from it the great-circle path cuts slightly poleward of the
    parallel, so the distance must be a little SHORTER than the parallel arc —
    under a metre for one degree. Checking the sign of that difference catches
    a formula that has quietly become the flat cos(lat) approximation.
    """
    one_degree = EARTH_RADIUS_M * math.pi / 180.0
    close(haversine_m(0, 0, 0, 1), one_degree, 0.001, "1 deg longitude at equator")
    for lat in (60.0, 89.0):
        parallel_arc = one_degree * math.cos(math.radians(lat))
        d = haversine_m(lat, 0, lat, 1)
        if not (0 <= parallel_arc - d < 1.0):
            raise AssertionError(
                f"at {lat}N: great circle {d:.3f} m vs parallel arc "
                f"{parallel_arc:.3f} m — difference {parallel_arc - d:.3f} m"
            )
        if abs(parallel_arc - d) / parallel_arc > 2e-5:
            raise AssertionError(f"at {lat}N: {d:.3f} m is nowhere near {parallel_arc:.3f} m")


def test_antipodal_points_do_not_produce_nan():
    close(haversine_m(0, 0, 0, 180), math.pi * EARTH_RADIUS_M, 0.01, "antipodal on equator")
    d = haversine_m(45, 10, -45, -170)
    if math.isnan(d):
        raise AssertionError("antipodal distance is NaN")
    close(d, math.pi * EARTH_RADIUS_M, 1.0, "antipodal through the poles")


def test_symmetry():
    a = haversine_m(51.5007, -0.1246, 48.8584, 2.2945)
    b = haversine_m(48.8584, 2.2945, 51.5007, -0.1246)
    close(a, b, 1e-9, "symmetric")


def test_known_city_pair():
    """London Eye to the Eiffel Tower, about 343.5 km great-circle."""
    d = haversine_m(51.5007, -0.1246, 48.8584, 2.2945)
    if not (340_000 < d < 347_000):
        raise AssertionError(f"London->Paris: got {d:.0f} m, want ~343500 m")


def test_dateline_is_not_a_wall():
    """Two points 0.02 degrees apart across +/-180 must be ~2 km, not ~40000 km."""
    d = haversine_m(0, 179.99, 0, -179.99)
    close(d, EARTH_RADIUS_M * math.radians(0.02), 0.01, "across the dateline")


def test_small_distances_are_sane():
    """100 m north is 100 m."""
    metres_per_degree = EARTH_RADIUS_M * math.pi / 180.0
    delta = 100.0 / metres_per_degree
    close(haversine_m(52.0, 4.0, 52.0 + delta, 4.0), 100.0, 0.01, "100 m north")


# --- hysteresis -------------------------------------------------------------


def test_hysteresis_is_clamped_to_half_the_radius():
    check("h fits", effective_hysteresis(200, 50), 50.0)
    check("h too big", effective_hysteresis(100, 200), 50.0)
    check("h exactly half", effective_hysteresis(100, 50), 50.0)
    check("h zero", effective_hysteresis(100, 0), 0.0)
    check("h negative", effective_hysteresis(100, -10), 0.0)


def test_classify_inner_and_outer_thresholds():
    # radius 200, hysteresis 50 -> enter at <=150, exit at >=250.
    check("well inside", classify(100, 200, 50, UNKNOWN), INSIDE)
    check("on the enter line", classify(150, 200, 50, UNKNOWN), INSIDE)
    check("just outside enter", classify(151, 200, 50, UNKNOWN), UNKNOWN)
    check("on the exit line", classify(250, 200, 50, UNKNOWN), OUTSIDE)
    check("well outside", classify(1000, 200, 50, UNKNOWN), OUTSIDE)


def test_band_keeps_the_previous_state():
    """This is the whole point of hysteresis: at radius exactly, whichever
    state you were in survives."""
    check("band keeps inside", classify(200, 200, 50, INSIDE), INSIDE)
    check("band keeps outside", classify(200, 200, 50, OUTSIDE), OUTSIDE)
    check("band keeps unknown", classify(200, 200, 50, UNKNOWN), UNKNOWN)


def test_no_hysteresis_is_a_plain_circle():
    check("plain in", classify(199, 200, 0, OUTSIDE), INSIDE)
    check("plain edge", classify(200, 200, 0, OUTSIDE), INSIDE)
    check("plain out", classify(201, 200, 0, INSIDE), OUTSIDE)


# --- transitions ------------------------------------------------------------


def test_first_fix_is_a_baseline_not_an_arrival():
    check("first fix inside", update(UNKNOWN, 10, 200, 50), (INSIDE, None))
    check("first fix outside", update(UNKNOWN, 5000, 200, 50), (OUTSIDE, None))


def test_enter_and_exit_fire_once():
    state, transition = update(OUTSIDE, 100, 200, 50)
    check("enter state", state, INSIDE)
    check("enter transition", transition, ENTER)
    state, transition = update(state, 120, 200, 50)
    check("still inside", state, INSIDE)
    check("no repeat enter", transition, None)
    state, transition = update(state, 400, 200, 50)
    check("exit state", state, OUTSIDE)
    check("exit transition", transition, EXIT)
    state, transition = update(state, 500, 200, 50)
    check("no repeat exit", transition, None)


def test_jitter_across_the_boundary_fires_nothing():
    """A fix bouncing 190/210/195/205 m around a 200 m circle must not produce
    a single event. Without hysteresis this is four events."""
    state = OUTSIDE
    events = []
    for distance in (190, 210, 195, 205, 199, 201, 200):
        state, transition = update(state, distance, 200, 50)
        if transition:
            events.append((distance, transition))
    check("jitter events", events, [])
    check("jitter state", state, OUTSIDE)


def test_a_real_arrival_still_fires_through_the_jitter():
    state = OUTSIDE
    events = []
    for distance in (400, 300, 210, 190, 140, 60, 10):
        state, transition = update(state, distance, 200, 50)
        if transition:
            events.append((distance, transition))
    check("arrival events", events, [(140, ENTER)])


def test_full_round_trip():
    state = UNKNOWN
    events = []
    for distance in (900, 400, 100, 20, 100, 400, 900, 100):
        state, transition = update(state, distance, 200, 50)
        if transition:
            events.append(transition)
    check("round trip", events, [ENTER, EXIT, ENTER])


# --- accuracy ---------------------------------------------------------------


def test_a_fix_coarser_than_the_circle_is_discarded():
    """A 500 m fix cannot tell you anything about a 200 m circle."""
    check("coarse fix ignored", update(OUTSIDE, 10, 200, 50, accuracy_m=500), (OUTSIDE, None))
    check("coarse fix inside too", update(INSIDE, 5000, 200, 50, accuracy_m=500), (INSIDE, None))


def test_a_fix_as_good_as_the_radius_is_believed():
    check("accuracy == radius", update(OUTSIDE, 10, 200, 50, accuracy_m=200), (INSIDE, ENTER))
    check("accuracy < radius", update(OUTSIDE, 10, 200, 50, accuracy_m=50), (INSIDE, ENTER))


def test_unknown_accuracy_is_believed():
    """Location.hasAccuracy() can be false. Discarding every such fix would
    mean a device that never fires a location trigger at all."""
    check("none", update(OUTSIDE, 10, 200, 50, accuracy_m=None), (INSIDE, ENTER))
    check("negative", update(OUTSIDE, 10, 200, 50, accuracy_m=-1), (INSIDE, ENTER))
    check("nan", update(OUTSIDE, 10, 200, 50, accuracy_m=float("nan")), (INSIDE, ENTER))


def test_a_silly_radius_is_refused():
    """Anything under 10 m is GPS noise, not a place. Accepting it would fire
    enter/exit constantly while the phone sits on a table."""
    check("tiny radius", update(OUTSIDE, 1, 5, 1), (OUTSIDE, None))
    check("zero radius", update(INSIDE, 0, 0, 0), (INSIDE, None))


def test_end_to_end_home_geofence():
    """A 150 m circle on a real coordinate, walked in and out."""
    home = (52.3676, 4.9041)
    inside = (52.3677, 4.9043)   # ~20 m away
    away = (52.3700, 4.9100)     # ~500 m away
    r, h = 150.0, 40.0

    d_in = haversine_m(*home, *inside)
    d_out = haversine_m(*home, *away)
    if d_in > 60:
        raise AssertionError(f"fixture drift: inside point is {d_in:.0f} m away")
    if d_out < 300:
        raise AssertionError(f"fixture drift: away point is only {d_out:.0f} m away")

    state, t0 = update(UNKNOWN, d_out, r, h, accuracy_m=30)
    check("start away", (state, t0), (OUTSIDE, None))
    state, t1 = update(state, d_in, r, h, accuracy_m=30)
    check("arrive", (state, t1), (INSIDE, ENTER))
    state, t2 = update(state, d_out, r, h, accuracy_m=30)
    check("leave", (state, t2), (OUTSIDE, EXIT))


# --- structural check: the Kotlin still says the same thing -----------------

KOTLIN = (
    Path(__file__).resolve().parent.parent
    / "app/src/main/kotlin/ai/jarvis/app/automation/triggers/GeofenceMath.kt"
)

REQUIRED_IN_KOTLIN = [
    r"6_371_008\.8",
    r"fun haversineMeters\(",
    r"fun classify\(",
    r"fun update\(",
    r"fun effectiveHysteresis\(",
    r"fun isFixUsable\(",
    # the enter/exit thresholds
    r"radiusM - h",
    r"radiusM \+ h",
    # first fix is a baseline
    r"previous == GeoState\.UNKNOWN",
    # min(1.0, ...) before asin, or the antipodal NaN comes back
    r"coerceAtMost\(1\.0\)",
]


def test_kotlin_source_still_matches():
    if not KOTLIN.exists():
        raise AssertionError(f"missing Kotlin source: {KOTLIN}")
    src = KOTLIN.read_text()
    problems = []
    if "import android." in src:
        problems.append("GeofenceMath.kt must stay free of Android imports")
    for pattern in REQUIRED_IN_KOTLIN:
        if not re.search(pattern, src):
            problems.append(f"GeofenceMath.kt no longer contains /{pattern}/")
    if problems:
        raise AssertionError("; ".join(problems))


# --- runner -----------------------------------------------------------------


def main() -> int:
    failures: list[str] = []
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001 - a raising test is a failing test
            failures.append(f"{t.__name__} raised {type(exc).__name__}: {exc}")
    if failures:
        print(f"FAIL  geofence_test: {len(failures)} problem(s) in {len(tests)} tests")
        for f in failures:
            print("  -", f)
        return 1
    print(f"ok    geofence_test: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
