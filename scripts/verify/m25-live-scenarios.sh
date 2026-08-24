#!/usr/bin/env bash
# M25 — the scenario suite: every capability, through real interaction, with
# the ones that do not exist yet written now and gated on the milestone that
# will build them.
source "$(dirname "$0")/lib.sh"
verify_begin "M25" "full-capability scenario suite"
use_venv
SCEN=testing/live/scenarios

require_dir "$SCEN"
check "the suite parses and every scenario says why it exists" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.scenario import load_all
missing = [s.name for s in load_all() if not s.intent]
assert not missing, f"no `intent:` on: {missing}"
print(f"{len(load_all())} scenarios")
'
for capability in house voice conversation task memory notes research coding subagents interactions safety skills; do
    check "a scenario covers: $capability" python3 -c "
import sys; sys.path.insert(0, '.')
from testing.live.scenario import load_all
assert any(s.capability == '$capability' for s in load_all()), 'none'
"
done
check "the coding scenarios assert containment, not just success" \
    grep -rq 'file:' "$SCEN/coding-fix-failing-tests.yaml"
check "there is a denied-approval scenario, not only an approved one" \
    grep -rq 'decision: deny' "$SCEN"
check "memory is tested across a restart" grep -rq 'restart: true' "$SCEN"
check "the wake word has positives and negatives" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.scenario import load_all
tags = [s for s in load_all() if "wake-word" in s.tags]
assert any("negative" in s.tags for s in tags), "no negative"
assert any("negative" not in s.tags for s in tags), "no positive"
print(f"{len(tags)} wake-word scenarios")
'
check "noise is exercised at a named SNR" grep -rq 'snr_db' "$SCEN"
require_file scripts/verify/live_interaction.sh
check "the runner has both modes" grep -q -- '--implemented-only' scripts/verify/live_interaction.sh
check "every remaining milestone runs the live suite" python3 -c '
import re, sys
from pathlib import Path
done = set()
for line in Path("MILESTONES.md").read_text().splitlines():
    m = re.match(r"- \[x\] \*\*(M[0-9]{2})", line.strip())
    if m:
        done.add(m.group(1))
missing = []
for script in sorted(Path("scripts/verify").glob("m[0-9][0-9]-*.sh")):
    mid = script.name[:3].upper()
    if mid in done or mid in {"M23"}:
        continue
    if "live_interaction.sh" not in script.read_text():
        missing.append(script.name)
assert not missing, f"these do not run the live suite: {missing}"
print("every unfinished milestone gates on the live suite")
'
check_sh "the ungated scenarios pass" 'bash scripts/verify/live_interaction.sh --implemented-only 2>&1 | tail -6'
verify_end
