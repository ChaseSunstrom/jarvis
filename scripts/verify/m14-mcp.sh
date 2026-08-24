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
check "REST: inspect endpoint" grep -qE '/api/mcp/servers/\{name\}/inspect|/api/mcp/inspect' jarvis-core/jarvis/api/rest.py
check "inspect reports protocol version, tool schemas and last error" grep -qE 'last_error' "$MCP/__init__.py"
check "automatic reconnect with backoff" grep -qi 'backoff' "$MCP/__init__.py"
check "tier semantics come from the shared contract" grep -rlq tool_tiers.json jarvis-core/tests/test_mcp.py
check "console can inspect a server" grep -qi 'inspect' jarvis-web/src/lib/components/McpServers.svelte
check "console can test-call a tool through the gate" grep -qE 'jarvis/tools/call|test.?call' jarvis-web/src/lib/components/McpServers.svelte
check "mock backend serves jarvis/mcp/inspect" grep -q 'jarvis/mcp/inspect' tests/web/mock-ha.mjs
require_file jarvis-core/docs/mcp.md
check_sh "mcp unit tests" 'cd jarvis-core && python3 -m pytest tests/test_mcp.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
ensure_web_deps
check_sh "mcp draft tests" 'cd jarvis-web && npx vitest run src/lib/mcpDraft.test.ts 2>&1 | tail -3'
ensure_web_build
run_playwright "mcp console e2e" e2e/mcp.spec.ts
verify_end
