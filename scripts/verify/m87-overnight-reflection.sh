#!/usr/bin/env bash
# M87 — Overnight reflection.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M87" "overnight reflection"

check "the memory block names the hour, and the reflection is attached at setup" python3 -c '
import yaml, re
from pathlib import Path
loader = yaml.SafeLoader
loader.add_multi_constructor("!", lambda l, s, n: None)
cfg = yaml.load(Path("jarvis-core/config/configuration.yaml").read_text(), Loader=loader)
assert re.fullmatch(r"\d\d:\d\d", str(cfg.get("memory", {}).get("reflect_at") or "")), cfg.get("memory")
src = Path("jarvis-core/jarvis/integrations/memory/__init__.py").read_text()
assert "_attach_reflection(jarvis, memory, options)" in src
print("memory.reflect_at =", cfg["memory"]["reflect_at"])
'
check_pytest "the reflection suite: reads the day, asks once, keeps what is new, never re-learns a forgotten fact, says what it learned" 'cd jarvis-core && python3 -m pytest tests/test_memory_reflection.py -q --timeout=120 --timeout-method=signal'
check_pytest "the memory suite still passes" 'cd jarvis-core && python3 -m pytest tests/test_memory.py -q --timeout=120 --timeout-method=signal'
check "the scenario asks the house what it learned after the rig calls memory.reflect" python3 -c '
import yaml
from pathlib import Path
s = yaml.safe_load(Path("testing/live/scenarios/memory-reflection.yaml").read_text())
assert s["gated-on"] == "M87" and s["turns"][1]["reflect"] is True
print(s["name"])
'
check_sh "on the house, memory.reflect exists and answers" \
    'python3 -c "
import json, os, httpx
def token():
    for line in open(\"jarvis-core/.env\"):
        if line.startswith(\"JARVIS_TOKEN=\"):
            return line.split(\"=\", 1)[1].strip().strip(chr(34))
    return \"\"
base = os.environ.get(\"JARVIS_URL\", \"http://127.0.0.1:8080\")
r = httpx.post(base + \"/api/services/memory/reflect?return_response\", headers={\"Authorization\": \"Bearer \" + token()}, json={}, timeout=180)
assert r.status_code == 200, (r.status_code, r.text[:200])
body = r.json()
print(json.dumps(body)[:200])
"'
check_sh "on the house, a day of two turns is reflected and read back" \
    'LIVE_ONLY=memory-reflection bash scripts/verify/live_interaction.sh --full 2>&1 | tail -5'

verify_end
