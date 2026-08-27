#!/usr/bin/env bash
# M101 — One shape for every payload: the mock cannot drift from the server
# unnoticed, the Code screen names its worker, Readings says when no sensor
# has a room, the error page goes home.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M101" "one shape for every payload"

check "the parity script exists, is executable, and names every list command the server answers" python3 -c '
import re
from pathlib import Path
script = Path("scripts/verify/mock_parity.py")
assert script.exists() and script.stat().st_mode & 0o111
src = script.read_text()
ws = Path("jarvis-core/jarvis/api/websocket.py").read_text()
served = set(re.findall(r"\"((?:jarvis|config)/[a-z_/]*list)\"", ws))
named = set(re.findall(r"\"((?:jarvis|config)/[a-z_/]*list)\"", src))
missing = sorted(served - named)
assert not missing, f"list commands the parity script does not send: {missing}"
print(f"{len(served)} list commands, all sent")
'
check "the Code screen names its worker, Readings says when no sensor has a room, the error page goes home" python3 -c '
from pathlib import Path
assert "\"worker\"" in Path("jarvis-core/jarvis/integrations/code/__init__.py").read_text()
assert "opencode_version" in Path("jarvis-orchestrator/app/main.py").read_text()
code = Path("jarvis-web/src/lib/sections/Code.svelte").read_text()
assert "code-worker" in code
readings = Path("jarvis-web/src/lib/dashboards/Readings.svelte").read_text()
assert "readings-no-rooms" in readings
assert "location.href = \x27/house/devices\x27" in Path("jarvis-web/src/routes/+error.svelte").read_text()
mock = Path("tests/web/mock-ha.mjs").read_text()
assert "worker:" in mock.split("const codePayload", 1)[1][:2500]
print("worker line, no-rooms sentence, /house/devices, mock")
'
use_venv
check_pytest "the code suite: the listing carries the worker, and the mock agrees on its keys" 'cd jarvis-core && python3 -m pytest tests/test_code_repos.py -q --timeout=120 --timeout-method=signal -k "worker or mock"'
check_pytest "the orchestrator suite: healthz names the binary" 'cd jarvis-orchestrator && python3 -m pytest tests -q --timeout=120 --timeout-method=signal -k "healthz or version"'
check "docs/verification.md web rows carry the measured counts (the M90 commands)" python3 -c '
import re, subprocess
from pathlib import Path
doc = Path("docs/verification.md").read_text()
specs = len(list(Path("jarvis-web/e2e").glob("*.spec.ts")))
tests = sum(len(re.findall(r"^\s*test\(", p.read_text(), re.M)) for p in Path("jarvis-web/e2e").glob("*.spec.ts"))
vitest_files = len(list(Path("jarvis-web/src").rglob("*.test.ts")))
vitest = sum(len(re.findall(r"^\s*(it|test)\(", p.read_text(), re.M)) for p in Path("jarvis-web/src").rglob("*.test.ts"))
assert f"(Playwright, {specs} spec files" in doc, f"the Playwright row does not say {specs} spec files"
assert re.search(rf"Playwright, {specs} spec files[^|]*\| {tests} \|", doc), f"the Playwright row does not count {tests}"
assert f"(vitest, {vitest_files} files) | {vitest} |" in doc, f"the vitest row does not say {vitest_files} files / {vitest}"
print(f"Playwright {specs} files / {tests} tests, vitest {vitest_files} files / {vitest}")
'
ensure_web_build
run_playwright "the Code screen and Readings" code.spec.ts dashboards.spec.ts
check_sh "the mock carries every key the running house sends on every list" \
    'python3 scripts/verify/mock_parity.py 2>&1 | tail -12'
check_sh "the live smoke scenarios still pass" \
    'LIVE_ONLY=house-light-on,chat-context-retention,lock-needs-a-human bash scripts/verify/live_interaction.sh --implemented-only 2>&1 | tail -4'
verify_end
