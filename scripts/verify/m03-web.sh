#!/usr/bin/env bash
# M03 — the web console on the design system: every screen handles the four
# states, is responsive, has no ad-hoc values and no dead controls; the whole
# Playwright suite passes headless on this host.
source "$(dirname "$0")/lib.sh"
verify_begin "M03" "web: redesign, states, responsive, no ad-hoc values, no dead controls"
ensure_web_deps
use_venv

check_sh "svelte-check: 0 errors" 'cd jarvis-web && npm run check 2>&1 | tail -3'
check_sh "vitest: every unit test passes" 'cd jarvis-web && npx vitest run 2>&1 | tail -4'
check "token lint: jarvis-web/src has no hard-coded value left (baseline empty)" python3 scripts/verify/token_lint.py --require-clean jarvis-web/src
check "no ad-hoc colours / spacing / motion values (web_adhoc_scan.mjs)" node scripts/verify/web_adhoc_scan.mjs jarvis-web/src
check "no dead controls (web_dead_controls.mjs)" node scripts/verify/web_dead_controls.mjs jarvis-web/src
check "every screen is declared and uses <ScreenState> (web_states_check.py)" python3 scripts/verify/web_states_check.py
check_not "overflow is not hidden at the page level (it would mask the responsive check)" \
    grep -nE 'overflow-x:\s*hidden' jarvis-web/src/lib/styles/base.css
check_not "HUD does not re-derive its own colour layer" \
    grep -nE '^\s*--(accent|dim|line|line-soft):' jarvis-web/src/routes/+page.svelte
check_not "no placeholder markers in web sources" \
    grep -rniE 'TODO|FIXME|coming soon|not implemented' jarvis-web/src --include='*.svelte' --include='*.ts' --include='*.css'
for spec in states responsive controls; do
    require_file "jarvis-web/e2e/$spec.spec.ts"
done
ensure_web_build
run_playwright "the whole Playwright suite (headless chromium on E2E_PORT=$E2E_PORT)"
verify_end
