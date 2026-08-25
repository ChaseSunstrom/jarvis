#!/usr/bin/env bash
# M37 — the n8n bridge. Everything here is about what it will NOT do: run
# before it is switched on, run something nobody listed, or run anything at
# Tier 1 by accident.
source "$(dirname "$0")/lib.sh"
verify_begin "M37" "n8n bridge, flag-gated and off"
use_venv

require_file jarvis-core/jarvis/integrations/n8n/__init__.py

check "the shipped config has it OFF" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from pathlib import Path
from jarvis.config import load_yaml
cfg = load_yaml(Path("jarvis-core/config/configuration.yaml"), Path("jarvis-core/config"), {})
block = cfg.get("n8n") or {}
assert block.get("enabled") is False, "the n8n bridge ships enabled"
assert not block.get("workflows"), "the shipped allow-list is not empty"
print("enabled: false, allow-list empty")
'
check "no API key is in the tracked config" python3 -c '
from pathlib import Path
text = Path("jarvis-core/config/configuration.yaml").read_text()
block = text.split("n8n:")[1].split("# ---")[0]
for line in block.splitlines():
    stripped = line.strip()
    if stripped.startswith("api_key:") and "!secret" not in stripped:
        raise AssertionError(f"a key is in the tracked config: {stripped}")
print("the key is a !secret, commented, and not here")
'
check "a workflow is Tier 3 unless the operator lowers it" \
    grep -q 'tier: int = 3' jarvis-core/jarvis/integrations/n8n/__init__.py
check "the allow-list is not a discovery" \
    grep -q 'ALLOW-LIST, not a discovery' jarvis-core/config/configuration.yaml
check_not "nothing lists every workflow on the instance" \
    grep -q '/api/v1/workflows' jarvis-core/jarvis/integrations/n8n/__init__.py

check_sh "off, un-listed and no-webhook are all refused, and one worked example runs" \
    'cd jarvis-core && python3 -m pytest tests/test_n8n.py -q \
        --timeout=120 --timeout-method=signal 2>&1 | tail -2'

# The three the milestone names, each asserted by name so a rename cannot
# quietly drop one.
check "the flag being off is covered by name" \
    grep -q 'def test_a_workflow_cannot_run_while_the_bridge_is_off' jarvis-core/tests/test_n8n.py
check "so is the allow-list refusing" \
    grep -q 'def test_something_nobody_listed_is_refused' jarvis-core/tests/test_n8n.py
check "so is the worked example" \
    grep -q 'def test_the_worked_example_end_to_end' jarvis-core/tests/test_n8n.py

check "the env var reaches jarvis-core" \
    grep -q 'N8N_URL=' jarvis-core/docker-compose.yml
verify_end
