#!/usr/bin/env bash
# M113 — markdown renders completely: tables, nested lists, task lists, code, quotes, breaks — safely.
set -u
cd "$(dirname "$0")/../.."
. scripts/verify/lib.sh
verify_begin "M113" "markdown renders completely"
check "the renderer's table of cases: tables with alignment, nested and task lists, fences, setext, breaks, autolinks, and nothing unsafe" bash -c 'cd jarvis-web && npx vitest run src/lib/markdown.test.ts --reporter=dot 2>&1 | grep -E "Tests .* passed" | grep -v failed'
check "svelte-check: 0 errors" bash -c 'cd jarvis-web && npx svelte-check --threshold error 2>&1 | grep -E "0 ERRORS"'
ensure_web_build
run_playwright "a note with a table, a nested list and a task list opens rendered as all three; the M106 cases still hold" e2e/markdown.spec.ts
verify_end
