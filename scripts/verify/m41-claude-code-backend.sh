#!/usr/bin/env bash
# M41 — Claude Code as an execution backend. Off, contained, and the same gate.
# This is the one deliberate exception to "nothing goes to the cloud", so most
# of this script is about it refusing to run.
source "$(dirname "$0")/lib.sh"
verify_begin "M41" "Claude Code as an execution backend"
use_venv

require_file jarvis-core/jarvis/integrations/code/claude_backend.py
require_file testing/fixtures/fake_claude_code.py
require_file testing/fixtures/claude_backend_probe.py

check "the shipped config uses the local agent" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from pathlib import Path
from jarvis.config import load_yaml
cfg = load_yaml(Path("jarvis-core/config/configuration.yaml"), Path("jarvis-core/config"), {})
code = cfg.get("code") or {}
assert code.get("backend", "local") == "local", "the delegated backend ships as the default"
assert not (code.get("claude_code") or {}).get("enabled"), "the delegated backend ships enabled"
print("backend: local, claude_code: off")
'
check "a typo in the backend name cannot select the cloud one" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.code import CodeConfig
assert CodeConfig.from_config({"backend": "clude-code"}).backend == "local"
assert CodeConfig.from_config({"backend": "claude-code"}).backend == "claude-code"
print("an unknown backend is local")
'
check "it is written down as a blocker, with the reason" python3 -c '
from pathlib import Path
text = Path("BLOCKERS.md").read_text()
assert "Anthropic API key" in text
assert "exception" in text.lower() and "cloud" in text.lower()
print("BLOCKERS.md carries the row and says what it costs")
'
check "the threat model has not quietly changed" \
    grep -q 'NOT the operator' docs/THREAT_MODEL.md

check_pytest "the backend's own suite: off, keyless, unsandboxed, and the protocol" 'cd jarvis-core && python3 -m pytest tests/test_claude_backend.py -q \
        --timeout=120 --timeout-method=signal'

# The containment claim, against a real container and a stand-in that speaks
# the same protocol. There is no key on this host and there should not be one.
check_sh "a delegated job runs inside the sandbox, and nowhere else" \
    'timeout 900 python3 testing/fixtures/claude_backend_probe.py 2>&1 | tail -7'

check "CI proves it against the stand-in rather than a key" python3 -c '
from pathlib import Path
probe = Path("testing/fixtures/claude_backend_probe.py").read_text()
assert "fake_claude_code.py" in probe or "STAND_IN" in probe
assert "sk-ant" not in probe, "a real key shape appears in a test fixture"
print("the stand-in speaks --print --output-format json; no key anywhere")
'
check_not "no real Anthropic endpoint is contacted by any test" \
    grep -rq 'api.anthropic.com' testing/ jarvis-core/tests/
verify_end
