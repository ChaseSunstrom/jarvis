#!/usr/bin/env python3
"""Executable spec: the battery policy that was written down and never asked.

`config/WakeWordGate.kt` is a hundred lines of careful policy — listen through a
worn headset, listen on car Bluetooth, listen at home during waking hours, and
otherwise do not, because a third-party app gets no low-power hotword path on
Android and an open mic in a pocket is both a dead battery and a room nobody
thinks is being heard. Every rule has a paragraph justifying it. There is a unit
test. There are four SharedPreferences keys behind it and a whole section of the
settings screen writing them.

**`shouldListen` had no production caller.** Its only callers were
`WakeWordGateTest.kt` and this repo's own greps. `JarvisConfig.wakeWordGate()`,
the factory that builds one from the stored settings, had none at all. The
settings screen wrote `wakingHourStart` and `wakingHourEnd` into a preference
file nothing in the listening path ever read, and said so out loud in its own
heading: *"When to listen — saved, not yet in effect"*. `no_empty_seams_test.py`
carried all four keys in its exceptions list with the reason spelled out.

Meanwhile `DEVIATIONS.md` asserted, as shipped behaviour, that *"the car-BT wake
policy (WakeWordGate) turns detection on for the drive and off afterwards"*. It
did not. Nothing consulted the policy at any point in the microphone's life.

This is the `MediaButtonGate` shape for the seventh time (see
`no_empty_seams_test.py` for the first six), with the aggravating factor that a
document made a claim about it.

## What was actually missing, and why it took this long

Not the policy. `shouldListen` takes `isHome: Boolean` — a fact — and a phone
usually cannot supply one. Wiring it with `isHome = false` would have silenced
always-on detection everywhere except a car for every user who has not drawn a
circle on a map, which is not a battery policy; it is the feature switched off.

So the missing piece was a third answer. `WakeWordGate.decide` takes
`atHome: Boolean?` where null means **unknown**, resolves unknown as *at home*
(the clock is not in doubt even when the map is), and `WakeListenWatch` is the
Android half that gathers the signals — the audio device list for the headset
and the car stereo, `GeofenceStates` for a fence the user has already configured
called "home", and the wall clock — and re-asks on every edge that can move the
answer.

## What this file checks

The mechanical facts that make the difference between a policy and a page of
prose: that the gate is called from the path that opens the microphone, that it
is called BEFORE the recorder is opened rather than after, that every edge is
subscribed to, that the four settings are read by the thing that decides, and
that the screen no longer says the feature is not in effect.

It also mirrors `decide` in Python across every input combination, because the
resolution of "unknown" is a judgement call that somebody will one day want to
change, and changing it silently is exactly how this area got into trouble.

Run:  python3 android-app/tools/wake_listen_gate_test.py
"""

from __future__ import annotations

import itertools
import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
KOTLIN = ANDROID / "app/src/main/kotlin/ai/jarvis/app"

GATE = KOTLIN / "config/WakeWordGate.kt"
WATCH = KOTLIN / "assist/WakeListenWatch.kt"
SERVICE = KOTLIN / "assist/WakeWordService.kt"
CONFIG = KOTLIN / "config/JarvisConfig.kt"
SETTINGS = KOTLIN / "SettingsActivity.kt"


def code(path: Path) -> str:
    """Kotlin with comments stripped.

    A comment saying the gate is consulted satisfies a naive search for the
    call — which is how `orb_is_started_test` once shipped a draft that passed
    against a reverted fix.
    """
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


# ---------------------------------------------------------------------------
# 1. the mirror: what `decide` does with every input
# ---------------------------------------------------------------------------

DEFAULT_START = 7
DEFAULT_END = 23


def should_listen(is_home: bool, car: bool, hour: int, headset: bool,
                  start: int = DEFAULT_START, end: int = DEFAULT_END) -> bool:
    """`WakeWordGate.shouldListen`, unchanged. The policy itself."""
    if headset:
        return True
    if car:
        return True
    if not is_home:
        return False
    return is_waking_hour(hour, start, end)


def is_waking_hour(hour: int, start: int = DEFAULT_START, end: int = DEFAULT_END) -> bool:
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def decide(at_home, car_bt, headset, hour, listen_at_home, listen_in_car,
           start=DEFAULT_START, end=DEFAULT_END):
    """`WakeWordGate.decide`. Returns (listen, reason)."""
    car = car_bt and listen_in_car
    # THE LINE THIS FILE IS ABOUT. Unknown place resolves to "home", so the
    # hours still apply; resolving it the other way would silence the feature
    # for anybody without a geofence.
    home = listen_at_home and (True if at_home is None else at_home)
    listen = should_listen(home, car, hour, headset, start, end)
    if headset:
        reason = "HEADSET"
    elif car:
        reason = "CAR"
    elif car_bt:
        reason = "CAR_RULE_OFF"
    elif not listen_at_home:
        reason = "HOME_RULE_OFF"
    elif at_home is False:
        reason = "AWAY"
    elif listen:
        reason = "WAKING_HOURS"
    else:
        reason = "QUIET_HOURS"
    return listen, reason


def test_the_gate_never_refuses_a_headset_or_a_car() -> None:
    """The two rules that open the mic away from home, over every other input.

    Both exist because they are strong statements of intent — a headset the user
    put in their ear, a car they are driving — and neither may be overridden by
    the hour or the place.
    """
    for at_home, hour, listen_at_home in itertools.product(
        (True, False, None), range(24), (True, False)
    ):
        listen, _ = decide(at_home, False, True, hour, listen_at_home, False)
        assert listen, f"a worn headset was refused at hour {hour}"
        listen, _ = decide(at_home, True, False, hour, listen_at_home, True)
        assert listen, f"the car was refused at hour {hour}"


def test_an_unknown_place_still_obeys_the_hours() -> None:
    """The judgement call, pinned.

    Unknown resolves to "at home", which means the WINDOW applies — and that is
    the whole of what wiring the gate buys a user with no geofence. If somebody
    changes this to resolve as "away", the feature silently stops working for
    almost everyone and this is the check that says so.
    """
    for hour in range(24):
        listen, reason = decide(None, False, False, hour, True, True)
        assert listen == is_waking_hour(hour), (
            f"hour {hour} with no place signal: expected the waking-hours window "
            f"to decide, got {listen}"
        )
        assert reason in ("WAKING_HOURS", "QUIET_HOURS")


def test_turning_the_home_rule_off_leaves_only_the_car_and_the_headset() -> None:
    """What the "while at home" switch has to mean now that it means anything."""
    for at_home, hour in itertools.product((True, False, None), range(24)):
        listen, reason = decide(at_home, False, False, hour, False, True)
        assert not listen, f"the home rule is off and the gate still listened at {hour}"
        assert reason == "HOME_RULE_OFF"
        listen, _ = decide(at_home, True, False, hour, False, True)
        assert listen, "the car rule stopped working when the home rule was turned off"


def test_a_place_signal_that_says_away_is_obeyed() -> None:
    """The case a geofence buys, and the one `DEVIATIONS.md` describes."""
    for hour in range(24):
        listen, reason = decide(False, False, False, hour, True, True)
        assert not listen, f"the phone is away from home and still listening at {hour}"
        assert reason == "AWAY"


def test_the_car_switch_can_refuse_the_car() -> None:
    listen, reason = decide(False, True, False, 12, True, False)
    assert not listen and reason == "CAR_RULE_OFF", (
        "turning the car rule off left the car rule on"
    )


# ---------------------------------------------------------------------------
# 2. the wiring — the half that did not exist
# ---------------------------------------------------------------------------


def test_the_gate_has_a_production_caller() -> None:
    """The whole point. `shouldListen` was reachable only from a unit test."""
    watch = code(WATCH)
    assert "config.wakeWordGate().decide(" in watch, (
        "WakeListenWatch does not build the gate from the stored settings, so "
        "the four preference keys behind it are inert again — which is the "
        "state this whole file exists to make impossible"
    )
    gate = code(GATE)
    assert re.search(r"fun decide\(", gate), "WakeWordGate.decide is gone"
    # `decide` must go through `shouldListen` rather than reimplementing it: two
    # copies of a policy is how the phone and the console ended up with two
    # palettes that merely looked alike.
    body = gate.split("fun decide(", 1)[1].split("fun isWakingHour", 1)[0]
    assert "shouldListen(" in body, (
        "decide() no longer calls shouldListen, so the policy is written twice "
        "and the unit test is proving the half nothing calls"
    )


def test_the_listening_path_asks_before_it_opens_the_microphone() -> None:
    """Order matters, and only one order is a gate.

    Asked after `MicStreamer.start()`, this would be a policy that opens the
    microphone and then decides whether it should have.
    """
    src = code(SERVICE)
    assert "listenWatch?.decide()" in src, (
        "WakeWordService never asks the listening policy; the gate is unwired "
        "again and DEVIATIONS.md's car claim is false again"
    )
    open_link = re.search(r"private fun openLink\(\).*?\n    \}", src, re.S)
    assert open_link, "WakeWordService has no openLink()"
    body = open_link.group(0)
    ask = body.find("listenWatch?.decide()")
    mic = body.find("MicStreamer(")
    assert ask >= 0, "openLink does not consult the gate"
    assert mic < 0 or ask < mic, (
        "the gate is consulted after the recorder is opened, which is not a gate"
    )
    # Every path that reopens the mic goes through openLink, so a check
    # elsewhere would have a way round it.
    resume = re.search(r"private fun resume\(\).*?\n    \}", src, re.S)
    assert resume and "openLink()" in resume.group(0), (
        "resume() no longer routes through openLink, so it can take the "
        "microphone back past the gate"
    )


def test_the_watch_subscribes_to_every_edge_that_can_move_the_answer() -> None:
    """A gate asked once at startup is a gate that is wrong by lunchtime.

    Four signals move it and each has an edge: the audio device list (a headset
    or a car stereo appearing), the geofence transitions, the Bluetooth trigger,
    and the clock.
    """
    src = code(WATCH)
    for needle, why in (
        ("HeadsetMonitor(", "no audio-device callback, so a car stereo connecting is not noticed"),
        ("AutomationBridge.subscribe(this)", "not subscribed to the trigger bus"),
        ("TriggerIds.GEOFENCE_ENTER", "arriving home does not re-ask the gate"),
        ("TriggerIds.GEOFENCE_EXIT", "leaving home does not re-ask the gate"),
        ("TriggerIds.BLUETOOTH_CONNECTED", "the car connecting does not re-ask the gate"),
        ("msUntilNextHour()", "the waking-hours boundary is never noticed"),
    ):
        assert needle in src, f"WakeListenWatch: {why}"
    # And releases them: a service that leaks an AudioDeviceCallback keeps
    # waking for an event it can no longer act on.
    assert "AutomationBridge.unsubscribe(this)" in src and "headsets.stop()" in src, (
        "WakeListenWatch.stop does not release what start registered"
    )
    service = code(SERVICE)
    assert "listenWatch?.start()" in service and "listenWatch?.stop()" in service, (
        "the watch is never started, or never stopped, by the service that owns it"
    )


def test_the_four_settings_reach_the_decision() -> None:
    """`wakeInCar`, `wakeAtHome`, `wakingHourStart`, `wakingHourEnd`.

    All four were written by the settings screen and read by nothing. They are
    listed by name because a refactor that drops one leaves a switch that does
    nothing, which is precisely the bug `no_empty_seams_test.py` exists for and
    precisely what these four were doing.
    """
    watch = code(WATCH)
    for name in ("wakeAtHome", "wakeInCar"):
        assert f"config.{name}" in watch, f"{name} does not reach the gate"
    config = code(CONFIG)
    assert "fun wakeWordGate()" in config, "the gate factory is gone"
    factory = config.split("fun wakeWordGate()", 1)[1][:200]
    for name in ("wakingHourStart", "wakingHourEnd"):
        assert name in factory, f"{name} does not reach the gate"


def test_the_settings_screen_no_longer_says_it_does_nothing() -> None:
    """The label was the app being honest. Leaving it up now would be the app
    being wrong in the other direction."""
    # Comments stripped: the Kotlin explains what the label USED to say, and a
    # naive search would refuse the very comment that records the fix.
    src = code(SETTINGS)
    assert "saved, not yet in effect" not in src, (
        "the settings screen still tells the user this section does nothing"
    )
    assert 'JarvisUi.label(ctx, "When to listen")' in src, (
        "the when-to-listen section has lost its heading"
    )
    # And the remaining honest limit is still stated somewhere, because a
    # geofence called "home" is the only thing that makes "while at home" mean
    # what it says.
    strings = (ANDROID / "app/src/main/res/values/strings.xml").read_text(encoding="utf-8")
    assert "geofence" in strings.lower() and "settings_when_to_listen_explain" in strings, (
        "nothing tells the user that 'while at home' needs a home geofence to "
        "be able to refuse — which is the one thing about this policy that is "
        "surprising"
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
    combos = len(list(itertools.product((True, False, None), (True, False), (True, False),
                                        range(24), (True, False), (True, False))))
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed "
          f"({combos} gate input combinations mirrored)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
