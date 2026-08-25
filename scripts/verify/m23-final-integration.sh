#!/usr/bin/env bash
# M23 — final integration: every milestone ticked, no blockers, the ledger and
# the claims register current, CI runs the harness, no placeholders anywhere.
source "$(dirname "$0")/lib.sh"
verify_begin "M23" "final integration"

check_sh "every milestone M00–M22 is ticked in MILESTONES.md" '
    rc=0
    for n in $(seq -w 0 22); do
        grep -qE "^- \[x\] \*\*M$n" MILESTONES.md || { echo "M$n is not ticked"; rc=1; }
    done
    exit $rc'
check_not "BLOCKERS.md has no open entries" grep -nE '^## M[0-9]{2}' BLOCKERS.md
check_sh "CHANGELOG.md names every milestone" '
    rc=0
    for n in $(seq -w 0 23); do grep -q "M$n" CHANGELOG.md || { echo "M$n missing from CHANGELOG.md"; rc=1; }; done
    exit $rc'
check "README documents make verify-all" grep -q 'make verify-all' README.md
check "docs/verification.md names the harness" grep -q 'make verify-all' docs/verification.md
for word in styleguide dashboards Robolectric jarvis-desktop-app; do
    check "docs/verification.md covers: $word" grep -qi "$word" docs/verification.md
done
check_sh "a CI workflow runs make verify-all" 'grep -lE "make verify-all" .github/workflows/*.yml >/dev/null'
# Case-SENSITIVE and word-bounded, and it took three false alarms to get
# there. `-i` on `todo` matched `toDouble` and `states("todo")` — `todo` is
# a real Home Assistant entity domain — and `not implemented` matched
# `ActionResult.unsupported("$actionId is not implemented")`, which is a
# service reporting honestly rather than a stub. A check that cries wolf is
# one somebody learns to skip, which is worse than not having it.
check "no placeholder markers in any surface's sources" python3 -c '
import re, sys
from pathlib import Path

ROOTS = (
    "jarvis-web/src", "jarvis-core/jarvis", "jarvis-desktop/jarvis_desktop",
    "android-app/app/src/main",
)
SUFFIXES = (".svelte", ".ts", ".css", ".py", ".kt")
#: Uppercase because that is the convention for a marker a person left for
#: themselves; lowercase `todo` is a word this codebase uses about laundry.
MARKERS = re.compile(r"\bTODO\b|\bFIXME\b|\bXXX\b|\bHACK\b|coming soon", re.I if False else 0)
SOON = re.compile(r"coming soon|placeholder implementation", re.I)
found = []
for root in ROOTS:
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if MARKERS.search(line) or SOON.search(line):
                found.append(f"{path}:{n}: {line.strip()[:90]}")
assert not found, "placeholder markers:\n  " + "\n  ".join(found[:10])
print("no TODO/FIXME/XXX/HACK/coming-soon in any shipping source")
'
check_not "no mutation-stub markers anywhere (CI's static job, mirrored)" \
    grep -rnIiE '\bM[U]TANT\b|\bDELIBERATELY BR[O]KEN\b' --include='*.py' --include='*.kt' --include='*.kts' \
    --include='*.ts' --include='*.js' --include='*.svelte' --include='*.sh' --include='*.yml' --include='*.yaml' \
    --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=build --exclude-dir=.svelte-kit \
    --exclude-dir=__pycache__ --exclude-dir=.venv --exclude=ci.yml .
verify_end
