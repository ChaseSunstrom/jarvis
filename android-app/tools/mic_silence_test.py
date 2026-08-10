#!/usr/bin/env python3
"""Executable spec for the microphone that is open and hearing nothing.

`AudioRecord` has two ways to fail and only one of them is visible. It can
refuse to open — `MicStreamer` already reports that — or it can open, `read()`
happily, and hand back frames of digital zero forever. The second is what
Android does to a while-in-use foreground service (microphone, camera,
location) that was started while the app was in the background: no exception,
no callback, just silence. A GrapheneOS per-app *Sensors* toggle looks the
same, and so does a hardware mute.

Without `MicSilenceWatch` the always-on listener sits there with a notification
saying "Jarvis is listening" while nothing can ever reach it, which is worse
than saying nothing at all.

The whole design rests on one decision worth pinning: the test is **exactly
zero**, not a small threshold. `JarvisConversation` uses 0.0005 to tell a dead
mic from a quiet one, but it only holds that judgement for a few seconds of one
conversation. This watch runs for hours in whatever room the phone is in, and a
quiet room's RMS genuinely does sit near that figure — a threshold here would
cry wolf every night. A muted recorder is not quiet, it is arithmetically zero.

Run:  python3 android-app/tools/mic_silence_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DIGITAL_SILENCE = 0.0
MUTED_AFTER_MS = 90_000


class Watch:
    """Mirrors MicSilenceWatch.kt."""

    def __init__(self, muted_after_ms: int = MUTED_AFTER_MS) -> None:
        self.muted_after_ms = muted_after_ms
        self.silent_since = 0
        self.reported = False

    @property
    def muted(self) -> bool:
        return self.reported

    def reset(self) -> None:
        self.silent_since = 0
        self.reported = False

    def on_level(self, now_ms: int, level: float) -> bool:
        if level != DIGITAL_SILENCE:
            self.reset()
            return False
        if self.silent_since == 0:
            self.silent_since = now_ms
            return False
        if self.reported:
            return False
        if now_ms < self.silent_since:
            self.silent_since = now_ms
            return False
        if now_ms - self.silent_since < self.muted_after_ms:
            return False
        self.reported = True
        return True


def run(frames: list[tuple[int, float]]) -> tuple[int, bool]:
    """Feed a whole session. Returns (times it fired, final muted state)."""
    watch = Watch()
    fired = 0
    for now, level in frames:
        if watch.on_level(now, level):
            fired += 1
    return fired, watch.muted


def every(start: int, end: int, step: int, level: float) -> list[tuple[int, float]]:
    return [(t, level) for t in range(start, end, step)]


CASES: list[tuple[str, list[tuple[int, float]], int, bool]] = [
    (
        "a muted recorder: zeroes for three minutes",
        every(0, 180_000, 100, 0.0),
        1,
        True,
    ),
    (
        "and it fires exactly once, not sixty times a second",
        every(0, 600_000, 20, 0.0),
        1,
        True,
    ),
    (
        "a silent house is not a muted mic: a real recorder's noise floor is "
        "small but never zero",
        every(0, 600_000, 100, 0.00002),
        0,
        False,
    ),
    (
        "nor is a quiet room at the level JarvisConversation calls dead",
        every(0, 600_000, 100, 0.0005),
        0,
        False,
    ),
    (
        "a short gap between sentences is nothing at all",
        every(0, 4_000, 100, 0.0) + every(4_000, 8_000, 100, 0.3),
        0,
        False,
    ),
    (
        "silence that ends one frame before the deadline never fires",
        every(0, MUTED_AFTER_MS, 100, 0.0) + [(MUTED_AFTER_MS, 0.4)],
        0,
        False,
    ),
    (
        "one real frame in the middle resets the clock entirely",
        every(0, 80_000, 100, 0.0)
        + [(80_000, 0.2)]
        + every(80_100, 160_000, 100, 0.0),
        0,
        False,
    ),
    (
        "and after that reset it can still fire, given a fresh full run",
        every(0, 80_000, 100, 0.0)
        + [(80_000, 0.2)]
        + every(80_100, 200_000, 100, 0.0),
        1,
        True,
    ),
    (
        "a clock that jumped backwards re-seeds rather than firing instantly",
        [(500_000, 0.0), (1_000, 0.0), (2_000, 0.0)],
        0,
        False,
    ),
    (
        "a negative level is a caller bug, not silence",
        every(0, 200_000, 100, -1.0),
        0,
        False,
    ),
]


def check_cases() -> int:
    failures = 0
    for name, frames, want_fired, want_muted in CASES:
        fired, muted = run(frames)
        if fired != want_fired or muted != want_muted:
            print(
                f"FAIL  {name}: expected fired={want_fired} muted={want_muted}, "
                f"got fired={fired} muted={muted}"
            )
            failures += 1
    return failures


def check_kotlin_agrees(android: Path) -> int:
    path = android / "app/src/main/kotlin/ai/jarvis/app/assist/MicSilenceWatch.kt"
    if not path.is_file():
        print(f"FAIL  {path} is missing")
        return 1
    text = path.read_text(encoding="utf-8")
    failures = 0
    if "const val DIGITAL_SILENCE = 0f" not in text:
        print(
            "FAIL  MicSilenceWatch no longer tests for exactly zero — a threshold "
            "here reports a muted microphone every quiet night"
        )
        failures += 1
    if f"MUTED_AFTER_MS = {MUTED_AFTER_MS:_}".replace("_", "_") not in text:
        print(f"FAIL  MicSilenceWatch no longer waits {MUTED_AFTER_MS} ms")
        failures += 1
    # `level > DIGITAL_SILENCE` with DIGITAL_SILENCE == 0 would treat a negative
    # level as silence and extend the run, so the comparison itself is pinned.
    if not re.search(r"if \(level != DIGITAL_SILENCE\)", text):
        print("FAIL  onLevel no longer compares with != , so a negative level is silence")
        failures += 1
    return failures


def check_the_watch_is_actually_wired(android: Path) -> int:
    """A detector nothing calls is a comment.

    The wake listener is the only place this matters: it is the one that holds
    the mic open for hours with no screen to show a problem on.
    """
    path = android / "app/src/main/kotlin/ai/jarvis/app/assist/WakeWordService.kt"
    text = path.read_text(encoding="utf-8")
    failures = 0
    if "MicSilenceWatch()" not in text:
        print("FAIL  WakeWordService does not own a MicSilenceWatch")
        failures += 1
    if "onLevel = { level -> watchForSilence(level) }" not in text:
        print("FAIL  the wake listener's mic level is not fed to the silence watch")
        failures += 1
    if "MUTED_MESSAGE" not in text:
        print("FAIL  a detected mute is not reported anywhere the user can see")
        failures += 1
    # Reporting must not take the listener down: the notification is the only
    # surface able to offer the repair.
    watcher = re.search(r"private fun watchForSilence\(.*?\n    \}", text, re.S)
    if watcher and "stopSelf" in watcher.group(0):
        print("FAIL  a silent microphone stops the service, removing the fix with it")
        failures += 1
    if "tapToRestart = true" not in text:
        print("FAIL  the mute notification does not offer a one-tap restart")
        failures += 1
    return failures


def main() -> int:
    android = Path(__file__).resolve().parents[1]
    failures = (
        check_cases()
        + check_kotlin_agrees(android)
        + check_the_watch_is_actually_wired(android)
    )
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(
        f"mic silence: {len(CASES)} sessions, the zero test, and the wake "
        "listener's wiring all agree"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
