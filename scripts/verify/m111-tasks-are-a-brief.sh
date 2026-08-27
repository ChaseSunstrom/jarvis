#!/usr/bin/env bash
# M111 — the voice tab's tasks are one-line briefs that expand; the page never scrolls for them.
set -u
cd "$(dirname "$0")/../.."
. scripts/verify/lib.sh
verify_begin "M111" "tasks on the voice tab are a brief"
check "the dock draws a brief button per task, a detail behind it, and a list that scrolls inside itself" bash -c 'grep -q "task-dock-brief-" jarvis-web/src/lib/components/TaskDock.svelte && grep -q "task-dock-detail-" jarvis-web/src/lib/components/TaskDock.svelte && grep -q "max-height: min(22vh, 13rem)" jarvis-web/src/lib/components/TaskDock.svelte && echo brief'
check "svelte-check: 0 errors" bash -c 'cd jarvis-web && npx svelte-check --threshold error 2>&1 | grep -E "0 ERRORS"'
ensure_web_build
run_playwright "four running tasks: no page scroll at 1440×900, the dock within its cap at 1280×720, one-line rows, click expands and folds; the M76 placement still holds" e2e/tasks-brief.spec.ts e2e/voice-layout.spec.ts
verify_end
