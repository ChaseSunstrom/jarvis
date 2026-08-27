#!/usr/bin/env bash
# M42 — delegation across backends. One request becomes a plan whose pieces go
# to different kinds of worker: specialists, the research engine, a coding job.
source "$(dirname "$0")/lib.sh"
verify_begin "M42" "delegation across backends"
use_venv

require_file jarvis-core/jarvis/agents/backends.py
require_file testing/live/scenarios/delegation-across-backends.yaml

check "a plan entry can name a subsystem, not just a specialist" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.agents.backends import BACKEND_AGENT, BACKEND_CODE, BACKEND_RESEARCH, split
assert split("research")[0] == BACKEND_RESEARCH
assert split("code:claude-code") == (BACKEND_CODE, "claude-code")
assert split("researcher")[0] == BACKEND_AGENT, "a specialist was read as a backend"
print("research, code, code:<backend>, and everything else is a specialist")
'
check "the model is told the subsystems exist" python3 -c '
from pathlib import Path
text = Path("jarvis-core/jarvis/integrations/agents/__init__.py").read_text()
assert "research" in text.split("description=(")[1][:400]
print("the tool description names them")
'
check "delegated work waits on TASKS rather than reimplementing them" \
    grep -q 'async def wait_for_task' jarvis-core/jarvis/agents/backends.py
check "an approval that stops a child stops the wait" \
    grep -q 'STATUS_CANCELLED' jarvis-core/jarvis/agents/backends.py
check "concurrency is still the pool's to bound" \
    grep -q 'pool = pool or get_pool(jarvis)' jarvis-core/jarvis/agents/runner.py
check "a fan-out is labelled a fan-out, not what it delegated" \
    grep -q 'the outer act is the answer' testing/live/capability.py

check_pytest "the dispatch, and what it refuses to guess" 'cd jarvis-core && python3 -m pytest tests/test_delegation_backends.py -q \
        --timeout=180 --timeout-method=signal'
check_pytest "the subagent suite still holds" 'cd jarvis-core && python3 -m pytest tests -q -k "agent" \
        --timeout=300 --timeout-method=signal'

check_sh "one request, two backends, one lead" \
    'set -a; . ./.env 2>/dev/null; set +a; \
     timeout 1800 python3 -m testing.live.runner --full --only delegation-across-backends \
       --no-browser --target harness 2>&1 | grep -v onnxruntime | tail -3'
# Counted on the task list, not on the turn: since rule 4 and the tool say a
# two-job request goes through delegate_to_agents, the model makes ONE call and
# the lead task fans out — the research engine and the specialists are its
# children (`parent_id`), which is where "more than one backend" is visible.
check "and the run really did reach more than one backend: the lead task fanned out to children of two kinds" python3 -c '
import json
from pathlib import Path
data = json.loads(Path(".verify/live/results-m42.json").read_text())
matched = [turn.get("task") for s in data["scenarios"] for turn in s["turns"] if turn.get("task")]
assert matched, "no turn matched a task expectation"
lead = matched[0]
kinds = sorted({str(c.get("kind")) for c in lead.get("children") or []})
assert len(kinds) >= 2, "the lead fanned out to one kind of worker: %s (%d children)" % (kinds, len(lead.get("children") or []))
print("lead %r: %d children of kinds %s" % (lead.get("title"), len(lead.get("children") or []), kinds))
'
verify_end
