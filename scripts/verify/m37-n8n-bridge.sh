#!/usr/bin/env bash
# M37 — the n8n bridge. Everything here is about what it will NOT do: run
# before it is switched on, run something nobody listed, or run anything at
# Tier 1 by accident.
source "$(dirname "$0")/lib.sh"
verify_begin "M37" "n8n bridge, flag-gated and off"
use_venv

require_file jarvis-core/jarvis/integrations/n8n/__init__.py

check "the shipped config has it OFF: no server URL, no allow-list, until the operator names one" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from pathlib import Path
from jarvis.config import load_yaml
cfg = load_yaml(Path("jarvis-core/config/configuration.yaml"), Path("jarvis-core/config"), {})
block = cfg.get("n8n") or {}
# M77 replaced the M37 block (the loader refuses two `n8n:` keys now): the
# bridge is OFF when the URL is empty, which is what the shipped env default
# leaves it. `enabled: false` was the old switch; an empty URL is the new one.
assert not str(block.get("url") or "").strip(), "the shipped config names an n8n server"
assert not block.get("workflows"), "the shipped allow-list is not empty"
print("url empty (off), no workflows")
'
check "no API key is in the tracked config" python3 -c '
from pathlib import Path
text = Path("jarvis-core/config/configuration.yaml").read_text()
block = text.split("\nn8n:", 1)[1].split("\n\n", 1)[0]
for line in block.splitlines():
    stripped = line.strip()
    if stripped.startswith("api_key:") and "!secret" not in stripped and "!env_var" not in stripped:
        raise AssertionError(f"a key is in the tracked config: {stripped}")
    if stripped.startswith("api_key:") and "!env_var" in stripped and not stripped.rstrip().endswith(chr(34) + chr(34)):
        raise AssertionError(f"the env default is not empty: {stripped}")
print("the key comes from the environment or a secret, never from the tracked file")
'
# M77 replaced the M37 bridge: workflows are listed from n8n's own API (a
# read, Tier 1) and started only through their Webhook trigger; anything
# that changes n8n — run, activate, create, update — is held for a yes.
# These checks name M77's tests, since M37's (an allow-list in the config,
# a flag) describe code that no longer exists.
check "running, activating, creating and updating a workflow are held for approval (Tier 3)" python3 -c '
from pathlib import Path
rows = Path("jarvis-core/tests/test_gated_services.py").read_text()
for tool in ("run_workflow", "activate_workflow", "create_workflow", "update_workflow"):
    assert tool in rows, tool + " is not in the Tier-3 table"
print("four n8n tools in the Tier-3 table")
'
check "a workflow without a Webhook trigger refuses to run, and says which node it needs" \
    grep -q 'def test_a_workflow_runs_through_its_webhook_and_one_without_is_refused_with_the_reason' jarvis-core/tests/test_n8n.py
check "unconfigured, it registers nothing and says so" \
    grep -q 'def test_unconfigured_says_so_and_calls_nothing' jarvis-core/tests/test_n8n.py
check "the assistant's words come back fenced, and nothing it says runs" \
    grep -q 'def test_the_assistant_answers_fenced_and_nothing_it_says_runs' jarvis-core/tests/test_n8n.py
check_sh "the n8n suite" \
    'cd jarvis-core && python3 -m pytest tests/test_n8n.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -1'

check "the env var reaches jarvis-core" \
    grep -q 'N8N_URL=' jarvis-core/docker-compose.yml
verify_end
