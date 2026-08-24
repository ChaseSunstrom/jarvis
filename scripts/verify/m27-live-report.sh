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
check "every issue found has a regression scenario or a reason it cannot" python3 -c '
import re, sys; sys.path.insert(0, ".")
from pathlib import Path
from testing.live.scenario import load_all
text = Path("ISSUES.md").read_text()
names = {s.name for s in load_all()}
missing = []
for block in re.split(r"^## ", text, flags=re.M)[1:]:
    title = block.splitlines()[0].strip()
    scenario = re.search(r"Regression: `([a-z0-9-]+)`", block)
    if scenario is None:
        if "cannot be reproduced" in block or "needs the operator" in block:
            continue
        missing.append(title)
    elif scenario.group(1) not in names:
        missing.append(f"{title} (names a scenario that does not exist)")
assert not missing, f"issues with no regression scenario: {missing}"
print("every issue has a regression scenario")
'
check_not "no critical issue is still open" grep -nE '^\s*[-*]?\s*severity:\s*critical' ISSUES.md
# No live scenarios of its own — this milestone does not add a capability
# anybody talks to. What it must not do is break the ones that exist, so a
# named smoke subset runs: house-light-on, chat-context-retention, lock-needs-a-human.
check_sh "the live smoke scenarios still pass" \
    'LIVE_ONLY=house-light-on,chat-context-retention,lock-needs-a-human bash scripts/verify/live_interaction.sh --implemented-only 2>&1 | tail -4'
verify_end
