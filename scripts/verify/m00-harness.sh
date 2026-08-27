#!/usr/bin/env bash
# M00 — the verification harness itself: runner, helpers, one script per
# milestone, the planning artefacts, and the toolchain the other scripts need.
source "$(dirname "$0")/lib.sh"
verify_begin "M00" "verification harness"

require_exec scripts/verify/all.sh
require_file scripts/verify/lib.sh
check "Makefile has a verify-all target" grep -qE '^verify-all:' Makefile
check "verify-all recipe is exactly the runner (nothing masks its status)" grep -qE $'^\tbash scripts/verify/all\\.sh$' Makefile
check_not "the runner has no skip status" grep -nE 'status=SKIP|SKIPPED' scripts/verify/all.sh

# The helpers must turn a failing check into a failing script, or nothing
# downstream means anything.
check_sh "lib.sh: a failing check exits 1" '! bash -c "source scripts/verify/lib.sh; verify_begin T t; check x false; verify_end" >/dev/null 2>&1'
check_sh "lib.sh: a passing check exits 0" 'bash -c "source scripts/verify/lib.sh; verify_begin T t; check x true; verify_end" >/dev/null 2>&1'
check_sh "lib.sh: check_not inverts" '! bash -c "source scripts/verify/lib.sh; verify_begin T t; check_not x true; verify_end" >/dev/null 2>&1'

for f in docs/AUDIT.md MILESTONES.md PROCESS.md CHANGELOG.md BLOCKERS.md; do
    require_file "$f"
done
check "MILESTONES.md uses checkboxes with milestone ids" grep -qE '^- \[[ x]\] \*\*M00' MILESTONES.md

check_sh "every milestone in MILESTONES.md has a verify script" '
    ids=$(grep -oE "^- \[[ x]\] \*\*M[0-9]{2}" MILESTONES.md | grep -oE "M[0-9]{2}" | tr A-Z a-z | sort -u)
    [ -n "$ids" ] || { echo "no milestone lines found"; exit 1; }
    rc=0
    for id in $ids; do
        ls scripts/verify/"$id"-*.sh >/dev/null 2>&1 || { echo "no script for $id"; rc=1; }
    done
    exit $rc'
check_sh "every verify script has a milestone line and is named in it verbatim" '
    rc=0
    for s in scripts/verify/m[0-9][0-9]-*.sh; do
        id=$(basename "$s" | cut -c1-3 | tr a-z A-Z)
        grep -qE "^- \[[ x]\] \*\*$id" MILESTONES.md || { echo "no milestone for $s"; rc=1; }
        grep -qF "bash $s" MILESTONES.md || { echo "MILESTONES.md does not name the command: bash $s"; rc=1; }
    done
    exit $rc'
check_sh "every verify script parses, is executable, and uses the helpers" '
    rc=0
    for s in scripts/verify/m[0-9][0-9]-*.sh; do
        bash -n "$s" || rc=1
        [ -x "$s" ] || { echo "$s is not executable"; rc=1; }
        grep -q verify_begin "$s" && grep -q verify_end "$s" || { echo "$s lacks verify_begin/verify_end"; rc=1; }
    done
    exit $rc'
# A device is never touched by a verify script. The pattern is written with
# brackets so that this line does not match itself.
check_not "verify scripts never reach for a device" grep -rnE '\b(a[d]b|emu[l]ator|connected(Debug)?[A]ndroidTest)\b' scripts/verify/

# Toolchain this host needs for the rest of the harness.
use_venv
check "pytest in the venv" python3 -m pytest --version
check "ruff in the venv" python3 -m ruff --version
check_sh "node >= 20" 'node -e "process.exit(parseInt(process.versions.node, 10) >= 20 ? 0 : 1)"'
require_dir jarvis-web/node_modules
check_sh "Playwright chromium installed (npx playwright install chromium)" \
    'ls -d "${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"/chromium*-* >/dev/null 2>&1'
check "playwright.config.ts honours E2E_PORT (the live HUD holds 8199)" grep -q E2E_PORT jarvis-web/playwright.config.ts
check ".verify/ is gitignored" git check-ignore -q .verify/x.log
check "docker compose config works without the daemon (static checks)" docker compose -f jarvis-core/docker-compose.yml config -q
verify_end
