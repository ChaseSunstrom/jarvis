#!/usr/bin/env python3
"""Token lint — no hard-coded colour, spacing, type or motion value in app code.

The design system's rule, enforced: a colour, size, radius, shadow or duration
is typed once, in ``design/tokens.json``, and reaches app code through the
generated files (``tokens.css``/``tokens.ts``, ``tokens.py``, ``JarvisTokens.kt``).
This scans every surface for a value typed anywhere else.

What counts as a hit:

  web      (jarvis-web/src, jarvis-desktop-app/src — .svelte .css .ts)
           #hex · rgb()/rgba()/hsl() that is not the relative-colour form
           `rgb(from var(--jv-…) …)` · a numeric px/rem/em/ms/s in a spacing,
           sizing, type, radius, shadow or motion property (CSS and <style>).
           `0` and `1px` (a hairline) are the only literals allowed.
  android  (android-app/app/src/main/kotlin — .kt)
           0xAARRGGBB colour ints · Color.parseColor/rgb/argb · dp(ctx, N) ·
           textSize = N · COMPLEX_UNIT_SP, N.
  desktop  (jarvis-desktop/jarvis_desktop — .py)  #hex.

Exempt: generated files (they carry the `@generated from design/tokens.json`
marker), tests, and the files named under "exceptions" in the baseline with a
sentence saying why — today the orb's GLSL shader and renderer (a shader cannot
read a custom property; their palette is drift-checked by design/build.py) and
the QR encoder (SVG fills for a code that must survive being photographed).

The ratchet. ``design/token-lint.baseline.json`` records, per file, how many
hits existed when the rule was introduced. The lint FAILS when a file not in the
baseline has any hit, or a baselined file has more than its baseline — so new
code is always clean and old code can only get cleaner. A milestone that finishes
a surface runs ``--require-clean <path>`` to insist its baseline is zero.

    python3 scripts/verify/token_lint.py                 lint against the baseline
    python3 scripts/verify/token_lint.py --report        every hit, no exit status
    python3 scripts/verify/token_lint.py --require-clean jarvis-web/src
    python3 scripts/verify/token_lint.py --update-baseline   (only when a milestone
                                                          legitimately moves the line)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "design" / "token-lint.baseline.json"
MARK = "@generated from design/tokens.json"

WEB_ROOTS = ("jarvis-web/src", "jarvis-desktop-app/src")
ANDROID_ROOT = "android-app/app/src/main/kotlin"
DESKTOP_ROOT = "jarvis-desktop/jarvis_desktop"
SKIP_DIRS = {"node_modules", "build", ".svelte-kit", "__pycache__", "dist"}

HEX = re.compile(r"(?<![&\w])#[0-9a-fA-F]{3,8}\b")
COLOUR_FN = re.compile(r"\b(rgba?|hsla?)\(\s*([^)]*)")
PROPS = re.compile(
    r"^\s*(?:margin|padding|gap|row-gap|column-gap|inset|top|right|bottom|left|width|height|"
    r"min-width|max-width|min-height|max-height|font-size|letter-spacing|line-height|"
    r"border-radius|box-shadow|text-shadow|transition|transition-duration|transition-delay|"
    r"animation|animation-duration|animation-delay|translate|outline-offset)\s*:\s*([^;{}]*)"
)
RAW_UNIT = re.compile(r"(?<![\w.-])\d*\.?\d+(px|rem|em|ms|s)\b")
KT_COLOUR = re.compile(r"0x[0-9A-Fa-f]{8}\b|Color\.(?:parseColor|rgb|argb)\(")
KT_SPACE = re.compile(r"\bdp\(\s*\w+\s*,\s*\d+(?:\.\d+)?\s*\)")
KT_TYPE = re.compile(r"\btextSize\s*=\s*\d+(?:\.\d+)?f?\b|COMPLEX_UNIT_SP\s*,\s*\d+(?:\.\d+)?f?\b")


def walk(root: Path, suffixes: tuple[str, ...]):
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if SKIP_DIRS & set(path.parts):
            continue
        name = path.name
        if name.endswith((".test.ts", ".d.ts", "_test.py")) or "/tests/" in path.as_posix():
            continue
        yield path


def strip_comments_css(text: str) -> str:
    return re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), text, flags=re.S)


def scan_web(path: Path, text: str) -> list[str]:
    hits = []
    lines = text.split("\n")
    # Colour anywhere; raw units only where CSS is (a .css file, or <style> blocks).
    in_style = path.suffix == ".css"
    for n, raw in enumerate(lines, 1):
        line = re.sub(r"//.*$", "", raw) if path.suffix in (".ts", ".svelte") and not in_style else raw
        if path.suffix == ".svelte":
            if "<style" in raw:
                in_style = True
            if "</style>" in raw:
                in_style = False
        stripped = strip_comments_css(line)
        if HEX.search(stripped):
            hits.append(f"{n}: colour literal: {raw.strip()[:110]}")
        m = COLOUR_FN.search(stripped)
        if m and not m.group(2).strip().startswith("from"):
            hits.append(f"{n}: colour function: {raw.strip()[:110]}")
        if in_style or path.suffix == ".css":
            prop = PROPS.match(stripped)
            if prop:
                value = re.sub(r"var\([^)]*\)", "", prop.group(1))
                value = re.sub(r"\b(0|1px)\b", "", value)
                if RAW_UNIT.search(value):
                    hits.append(f"{n}: raw value: {raw.strip()[:110]}")
    return hits


def scan_kotlin(text: str) -> list[str]:
    hits = []
    for n, raw in enumerate(text.split("\n"), 1):
        line = re.sub(r"//.*$", "", raw)
        if KT_COLOUR.search(line):
            hits.append(f"{n}: colour literal: {raw.strip()[:110]}")
        if KT_SPACE.search(line):
            hits.append(f"{n}: raw dp: {raw.strip()[:110]}")
        if KT_TYPE.search(line):
            hits.append(f"{n}: raw sp: {raw.strip()[:110]}")
    return hits


def scan_python(text: str) -> list[str]:
    hits = []
    for n, raw in enumerate(text.split("\n"), 1):
        line = re.sub(r"#(?![0-9a-fA-F]{3,8}\b).*$", "", raw)  # drop comments, keep '#hex'
        if re.search(r"[\"']#[0-9a-fA-F]{6}[\"']", line):
            hits.append(f"{n}: colour literal: {raw.strip()[:110]}")
    return hits


def scan_all(exceptions: dict[str, str]) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}

    def record(path: Path, hits: list[str]) -> None:
        rel = path.relative_to(ROOT).as_posix()
        if rel in exceptions or not hits:
            return
        results[rel] = hits

    for root in WEB_ROOTS:
        for path in walk(ROOT / root, (".svelte", ".css", ".ts")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if MARK in text:
                continue
            record(path, scan_web(path, text))
    for path in walk(ROOT / ANDROID_ROOT, (".kt",)):
        text = path.read_text(encoding="utf-8", errors="replace")
        if MARK in text:
            continue
        record(path, scan_kotlin(text))
    for path in walk(ROOT / DESKTOP_ROOT, (".py",)):
        text = path.read_text(encoding="utf-8", errors="replace")
        if MARK in text:
            continue
        record(path, scan_python(text))
    return results


def load_baseline() -> dict:
    if BASELINE.is_file():
        return json.loads(BASELINE.read_text(encoding="utf-8"))
    return {"exceptions": {}, "files": {}}


def main(argv: list[str]) -> int:
    baseline = load_baseline()
    exceptions = baseline.get("exceptions", {})
    results = scan_all(exceptions)
    total = sum(len(v) for v in results.values())

    if "--update-baseline" in argv:
        baseline["files"] = {path: len(hits) for path, hits in sorted(results.items())}
        baseline.setdefault("$note", "Per-file hit counts when the rule was introduced. Only ever goes down.")
        BASELINE.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
        print(f"baseline written: {len(results)} files, {total} hits")
        return 0

    if "--report" in argv:
        for path, hits in sorted(results.items()):
            print(f"{path} ({len(hits)})")
            for h in hits[:40]:
                print(f"   {h}")
        print(f"\n{total} hit(s) in {len(results)} file(s)")
        return 0

    failures = []
    allowed = baseline.get("files", {})
    for path, hits in sorted(results.items()):
        cap = allowed.get(path)
        if cap is None:
            failures.append(f"{path}: {len(hits)} hit(s) in a file with no baseline (new code must use tokens)")
            failures.extend(f"   {h}" for h in hits[:20])
        elif len(hits) > cap:
            failures.append(f"{path}: {len(hits)} hit(s), baseline allows {cap} (the ratchet only goes down)")
            failures.extend(f"   {h}" for h in hits[:20])

    if "--require-clean" in argv:
        prefix = argv[argv.index("--require-clean") + 1]
        dirty = {p: len(h) for p, h in results.items() if p.startswith(prefix)}
        if dirty:
            failures.append(f"--require-clean {prefix}: {sum(dirty.values())} hit(s) remain in {len(dirty)} file(s):")
            failures.extend(f"   {p}: {n}" for p, n in sorted(dirty.items()))

    if failures:
        print("\n".join(failures))
        print(f"\ntoken lint FAILED — {total} hit(s) across {len(results)} file(s); run with --report for all")
        return 1
    print(f"token lint ok — {total} legacy hit(s) in {len(results)} baselined file(s), none new; "
          f"{len(exceptions)} documented exception(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
