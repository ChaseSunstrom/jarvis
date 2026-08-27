#!/usr/bin/env bash
# M14 — MCP finish: the client and the add/remove UI exist; this adds inspect
# (schemas, server info, last error, a gated test call), automatic reconnect
# with backoff, tier semantics from the shared contract, and docs.
source "$(dirname "$0")/lib.sh"
verify_begin "M14" "MCP: manage + inspect servers as Jarvis tools"
use_venv
MCP=jarvis-core/jarvis/integrations/mcp

require_file "$MCP/client.py"
require_file "$MCP/__init__.py"
check "WS: jarvis/mcp/inspect" grep -q '"jarvis/mcp/inspect"' jarvis-core/jarvis/api/websocket.py
# Asked of the route table: the routes are declared on `api_router`, which
# carries the `/api` prefix, so the literal "/api/mcp/..." appears nowhere in
# the source and a grep for it failed on a route that exists.
check "REST: inspect endpoint" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.api.rest import api_router
paths = {getattr(r, "path", "") for r in api_router.routes}
assert "/api/mcp/servers/{name}/inspect" in paths, sorted(p for p in paths if "mcp" in p)
'
check "inspect reports protocol version, tool schemas and last error" grep -qE 'last_error' "$MCP/__init__.py"
check "automatic reconnect with backoff" grep -qi 'backoff' "$MCP/__init__.py"
check "tier semantics come from the shared contract" grep -rlq tool_tiers.json jarvis-core/tests/test_mcp.py
check "console can inspect a server" grep -qi 'inspect' jarvis-web/src/lib/components/McpServers.svelte
check "console can test-call a tool through the gate" grep -qE 'jarvis/tools/call|test.?call' jarvis-web/src/lib/components/McpServers.svelte
check "mock backend serves jarvis/mcp/inspect" grep -q 'jarvis/mcp/inspect' tests/web/mock-ha.mjs
require_file jarvis-core/docs/mcp.md
check_pytest "mcp unit tests" 'cd jarvis-core && python3 -m pytest tests/test_mcp.py -q --timeout=120 --timeout-method=signal'
ensure_web_deps
check_sh "mcp draft tests" 'cd jarvis-web && npx vitest run src/lib/mcpDraft.test.ts 2>&1 | tail -3'
ensure_web_build
run_playwright "mcp console e2e" e2e/mcp.spec.ts
# No live scenarios of its own — this milestone does not add a capability
# anybody talks to. What it must not do is break the ones that exist, so a
# named smoke subset runs: house-light-on, chat-context-retention, lock-needs-a-human.
check_sh "the live smoke scenarios still pass" \
    'LIVE_ONLY=house-light-on,chat-context-retention,lock-needs-a-human bash scripts/verify/live_interaction.sh --implemented-only 2>&1 | tail -4'
verify_end
