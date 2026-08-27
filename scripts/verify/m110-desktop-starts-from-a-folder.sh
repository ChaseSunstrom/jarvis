#!/usr/bin/env bash
# M110 — the desktop app starts from a downloaded folder: no setuid helper, no console yet.
set -u
cd "$(dirname "$0")/../.."
. scripts/verify/lib.sh
verify_begin "M110" "the desktop app starts from a downloaded folder"

check "the sandbox-helper check is Chromium's three (root, setuid, executable) and a user-owned 4755 fails it" bash -c 'cd jarvis-desktop-app && npx vitest run --reporter=dot 2>&1 | grep -E "Tests .* passed" '
check "the main process compiles with the switch, the notice window and the renderer-gone line" bash -c 'cd jarvis-desktop-app && npx tsc -p tsconfig.json && grep -q "appendSwitch(\"no-sandbox\")" src/main/main.ts && grep -q "render-process-gone" src/main/main.ts && grep -q "showUnreachable" src/main/main.ts && echo compiled'
check "under xvfb, from node_modules (a user-owned chrome-sandbox): the switch is set before ready, and a closed port draws the notice page that names the URL" bash -c 'cd jarvis-desktop-app && CI= bash tools/xvfb.sh npx playwright test e2e/unreachable.spec.ts --reporter=line 2>&1 | tail -3 | grep -E "^\s+[0-9]+ passed" '
check "the packaged binary in dist-app/linux-unpacked, run as this user against a closed port, says why on stderr and does not abort" bash -c 'cd jarvis-desktop-app && [ -x dist-app/linux-unpacked/jarvis-desktop-app ] && JARVIS_CONSOLE_URL=http://127.0.0.1:8198 JARVIS_AGENT_PORT= JARVIS_AGENT_TOKEN= timeout 15 bash tools/xvfb.sh ./dist-app/linux-unpacked/jarvis-desktop-app 2>&1 | grep -aE "no console at http://127.0.0.1:8198|is not setuid root" | sort -u | head -2 | grep -q "no console at" && echo "starts, names the URL"'
check "the README tells a person what the app does about the sandbox and the missing console" bash -c 'grep -q "chrome-sandbox" jarvis-desktop-app/README.md && grep -q "No console there yet" jarvis-desktop-app/README.md && echo documented'
verify_end
