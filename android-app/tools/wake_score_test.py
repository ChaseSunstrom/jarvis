#!/usr/bin/env python3
"""Executable spec for turning wake-word scores into one detection.

`OnDeviceWakeWord` runs openWakeWord's three ONNX models on the phone and emits
a probability roughly every 80 ms. `WakeScore` turns that stream into an edge —
"they said it", once — and it is the half of on-device detection that can be
proved without a device, which is why it is a separate class.

Three ideas, each with its own failure mode, and the point of pinning them
separately is that a single tuned constant hides all three:

  * **threshold** — too low and the television triggers it, too high and you
    say the name twice;
  * **consecutive frames** — one frame over the line is usually a transient (a
    door, a hard consonant), and requiring two costs 80 ms and removes most of
    them;
  * **refractory period** — the score stays high while the phrase is still in
    the model's window, so without this one "hey Jarvis" is five detections.

The subtle one is what happens to the run counter DURING the refractory period.
It keeps counting rather than resetting, so a genuinely new utterance arriving
the instant the period ends fires immediately; resetting there would swallow
the first phrase after every detection, which is the kind of bug that presents
as "it ignores me when I ask twice".

Run:  python3 android-app/tools/wake_score_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

THRESHOLD = 0.5
FRAMES = 2
REFRACTORY_MS = 2000

#: openWakeWord emits a score about every 80 ms.
FRAME_MS = 80


class WakeScore:
    """Mirrors WakeScore.kt."""

    def __init__(self, threshold=THRESHOLD, frames=FRAMES, refractory_ms=REFRACTORY_MS):
        self.threshold = threshold
        self.frames = frames
        self.refractory_ms = refractory_ms
        self.above = 0
        self.last_fired_at = 0
        self.peak = 0.0

    def on_score(self, now_ms: int, score: float) -> bool:
        if score > self.peak:
            self.peak = score
        if score < self.threshold:
            self.above = 0
            return False
        self.above += 1
        if self.above < self.frames:
            return False
        # The backwards-clock guard first: a negative delta is less than any
        # refractory period, so checking that first would lock the detector out
        # until the old timestamp came round again.
        if now_ms < self.last_fired_at:
            self.last_fired_at = now_ms
            return False
        if self.last_fired_at != 0 and now_ms - self.last_fired_at < self.refractory_ms:
            return False
        self.last_fired_at = now_ms
        self.above = 0
        return True

    def reset(self) -> None:
        self.above = 0
        self.last_fired_at = 0
        self.peak = 0.0


def run(frames: list[tuple[int, float]]) -> int:
    watch = WakeScore()
    return sum(1 for now, score in frames if watch.on_score(now, score))


def steady(start: int, count: int, score: float) -> list[tuple[int, float]]:
    return [(start + i * FRAME_MS, score) for i in range(count)]


CASES: list[tuple[str, list[tuple[int, float]], int]] = [
    ("silence never fires", steady(0, 200, 0.01), 0),
    (
        "a whole conversation below the line never fires",
        steady(0, 500, 0.49),
        0,
    ),
    (
        "one loud frame is a transient, not a wake word",
        steady(0, 50, 0.02) + [(4000, 0.99)] + steady(4080, 50, 0.02),
        1 - 1,  # zero: a single frame cannot complete a run of two
    ),
    (
        "two consecutive frames over the line is the wake word",
        steady(0, 10, 0.02) + [(800, 0.9), (880, 0.9)] + steady(960, 10, 0.02),
        1,
    ),
    (
        "two loud frames SEPARATED by a quiet one are not a run",
        [(0, 0.9), (80, 0.1), (160, 0.9)],
        0,
    ),
    (
        "one utterance held over the model's window is still one detection",
        # ~1.2 s, which is how long "hey Jarvis" stays inside the 16-embedding
        # window. Sustained speech for longer than the refractory period is a
        # different thing and is expected to fire again.
        steady(0, 15, 0.95),
        1,
    ),
    (
        "saying it again after the refractory period fires again",
        steady(0, 4, 0.95) + steady(3000, 4, 0.95),
        2,
    ),
    (
        "saying it again DURING the refractory period does not",
        steady(0, 4, 0.95) + steady(500, 4, 0.95),
        1,
    ),
    (
        "a run that spans the end of the refractory period fires at the end "
        "of it, rather than being swallowed",
        steady(0, 2, 0.95) + steady(160, 40, 0.95),
        2,
    ),
    (
        "a clock that went backwards does not lock the detector out",
        steady(10_000, 4, 0.95) + steady(100, 40, 0.95),
        2,
    ),
    (
        "exactly at the threshold counts as over it",
        [(0, THRESHOLD), (80, THRESHOLD)],
        1,
    ),
    (
        "just under the threshold does not",
        [(0, THRESHOLD - 0.001), (80, THRESHOLD - 0.001)],
        0,
    ),
]


def check_cases() -> int:
    failures = 0
    for name, frames, expected in CASES:
        got = run(frames)
        if got != expected:
            print(f"FAIL  {name}: expected {expected} detection(s), got {got}")
            failures += 1
    return failures


def check_peak_is_recorded() -> int:
    watch = WakeScore()
    for now, score in steady(0, 10, 0.3) + [(800, 0.42)]:
        watch.on_score(now, score)
    if abs(watch.peak - 0.42) > 1e-6:
        print(f"FAIL  the peak score is not recorded ({watch.peak})")
        return 1
    watch.reset()
    if watch.peak != 0.0:
        print("FAIL  reset does not clear the peak")
        return 1
    return 0


def check_kotlin_agrees(android: Path) -> int:
    path = android / "app/src/main/kotlin/ai/jarvis/app/assist/WakeScore.kt"
    if not path.is_file():
        print(f"FAIL  {path} is missing")
        return 1
    src = path.read_text(encoding="utf-8")
    failures = 0
    for const, value in (
        ("DEFAULT_THRESHOLD", "0.5f"),
        ("DEFAULT_FRAMES", "2"),
        ("DEFAULT_REFRACTORY_MS", "2_000L"),
    ):
        if f"const val {const} = {value}" not in src:
            print(f"FAIL  WakeScore.{const} is no longer {value}")
            failures += 1
    # The subtle one, spelled out: resetting the run counter inside the
    # refractory branch would swallow the next utterance.
    branch = re.search(
        r"if \(lastFiredAt != 0L && nowMs - lastFiredAt < refractoryMs\) return false", src
    )
    if not branch:
        print("FAIL  the refractory check is gone or has changed shape")
        failures += 1
    return failures


def check_the_detector_fails_safe(android: Path) -> int:
    """On-device detection must never be the reason the wake word stops.

    It depends on weights the user downloads, on an ONNX Runtime build for this
    ABI, and on model shapes this repository cannot test. Every one of those can
    be absent, and the answer to all of them is "use the server", which is the
    path that has always worked.
    """
    root = android / "app/src/main/kotlin/ai/jarvis/app/assist"
    failures = 0
    detector = (root / "OnDeviceWakeWord.kt").read_text(encoding="utf-8")
    if "fun open(" not in detector or "): OnDeviceWakeWord? {" not in detector:
        print("FAIL  OnDeviceWakeWord.open cannot report that it is unavailable")
        failures += 1
    # Every inference stage catches: a wrong tensor shape must degrade, not crash
    # a foreground service holding the microphone.
    for stage in ("runMels", "runEmbedding", "runWakeWord"):
        body = re.search(rf"private fun {stage}\(.*?\n    \}}\n", detector, re.S)
        if not body or "catch (t: Throwable)" not in body.group(0):
            print(f"FAIL  {stage} can throw out of the capture thread")
            failures += 1

    service = (root / "WakeWordService.kt").read_text(encoding="utf-8")
    local = re.search(r"private fun openLocalListener\(\).*?\n    \}", service, re.S)
    if not local:
        print("FAIL  the service never tries on-device detection")
        return failures + 1
    body = local.group(0)
    if "config.wakeWordOnDevice" not in body:
        print("FAIL  on-device detection is not behind its own setting")
        failures += 1
    if "?: return false" not in body:
        print("FAIL  a detector that will not load does not fall back to the server")
        failures += 1
    # And the fallback has to be the DEFAULT: the local path returns early, so
    # the server path must be what runs when it does not.
    if "if (openLocalListener()) return" not in service:
        print("FAIL  the server path is not what happens when local is unavailable")
        failures += 1
    return failures


def check_no_model_is_shipped_in_the_apk(android: Path) -> int:
    """Asked for directly, and it is also the privacy design.

    The weights come from the user's own jarvis-core, which mirrors them. A
    model checked into the app would make every install carry megabytes for a
    feature most never enable; a model fetched from GitHub by the PHONE would
    tell a third party that this device is setting up a voice assistant, and
    would be the one place the app talks to something other than its server.
    """
    failures = 0
    for pattern in ("**/*.onnx", "**/*.tflite"):
        found = [p for p in (android / "app/src").glob(pattern)]
        if found:
            print(f"FAIL  model weights are in the APK: {[str(p) for p in found]}")
            failures += 1

    store = (android / "app/src/main/kotlin/ai/jarvis/app/assist/ModelStore.kt").read_text(
        encoding="utf-8"
    )
    code = re.sub(r"/\*.*?\*/", " ", store, flags=re.S)
    code = re.sub(r"//[^\n]*", " ", code)
    for host in ("github.com", "huggingface.co", "raw.githubusercontent"):
        if host in code:
            print(f"FAIL  the phone downloads from {host} rather than from its own server")
            failures += 1
    if "/api/models/" not in code:
        print("FAIL  ModelStore does not fetch from the server's mirror")
        failures += 1
    if "Authorization" not in code or "Bearer" not in code:
        print("FAIL  the model download is unauthenticated")
        failures += 1
    if "followRedirects(false)" not in code:
        print("FAIL  a redirect could move the download, and the token, to another host")
        failures += 1
    if "MessageDigest" not in code:
        print(
            "FAIL  the downloaded bytes are not verified. A truncated model does "
            "not fail loudly — it fails as a wake word that never fires."
        )
        failures += 1
    return failures


def main() -> int:
    android = Path(__file__).resolve().parents[1]
    failures = (
        check_cases()
        + check_peak_is_recorded()
        + check_kotlin_agrees(android)
        + check_the_detector_fails_safe(android)
        + check_no_model_is_shipped_in_the_apk(android)
    )
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(
        f"wake score: {len(CASES)} score streams, the Kotlin constants, the "
        "fail-safe fallback, and the models coming from the user's own server"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
