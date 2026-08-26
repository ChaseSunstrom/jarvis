#!/usr/bin/env bash
# M76 — Jarvis in the middle, the tasks below.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M76" "Jarvis in the middle, the tasks below"

P=jarvis-web/src/routes/+page.svelte
check "the voice page renders the task dock itself, under the instrument" grep -q 'data-testid="voice-tasks"' "$P"
check "the layout's floating alerts no longer carry the dock on the voice page" bash -c "awk '/data-testid=\"hud-alerts\"/{f=1} f&&/<\\/div>/{exit} f' jarvis-web/src/routes/+layout.svelte | grep -vq TaskDock"
check "the stage centres the instrument and takes what the exchange does not" bash -c "grep -q 'justify-content: center' $P && grep -q 'grid-template-rows: minmax(0, 1fr) auto auto auto;' $P"
check "every breakpoint has the tasks row" bash -c "[ \$(grep -cE \"'tasks|tasks turn'\" $P) -ge 3 ]"
ensure_web_deps
ensure_web_build
run_playwright "the instrument's centre is in the middle band and a task draws below it, at 1440 and 390" 'e2e/voice-layout.spec.ts'
run_playwright "the look, the HUD, the tasks and the four states still hold" 'e2e/look.spec.ts e2e/hud.spec.ts e2e/tasks.spec.ts e2e/states.spec.ts'
check_sh "no new hard-coded value" 'python3 scripts/verify/token_lint.py 2>&1 | tail -1'

verify_end
