#!/usr/bin/env python3
"""Decode this repo's QR encoder with something that is not this repo.

`src/lib/qr.ts` is ~300 lines of bit twiddling against ISO/IEC 18004. A wrong
generator polynomial, an off-by-one in the block interleave or a transposed
row in the ECC table all produce output that still *looks* exactly like a QR
code — square, three finders, plausible noise — and still fails in a phone
camera. Rendering it proves nothing.

So the encoder is checked by round trip against OpenCV's detector, which
shares no code, no tables and no author with it. Every version and every
error-correction level is encoded at close to its stated capacity, rasterised,
and decoded; the test passes only if the text comes back byte for byte.

    pip install opencv-python-headless numpy
    node --experimental-strip-types tools/qr_fixtures.mjs \
        | python3 tools/qr_roundtrip.py

(The `--experimental-strip-types` is because the fixture generator imports the
TypeScript module directly rather than a built copy, so what is verified is the
source that ships.)

Reads newline-delimited JSON on stdin, one object per case:

    {"text": "...", "size": 33, "modules": [[0,1,...], ...], "label": "v4/M"}

Exit status is 0 only if every case round-trips.
"""

from __future__ import annotations

import json
import sys

import cv2
import numpy as np

#: Pixels per module. Below about 4 the detector starts failing on large
#: versions for reasons that are about the detector, not the encoder.
SCALE = 8
#: Quiet zone in modules. The spec says 4; a decoder given none may still work,
#: which would make this test weaker than a real camera.
QUIET = 4


def rasterise(modules: list[list[int]]) -> np.ndarray:
    """Modules to a white-background 8-bit greyscale image."""
    grid = np.array(modules, dtype=np.uint8)
    side = grid.shape[0] + QUIET * 2
    canvas = np.ones((side, side), dtype=np.uint8)
    canvas[QUIET : QUIET + grid.shape[0], QUIET : QUIET + grid.shape[1]] = 1 - grid
    return np.kron(canvas, np.ones((SCALE, SCALE), dtype=np.uint8)) * 255


def oracle_ceiling(detector: "cv2.QRCodeDetector") -> int:
    """The largest symbol OpenCV's detector can read, measured, not assumed.

    OpenCV's detector gives up well before version 40: it fails on symbols its
    OWN encoder produced, from about 109 modules upward, at any scale tried. So
    a failure above that line says nothing about the encoder under test, and
    asserting on it would produce 131 fake failures and bury the real ones.

    Rather than hardcode a version number that will drift with OpenCV releases,
    ask this OpenCV where its limit is: encode with `cv2.QRCodeEncoder`, decode
    with `cv2.QRCodeDetector`, and take the last size that survives. Cases above
    the line are reported as unchecked — never as passed.
    """
    params = cv2.QRCodeEncoder.Params()
    params.correction_level = cv2.QRCodeEncoder_CORRECT_LEVEL_M
    encoder = cv2.QRCodeEncoder.create(params)
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"

    ceiling = 0
    for length in range(10, 2400, 40):
        text = "".join(alphabet[(i * 31 + (i * i) % 17) % len(alphabet)] for i in range(length))
        image = encoder.encode(text)
        # OpenCV's encoder adds its own quiet zone; re-pad to ours and scale.
        scaled = np.kron(np.pad(image, QUIET, constant_values=255), np.ones((SCALE, SCALE), np.uint8))
        decoded, _points, _straight = detector.detectAndDecode(scaled)
        if decoded != text:
            break
        ceiling = image.shape[0] - 8  # strip OpenCV's 4-module quiet zone
    return ceiling


def main() -> int:
    detector = cv2.QRCodeDetector()
    ceiling = oracle_ceiling(detector)
    print(f"oracle ceiling: OpenCV reads its own symbols up to {ceiling} modules")

    passed = 0
    unchecked = 0
    failures: list[str] = []

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        case = json.loads(line)
        label = case.get("label", "?")

        if case["size"] > ceiling:
            unchecked += 1
            continue

        image = rasterise(case["modules"])
        try:
            decoded, _points, _straight = detector.detectAndDecode(image)
        except cv2.error as err:  # pragma: no cover - detector blew up
            failures.append(f"{label}: OpenCV raised {err}")
            continue

        if not decoded:
            failures.append(f"{label}: not detected ({len(case['text'])} bytes, {case['size']} modules)")
        elif decoded != case["text"]:
            failures.append(
                f"{label}: decoded {len(decoded)} bytes, expected {len(case['text'])}; "
                f"first difference at {_first_difference(decoded, case['text'])}"
            )
        else:
            passed += 1

    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    print(f"{passed} round-tripped, {len(failures)} failed, {unchecked} above the oracle's ceiling")
    if unchecked:
        print(
            f"  ({unchecked} cases were NOT verified here. They are covered by the layout and "
            f"golden-digest tests in src/lib/qr.test.ts, not by a decoder.)"
        )
    return 1 if failures else 0


def _first_difference(a: str, b: str) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


if __name__ == "__main__":
    raise SystemExit(main())
