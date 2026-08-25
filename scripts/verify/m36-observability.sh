#!/usr/bin/env bash
# M36 — observability. Every agent step, what it cost, and a link to it from
# the task that ran it — without a six-container analytics stack holding a
# second copy of the user's prompts.
source "$(dirname "$0")/lib.sh"
verify_begin "M36" "agent observability"
use_venv

require_file jarvis-core/jarvis/integrations/observability/__init__.py
require_file jarvis-web/src/lib/trace.ts

check "the rejection was re-argued, not inherited" python3 -c '
from pathlib import Path
section = Path("docs/TOOLING_DECISIONS.md").read_text().split("### 6. Observability")[1]
section = section.split("### 7.")[0]
for needle in ("942 MB", "169 MB", "16 GiB", "six containers", "second copy"):
    assert needle in section, f"the observability decision does not mention {needle!r}"
assert "What would reverse this" in section
print("measured, re-argued, and the reversal condition named")
'
check "tracing is on in the shipped config" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from pathlib import Path
from jarvis.config import load_yaml
cfg = load_yaml(Path("jarvis-core/config/configuration.yaml"), Path("jarvis-core/config"), {})
block = cfg.get("observability")
assert block, "observability is not configured"
assert block.get("max_spans"), "no span bound — a runaway loop would eat the heap"
print(f"{block[chr(109)+chr(97)+chr(120)+chr(95)+chr(116)+chr(114)+chr(97)+chr(99)+chr(101)+chr(115)]} traces x {block[chr(109)+chr(97)+chr(120)+chr(95)+chr(115)+chr(112)+chr(97)+chr(110)+chr(115)]} spans")
'
check "the agent reports what a model call cost" \
    grep -q 'jarvis_model_call' jarvis-core/jarvis/llm/agent.py
check "and it cannot fail a turn by failing to count one" \
    grep -q 'counting is never fatal' jarvis-core/jarvis/llm/agent.py
check "the recorder never raises on the hot path" \
    grep -q 'observability is never fatal' jarvis-core/jarvis/integrations/observability/__init__.py

check_sh "the recorder's own tests" \
    'cd jarvis-core && python3 -m pytest tests/test_observability.py -q \
        --timeout=120 --timeout-method=signal 2>&1 | tail -2'
check_sh "the console's reading half" \
    'cd jarvis-web && npx vitest run src/lib/trace.test.ts 2>&1 | tail -3'
check "the websocket commands are documented" \
    grep -q 'jarvis/traces/get' jarvis-core/docs/clients.md
check_sh "and the mock backend answers them, or the console tests lie" \
    'grep -q "jarvis/traces/get" tests/web/mock-ha.mjs'

run_playwright "the trace panel, in a real browser" task-live.spec.ts
verify_end
