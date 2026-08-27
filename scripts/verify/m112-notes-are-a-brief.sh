#!/usr/bin/env bash
# M112 — notes on the voice screen are a brief: one row, a title and a line, opened on request.
set -u
cd "$(dirname "$0")/../.."
. scripts/verify/lib.sh
verify_begin "M112" "notes on the voice screen are a brief"
use_venv
check_pytest "the server places a note or a page as one row, at a side, stacking down" 'cd jarvis-core && python3 -m pytest tests/test_surface.py -q --timeout=120 --timeout-method=signal'
check "the panel draws a brief at one row and the whole note above it, with ⤢/⤡ in the head" bash -c 'grep -q "surface-brief-" jarvis-web/src/lib/components/SurfacePanel.svelte && grep -q "surface-open-" jarvis-web/src/lib/components/SurfacePanel.svelte && echo brief'
check "svelte-check: 0 errors" bash -c 'cd jarvis-web && npx svelte-check --threshold error 2>&1 | grep -E "0 ERRORS"'
ensure_web_build
run_playwright "three long notes are three one-row briefs, the page does not scroll, ⤢ opens one and ⤡ folds it; M83's surface still holds" e2e/notes-brief.spec.ts e2e/surface.spec.ts
verify_end
