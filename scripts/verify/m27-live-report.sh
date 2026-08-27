#!/usr/bin/env bash
# M27 — the exploratory pass and the report: unscripted conversations against
# the audit's weak spots, every defect written down and turned into a
# regression scenario, and one document that says how it all actually went.
source "$(dirname "$0")/lib.sh"
verify_begin "M27" "exploratory pass and live test report"
use_venv

require_file ISSUES.md
require_file docs/LIVE_TEST_REPORT.md
check "the report was generated, not typed" grep -q 'testing.live.runner' docs/LIVE_TEST_REPORT.md
check "the report carries the headline numbers" python3 -c '
from pathlib import Path
text = Path("docs/LIVE_TEST_REPORT.md").read_text()
for needed in ("word error rate", "routing accuracy", "median round trip", "Per capability", "Latency"):
    assert needed.lower() in text.lower(), f"the report does not report {needed!r}"
print("headline, per-capability, latency, issues")
'
check "at least ten exploratory conversations were run and recorded" python3 -c '
import json
from pathlib import Path
path = Path(".verify/live/exploratory.json")
assert path.is_file(), "no exploratory run recorded"
data = json.loads(path.read_text())
count = len(data.get("conversations") or [])
assert count >= 10, f"only {count} exploratory conversations"
print(f"{count} conversations, {sum(len(c.get(chr(116)+chr(117)+chr(114)+chr(110)+chr(115)) or []) for c in data[chr(99)+chr(111)+chr(110)+chr(118)+chr(101)+chr(114)+chr(115)+chr(97)+chr(116)+chr(105)+chr(111)+chr(110)+chr(115)])} turns")
'
# A regression is whatever runs again and would go red: a live scenario, an
# exploratory probe (judged on every pass), the runner's own log gate, an
# eval or verify script, the Android lint gate. Naming one that does not
# exist, or naming nothing without saying why nothing can, fails. It does not
# accept a bare test-file path for a defect the live rig could see: those
# are the entries this milestone exists to make talk to a real Jarvis.
check "every issue found has a regression scenario or a reason it cannot" python3 -c '
import re, sys; sys.path.insert(0, ".")
from pathlib import Path
from testing.live.scenario import load_all
from testing.live.exploratory import PROBES
text = Path("ISSUES.md").read_text()
names = {s.name for s in load_all()} | {p.name for p in PROBES} | {"stack-logs-clean"}
def exists(token):
    token = token.strip().lstrip("./").split(" ")[0]
    if token == "gradlew": return Path("android-app/gradlew").is_file()
    path, _, test = token.partition("::")
    if test:  # a pytest node id: the file, and the test by name in it
        return Path(path).is_file() and test.split("[")[0] in Path(path).read_text()
    return Path(token).exists()
missing = []
for block in re.split(r"^## ", text, flags=re.M)[1:]:
    title = block.splitlines()[0].strip()
    paragraphs = re.findall(r"^Regression:.*?(?=\n\s*\n|\Z)", block, flags=re.M | re.S)
    if not paragraphs:
        if "cannot be reproduced" in block or "needs the operator" in block:
            continue
        missing.append(title); continue
    tokens = re.findall(r"`([^`]+)`", " ".join(paragraphs))
    if any(t in names or exists(t) for t in tokens):
        continue
    missing.append(f"{title} (regression names nothing that exists: {tokens[:3]})")
assert not missing, f"issues with no regression: {missing}"
print("every issue names a regression that exists")
'
# An OPEN critical, not a fixed one. A critical defect that was found, fixed and
# written down is the suite working; deleting the entry to keep this green is
# the opposite, so the check reads the status line rather than the severity.
check "no critical issue is still open" python3 -c '
import re, sys
from pathlib import Path
text = Path("ISSUES.md").read_text(encoding="utf-8")
open_criticals = [
    block.splitlines()[0].strip()
    for block in re.split(r"^## ", text, flags=re.M)[1:]
    if re.search(r"^severity:\s*critical", block, re.M)
    and not re.search(r"^status:\s*\*\*fixed\*\*", block, re.M)
]
assert not open_criticals, f"still open: {open_criticals}"
'
# No live scenarios of its own — this milestone does not add a capability
# anybody talks to. What it must not do is break the ones that exist, so a
# named smoke subset runs: house-light-on, chat-context-retention, lock-needs-a-human.
check_sh "the live smoke scenarios still pass" \
    'LIVE_ONLY=house-light-on,chat-context-retention,lock-needs-a-human bash scripts/verify/live_interaction.sh --implemented-only 2>&1 | tail -4'
verify_end
