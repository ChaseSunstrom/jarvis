#!/usr/bin/env bash
# M23 — final integration: every milestone ticked, no blockers, the ledger and
# the claims register current, CI runs the harness, no placeholders anywhere.
source "$(dirname "$0")/lib.sh"
verify_begin "M23" "final integration"

# Every milestone, not the first twenty-three: the ledger grew to fifty-two
# while this read `seq 0 22`, and a final-integration check that passes with
# a milestone unticked is a check that has stopped describing its milestone.
# M23 itself is exempt — it is ticked in the same commit this goes green in.
check "every other milestone in MILESTONES.md is ticked" python3 -c '
import re
from pathlib import Path
text = Path("MILESTONES.md").read_text(encoding="utf-8")
open_ = [m for m in re.findall(r"^- \[ \] \*\*(M\d{2,3})\b", text, re.M) if m != "M23"]
assert not open_, f"unticked: {open_}"
done = re.findall(r"^- \[x\] \*\*(M\d{2})", text, re.M)
print(f"{len(done)} ticked, none open but M23")
'
check "the live suite has run in FULL mode, and the report says so" python3 -c '
from pathlib import Path
report = Path("docs/LIVE_TEST_REPORT.md")
assert report.is_file(), "no docs/LIVE_TEST_REPORT.md"
text = report.read_text(encoding="utf-8")
assert "--full" in text, "the report was written from an --implemented-only run, not a full one"
print("full-mode report present")
'
# Every open blocker names what it waits on. The earlier form grepped for
# `^## M[0-9]{2}` — a heading shape BLOCKERS.md never had — so it could not
# fail, and passed over five open entries (the quality audit, 27 Aug 2026).
# Open means the heading is not struck through; each such section must carry
# a `**Needed by:**` line, which is where the operator reads what it blocks.
check "every open entry in BLOCKERS.md says what it waits on" python3 -c '
import re
from pathlib import Path
text = Path("BLOCKERS.md").read_text()
sections = re.split(r"^## ", text, flags=re.M)[1:]
open_ones = [s for s in sections if "~~" not in s.splitlines()[0]]
missing = [s.splitlines()[0] for s in open_ones if "**Needed by:**" not in s]
assert not missing, "open blockers with no Needed-by line: " + "; ".join(missing)
print(f"{len(open_ones)} open, each naming what it needs; {len(sections) - len(open_ones)} resolved")
'

check "CHANGELOG.md names every milestone" python3 -c '
import re
from pathlib import Path
ids = sorted(set(re.findall(r"^- \[[ x]\] \*\*(M\d{2})", Path("MILESTONES.md").read_text(encoding="utf-8"), re.M)))
log = Path("CHANGELOG.md").read_text(encoding="utf-8")
missing = [i for i in ids if i not in log]
assert not missing, f"missing from CHANGELOG.md: {missing}"
print(f"all {len(ids)} milestones named")
'
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
