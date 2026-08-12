#!/usr/bin/env python3
"""Executable spec: the numbers in docs/verification.md are the real numbers.

`docs/verification.md` is the document that says what this project has actually
proved and what it merely believes. Its android rows carry counts —
"`runtime_permissions_test.py` (22 checks, 17 permissions)" — and those counts
were typed by hand when the row was written and never again.

One of them said 22 while the file reported 25. That is small and it is exactly
the wrong kind of small: the document's entire job is to be trusted about how
much evidence exists, and a reader who checks one number, finds it wrong, and
stops believing the "Unproven" column has lost the most valuable thing in the
repository. A stale count is not a typo here — it is the document failing at
the one thing it is for.

So the counts are checked. Every android row that names a `*_test.py` and gives
a check count is run, and the number it prints has to be the number the document
claims.

This deliberately does not check the prose, and it does not check that every
spec has a row — a row is an editorial decision about what is worth telling a
reader, and several files are covered by one "the remaining `tools/*.py`" row on
purpose.

Run:  python3 android-app/tools/verification_doc_test.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
TOOLS = ANDROID / "tools"
DOC = ANDROID.parent / "docs" / "verification.md"

#: A row's claim: `` `name_test.py` `` … `(N checks` or `— N checks`.
#:
#: Both spellings are in the document already and both are natural English, so
#: this reads both rather than making the document uniform for a regex's sake.
CLAIM = re.compile(
    r"`(\w+_test)\.py`[^|\n]*?[(—-]\s*(\d+)\s+checks",
)


def claims() -> dict[str, int]:
    text = DOC.read_text(encoding="utf-8")
    found: dict[str, int] = {}
    for name, count in CLAIM.findall(text):
        # First claim wins; a second mention of the same file in prose is not a
        # second promise about a different number.
        found.setdefault(name, int(count))
    return found


def reported(name: str) -> int | None:
    """What the spec itself prints, or None if it prints no total."""
    path = TOOLS / f"{name}.py"
    if not path.is_file():
        return None
    out = subprocess.run(
        [sys.executable, str(path)], capture_output=True, text=True, timeout=300
    ).stdout
    match = re.search(r"(\d+)/(\d+) checks passed", out)
    return int(match.group(2)) if match else None


def test_the_document_names_files_that_exist() -> None:
    missing = sorted(n for n in claims() if not (TOOLS / f"{n}.py").is_file())
    assert not missing, (
        f"docs/verification.md cites specs that are not there: {missing}"
    )


def test_every_claimed_check_count_is_the_real_one() -> None:
    wrong: list[str] = []
    for name, claimed in sorted(claims().items()):
        actual = reported(name)
        if actual is None:
            # A spec that prints no total cannot be checked, and a row claiming
            # a count for one is claiming something nobody can read back.
            wrong.append(f"{name}: doc says {claimed}, the file prints no total")
        elif actual != claimed:
            wrong.append(f"{name}: doc says {claimed}, the file reports {actual}")
    assert not wrong, (
        "docs/verification.md is the document that says what this project has "
        "proved. Its counts have drifted, which is the one failure it cannot "
        "afford: " + "; ".join(wrong)
    )


def test_at_least_the_headline_specs_are_cited() -> None:
    """Not every spec needs a row — several are covered by one line on purpose.
    But a reader looking up the things this document makes the loudest claims
    about has to find them."""
    text = DOC.read_text(encoding="utf-8")
    for name in (
        "no_empty_seams_test",
        "runtime_permissions_test",
        "dispatch_spec_test",
        "policy_truth_table_test",
    ):
        assert f"`{name}.py`" in text, f"{name}.py has no row"


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
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed ({len(claims())} counts cited)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
