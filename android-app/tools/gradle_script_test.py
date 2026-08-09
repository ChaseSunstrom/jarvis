#!/usr/bin/env python3
"""Executable spec for the Gradle Kotlin DSL build scripts.

This container has no Android SDK, so `app/build.gradle.kts` cannot be compiled
here and CI is the only thing that type-checks it. That feedback loop is two
minutes long and the errors it produces are actively misleading, so it is worth
catching the known traps statically.

The trap that motivated this file, in full, because the error message points
nowhere near the cause:

    java.util.zip.ZipFile(archive).use { zip -> ... }

fails with `Unresolved reference: util`. In a Kotlin DSL script the Android and
Java plugins contribute a `java` **extension accessor**, so a leading `java.`
resolves to that extension rather than to the package root. The knock-on is
worse than the error: the call becomes error-typed, so `zip`, `entry` and
everything derived from them do too, and the compiler reports cascade failures
pages away — in our case an "overload resolution ambiguity" on `Int.compareTo`
forty lines down, in code that was entirely correct.

The fix is to `import` the type and call it unqualified.

Run:  python3 android-app/tools/gradle_script_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = sorted(ROOT.rglob("*.gradle.kts"))

# Package roots that are also Gradle extension accessors, or are shadowed often
# enough to be worth refusing outright. `java` is the one that bit us; `kotlin`
# is an accessor wherever a Kotlin plugin is applied.
SHADOWED_ROOTS = ("java", "kotlin")


def _strip_noise(text: str) -> list[tuple[int, str]]:
    """Lines with block comments, line comments and strings removed.

    Crude but sufficient: we only need to avoid flagging prose and string
    literals, and the scripts here have no exotic quoting.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        line = re.sub(r"//.*$", "", line)
        line = re.sub(r'"(?:[^"\\]|\\.)*"', '""', line)
        out.append((n, line))
    return out


def test_scripts_exist() -> None:
    assert SCRIPTS, "no *.gradle.kts found"


def test_no_shadowed_package_roots_in_expressions() -> None:
    """`java.util.zip.ZipFile(...)` must be an import, not an expression."""
    bad = []
    for path in SCRIPTS:
        for n, line in _strip_noise(path.read_text()):
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("package "):
                continue
            for root in SHADOWED_ROOTS:
                # `java.util.` / `kotlin.io.` etc. used as a qualified name.
                if re.search(rf"(?<![\w.]){root}\.[a-z][\w.]*\.[A-Z]", stripped):
                    bad.append(
                        f"{path.relative_to(ROOT)}:{n}: qualified `{root}.` in an "
                        f"expression — `{root}` is a Gradle extension accessor "
                        f"here; import the type instead: {stripped[:70]}"
                    )
    assert not bad, "\n" + "\n".join(bad)


def test_imports_precede_the_plugins_block() -> None:
    """Kotlin requires imports at the top; Gradle requires `plugins {}` first
    among *statements*. Both hold only if the imports come before it."""
    for path in SCRIPTS:
        lines = [line for _n, line in _strip_noise(path.read_text())]
        plugins_at = next(
            (i for i, l in enumerate(lines) if l.strip().startswith("plugins")), None
        )
        if plugins_at is None:
            continue
        for i, line in enumerate(lines):
            if line.strip().startswith("import ") and i > plugins_at:
                rel = path.relative_to(ROOT)
                raise AssertionError(
                    f"{rel}:{i + 1}: import after the plugins block; "
                    "Kotlin requires imports at the top of the file"
                )


def test_zipfile_usage_is_imported_where_used() -> None:
    """If a script constructs a ZipFile, it must have imported it."""
    for path in SCRIPTS:
        text = path.read_text()
        if re.search(r"(?<![\w.])ZipFile\s*\(", text):
            assert "import java.util.zip.ZipFile" in text, (
                f"{path.relative_to(ROOT)} constructs ZipFile without importing it"
            )


def main() -> int:
    tests = [
        (n, f) for n, f in sorted(globals().items())
        if n.startswith("test_") and callable(f)
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
    print(
        f"\n{len(tests) - failures}/{len(tests)} checks passed "
        f"({len(SCRIPTS)} gradle script(s))"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
