#!/usr/bin/env bash
# M15 — memory: the existing store gains automatic extraction, export, wipe, a
# "why" trace and a console UI; a scripted eval proves store → restart →
# retrieve → forget → export → wipe end to end.
source "$(dirname "$0")/lib.sh"
verify_begin "M15" "memory: durable, transparent, user-owned"
use_venv
M=jarvis-core/jarvis/integrations/memory/__init__.py

require_file "$M"
check "automatic extraction of durable facts after a turn" grep -qiE 'def .*extract' "$M"
check "extracted entries are marked as such" grep -q '"extracted"' "$M"
check "export: everything in one file" grep -qE 'def .*export' "$M"
check "wipe: everything, including the vector sidecar" grep -qE 'def .*wipe' "$M"
# Asked of the route table: the routes are declared on `api_router`, which
# carries the `/api` prefix, so the literal string is nowhere in the source.
check "REST: /api/memory/export" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.api.rest import api_router
paths = {getattr(r, "path", "") for r in api_router.routes}
assert "/api/memory/export" in paths, sorted(p for p in paths if "memory" in p)
'
check "WS: jarvis/memory/list" grep -q '"jarvis/memory/list"' jarvis-core/jarvis/api/websocket.py
check "the turn records which memory entries it used (memory_used)" grep -q 'memory_used' jarvis-core/jarvis/llm/agent.py
check_not "research no longer writes reports into memory (notes own that)" grep -n '_remember' jarvis-core/jarvis/integrations/research/__init__.py
require_file jarvis-web/src/routes/memory/+page.svelte
check "console memory route can export and wipe" grep -qE 'export' jarvis-web/src/routes/memory/+page.svelte
check "mock backend serves jarvis/memory/*" grep -q 'jarvis/memory/' tests/web/mock-ha.mjs
require_file jarvis-core/tests/test_memory.py
check_sh "memory unit tests" 'cd jarvis-core && python3 -m pytest tests/test_memory.py tests/test_memory_vectors.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
require_file evals/memory_eval.py
check_sh "scripted eval: store → restart → retrieve → forget → export → wipe (exit code)" \
    'timeout 900 python3 evals/memory_eval.py --out .verify/memory 2>&1 | tail -6'
require_file jarvis-web/e2e/memory.spec.ts
ensure_web_deps
ensure_web_build
run_playwright "memory UI e2e" e2e/memory.spec.ts
# This milestone's own live scenarios. A capability is not done until it works
# when a person talks to it — which is a different claim from "its unit tests
# pass", and the only one an operator can feel. Its scenarios are gated on this
# milestone, so they run here for the first time.
check_sh "the live scenarios for memory" \
    'LIVE_CAPABILITY=memory bash scripts/verify/live_interaction.sh --full 2>&1 | tail -6'
verify_end
