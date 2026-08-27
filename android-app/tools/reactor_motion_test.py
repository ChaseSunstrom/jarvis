#!/usr/bin/env python3
"""The reactor moves for what Jarvis does, on the phone as on the web (M53 → M61).

`docs/design/MOTION.md` names three moves beyond the idle clock: the blades
sweep once per tool call and settle over `motion.dur.sweep`; the rim beats on
`motion.reactor.speak` while speaking; the iris gathers while a camera is
being looked at. `Reactor.svelte` has them as `work`, `speak` and `looking`;
`ReactorOrb.kt` as `workSweep`, `cadence` and `looking`. This reads both as
text and holds the phone to the same vocabulary and the same tokens.

Run:  python3 android-app/tools/reactor_motion_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORB = ROOT / "app/src/main/kotlin/ai/jarvis/app/ui/ReactorOrb.kt"
VIEW = ROOT / "app/src/main/kotlin/ai/jarvis/app/ui/JarvisOrbView.kt"
CONVERSATION = ROOT / "app/src/main/kotlin/ai/jarvis/app/assist/JarvisConversation.kt"
WEB = ROOT.parent / "jarvis-web/src/lib/ui/Reactor.svelte"


def test_the_instrument_has_the_three_moves():
    orb = ORB.read_text()
    for field in ("var workSweep", "var cadence", "var looking"):
        assert field in orb, f"ReactorOrb.Frame lacks {field}"
    assert "f.workSweep" in orb[orb.index("private fun drawBlades"):], "the blades ignore the sweep"
    assert "f.cadence" in orb[orb.index("private fun drawLens"):], "the rim ignores the cadence"
    assert "f.looking" in orb[orb.index("private fun drawLens"):], "the iris ignores looking"


def test_the_view_times_them_by_the_tokens():
    view = VIEW.read_text()
    assert "JarvisTokens.Motion.Dur.SWEEP" in view, "the sweep is not timed by motion.dur.sweep"
    assert "JarvisTokens.Motion.Reactor.SPEAK" in view, "the cadence is not timed by motion.reactor.speak"
    assert "fun work()" in view and "var looking" in view
    assert re.search(r"f\.cadence = if \(mode == Mode\.SPEAKING\)", view), "the cadence beats outside speaking"


def test_the_conversation_drives_them_from_what_happened():
    src = CONVERSATION.read_text()
    started = src[src.index("override fun onToolStarted("): src.index("override fun onToolFinished(")]
    assert "ui.onWork()" in started, "a tool call does not sweep the blades"
    bus = src[src.index("override fun onBusEvent("): src.index("override fun onBusEvent(") + 400]
    assert "vision_look_" in bus and "lookingCaption()" in bus, "a look does not gather the iris"


def test_the_web_speaks_the_same_vocabulary():
    web = WEB.read_text()
    assert "work" in web and "looking" in web and "sweep" in web, "Reactor.svelte lost the M53 vocabulary"


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
