#!/usr/bin/env bash
# M26 — the intelligence scorecard: context retention, tool routing, multi-step
# reasoning, instruction following, graceful failure and latency, all measured
# through the full voice pipeline rather than against the API.
source "$(dirname "$0")/lib.sh"
verify_begin "M26" "intelligence eval and scorecard"
use_venv

require_dir evals/intelligence
require_file evals/intelligence/prompts.yaml
require_file evals/intelligence/run.py
check "routing is scored against what Jarvis DID, not what it said" \
    grep -q '_capability_of\|routed' testing/live/runner.py
check "every routing class the brief names has prompts" python3 -c '
import sys, yaml
from pathlib import Path
data = yaml.safe_load(Path("evals/intelligence/prompts.yaml").read_text())
want = {"answer", "memory", "notes", "task", "research", "coding"}
have = {row["expect"] for row in data["routing"]}
missing = sorted(want - have)
assert not missing, f"no routing prompt for: {missing}"
print(f"{len(data[chr(114)+chr(111)+chr(117)+chr(116)+chr(105)+chr(110)+chr(103)])} routing prompts")
'
check "latency is measured idle AND under load" grep -q 'under_load\|background_load' evals/intelligence/run.py
check_sh "the scorecard runs and writes its numbers" \
    'set -a; . ./.env 2>/dev/null; set +a; timeout 3600 python3 evals/intelligence/run.py --out .verify/live/scorecard.json 2>&1 | tail -6'
check "the scorecard exists and names every section" python3 -c '
import json
from pathlib import Path
data = json.loads(Path(".verify/live/scorecard.json").read_text())
for key in ("context_retention", "routing", "reasoning", "instructions", "graceful_failure", "latency"):
    assert key in data, f"the scorecard has no {key}"
print(", ".join(sorted(data)))
'
# No live scenarios of its own — this milestone does not add a capability
# anybody talks to. What it must not do is break the ones that exist, so a
# named smoke subset runs: house-light-on, chat-context-retention, lock-needs-a-human.
check_sh "the live smoke scenarios still pass" \
    'LIVE_ONLY=house-light-on,chat-context-retention,lock-needs-a-human bash scripts/verify/live_interaction.sh --implemented-only 2>&1 | tail -4'
verify_end
