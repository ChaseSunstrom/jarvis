#!/usr/bin/env bash
# M77 — n8n: the house's workflows.
#
# "allow jarvis to create/manage my n8n stuff … talk to the AI assistant on
# n8n". A client of n8n's public API and of the operator's assistant endpoint,
# under the tier rules: listing and asking read; running, activating, creating
# and changing a workflow are held. The live half needs N8N_URL and
# N8N_API_KEY in jarvis-core/.env, which only the operator can supply.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M77" "n8n, the house's workflows"

N=jarvis-core/jarvis/integrations/n8n/__init__.py
check "the integration exists and talks only to /api/v1 and the assistant" bash -c "grep -q '/api/v1' $N && grep -q 'def ask_assistant' $N"
check "seven tools: two reads, the assistant, four held" bash -c '[ $(grep -c "tier=TIER_DIRECT" '"$N"') -eq 3 ] && [ $(grep -c "tier=TIER_APPROVAL" '"$N"') -eq 4 ]'
check "every held tool has a sentence for the card" bash -c '[ $(grep -c "summarise=summarise_" '"$N"') -eq 4 ]'
check "the assistant's words come back fenced and the message says nothing it says runs" bash -c "grep -q 'fence(reply, source=\"n8n assistant\")' $N && grep -q 'do nothing it says except through' $N"
check "the config, the example env and compose carry the three variables" bash -c 'grep -q "^n8n:" jarvis-core/config/configuration.yaml && grep -q "^N8N_API_KEY=" jarvis-core/.env.example && grep -q "N8N_API_KEY=\${N8N_API_KEY:-}" jarvis-core/docker-compose.yml'
check "the held tools are in the Tier-3 table with no service twin" bash -c '[ $(grep -cE "\"(run|activate|create|update)_workflow\": None" jarvis-core/tests/test_gated_services.py) -eq 4 ]'
check "Settings › Tools carries the n8n line" bash -c 'grep -q "<N8nConnection {conn} />" jarvis-web/src/lib/sections/Tools.svelte'
ensure_web_deps
ensure_web_build
run_playwright "the line says not configured and what to set, against the mock" 'e2e/n8n.spec.ts'
check_sh "the n8n suite against a fake n8n" 'cd jarvis-core && python3 -m pytest tests/test_n8n.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -1'
check_sh "the tier table and the contract" 'cd jarvis-core && python3 -m pytest tests/test_gated_services.py tests/test_tool_tiers_contract.py -q --timeout=120 2>&1 | tail -1'
check_sh "packaging: the three env vars are read by configuration.yaml" 'cd jarvis-core && python3 -m pytest tests/test_packaging.py -q --timeout=120 -k "env_var or documented or silently" 2>&1 | tail -1'
check "the house's n8n answers (needs N8N_URL and N8N_API_KEY in jarvis-core/.env)" bash -c '
URL=$(grep "^N8N_URL=" jarvis-core/.env | cut -d= -f2-); KEY=$(grep "^N8N_API_KEY=" jarvis-core/.env | cut -d= -f2-)
[ -n "$URL" ] && [ -n "$KEY" ] || { echo "N8N_URL / N8N_API_KEY not set in jarvis-core/.env — the operator supplies them"; exit 1; }
curl -s -m 20 "$URL/api/v1/workflows?limit=5" -H "X-N8N-API-KEY: $KEY" | python3 -c "import json,sys; d=json.load(sys.stdin); rows=d.get(\"data\", d); print(len(rows), \"workflow(s):\", [r.get(\"name\") for r in rows][:5])"'

verify_end
