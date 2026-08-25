#!/usr/bin/env bash
# M31 — one headless browser, shared. jarvis-browser is the only Chromium that
# fetches pages: the research engine uses it, and so does the live rig, with
# the fixture stand-in demoted to what happens when it cannot be had.
source "$(dirname "$0")/lib.sh"
verify_begin "M31" "one headless browser, shared"
use_venv

require_file testing/live/browser_service.py
require_file testing/live/fixtures/handbook/appliances.html
require_file testing/live/scenarios/research-javascript-page.yaml

# The image. Its browser has to be able to start, and the build has to say so —
# this container ran for weeks answering /healthz 200 with a chromium that
# could not load libglib.
check "chromium's libraries are installed by name, not by install-deps" \
    grep -q 'libglib2.0-0 libnss3' jarvis-browser/Dockerfile
# The command, not the word: the Dockerfile's comment explains at length why
# `playwright install-deps` is gone, and the first version of this check failed
# on that comment.
check_not "playwright install-deps is not run any more" \
    grep -q 'python -m playwright install-deps' jarvis-browser/Dockerfile
check "the build proves chromium launches" \
    grep -q 'chromium launches' jarvis-browser/Dockerfile
check "a launch failure is named, not a 500" \
    grep -q 'chromium would not start' jarvis-browser/jarvis_browser/browser.py
check "a page that writes itself is waited for" \
    grep -q 'networkidle' jarvis-browser/jarvis_browser/browser.py
check_sh "and the wait is bounded and optional" \
    'grep -q "settle_ms: int = 400" jarvis-browser/jarvis_browser/config.py'

# One Chromium. The e2e suites drive the CONSOLE with Playwright, which is a
# different job from fetching a page; nothing may install a browser per task.
check_sh "nothing installs a browser per task" '
hits=$(grep -rn "playwright install" --include=*.py --include=*.sh \
       jarvis-core/jarvis jarvis-orchestrator jarvis-sandbox evals testing/live 2>/dev/null \
       | grep -v "browser.py" || true)
if [ -n "$hits" ]; then echo "$hits"; exit 1; fi
echo "no per-task browser install outside jarvis-browser and the e2e suites"
'
check "the fixture stand-in says what it does not prove" \
    grep -q 'Not proved:' testing/live/fixture_browser.py

# The whole rig suite rather than a `-k` selector: the selector matched three
# of the six tests that matter here, and a check that silently runs half of
# what it names is worse than one that runs none.
check_sh "the rig borrows the real browser and gives it back" \
    'python3 -m pytest testing/live/tests -q \
        --timeout=300 --timeout-method=signal 2>&1 | tail -2'

# The running service, which is the thing the operator actually has.
check_sh "the running jarvis-browser can open a page" '
token=$(grep "^JARVIS_BROWSER_TOKEN=" jarvis-core/.env | cut -d= -f2-)
test -n "$token" || { echo "JARVIS_BROWSER_TOKEN is not in jarvis-core/.env"; exit 1; }
body=$(curl -sS -m 60 -H "Authorization: Bearer $token" http://127.0.0.1:8210/healthz)
echo "$body"
python3 - "$body" <<PY
import json, sys
health = json.loads(sys.argv[1])
assert health.get("browser") == "ok", f"the browser is not usable: {health.get(chr(98)+chr(114)+chr(111)+chr(119)+chr(115)+chr(101)+chr(114))!r}"
PY
'
check "chromium keeps its own sandbox in the deployment" \
    grep -q 'seccomp:unconfined' jarvis-core/docker-compose.yml
check_not "and the browser is not run with --no-sandbox" \
    grep -qE '^\s*-\s*BROWSER_CHROMIUM_NO_SANDBOX=1' jarvis-core/docker-compose.yml

# The scenario, both ways round. Passing with the real browser proves it reads
# a page written by JavaScript; failing with the stand-in proves the scenario
# is about the browser rather than about the fixture.
check_sh "a JavaScript-rendered page is read through the real browser" \
    'set -a; . ./.env 2>/dev/null; set +a; \
     timeout 900 python3 -m testing.live.runner --full --only research-javascript-page \
       --no-browser --target harness 2>&1 | grep -v onnxruntime | tail -3'
check_sh_not "and the same scenario fails against the stand-in" \
    'set -a; . ./.env 2>/dev/null; set +a; \
     LIVE_SHARED_BROWSER=0 timeout 900 python3 -m testing.live.runner --full \
       --only research-javascript-page --no-browser --target harness 2>&1 | tail -3'

check_sh "the research scenarios still pass on the real browser" \
    'set -a; . ./.env 2>/dev/null; set +a; \
     timeout 2400 python3 -m testing.live.runner --full --only research-quick-lookup,research-cancel \
       --no-browser --target harness 2>&1 | grep -v onnxruntime | tail -3'
verify_end
