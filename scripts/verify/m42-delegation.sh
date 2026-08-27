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
check "and the run really did reach more than one backend" python3 -c '
import json
from pathlib import Path
data = json.loads(Path(".verify/live/results.json").read_text())
tools = [t for s in data["scenarios"] for turn in s["turns"] for t in turn.get("tools") or []]
backends = {t for t in tools if t in ("deep_research", "delegate_to_agents", "start_coding_job")}
assert len(backends) >= 2, f"only one backend was used: {sorted(backends)}"
print(f"reached: {sorted(backends)}")
'
verify_end
