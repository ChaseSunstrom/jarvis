#!/usr/bin/env bash
# M23 — final integration: every milestone ticked, no blockers, the ledger and
# the claims register current, CI runs the harness, no placeholders anywhere.
source "$(dirname "$0")/lib.sh"
verify_begin "M23" "final integration"

check_sh "every milestone M00–M22 is ticked in MILESTONES.md" '
    rc=0
    for n in $(seq -w 0 22); do
        grep -qE "^- \[x\] \*\*M$n" MILESTONES.md || { echo "M$n is not ticked"; rc=1; }
    done
    exit $rc'
check_not "BLOCKERS.md has no open entries" grep -nE '^## M[0-9]{2}' BLOCKERS.md
check_sh "CHANGELOG.md names every milestone" '
    rc=0
    for n in $(seq -w 0 23); do grep -q "M$n" CHANGELOG.md || { echo "M$n missing from CHANGELOG.md"; rc=1; }; done
    exit $rc'
check "README documents make verify-all" grep -q 'make verify-all' README.md
check "docs/verification.md names the harness" grep -q 'make verify-all' docs/verification.md
for word in styleguide dashboards Robolectric jarvis-desktop-app; do
    check "docs/verification.md covers: $word" grep -qi "$word" docs/verification.md
done
check_sh "a CI workflow runs make verify-all" 'grep -lE "make verify-all" .github/workflows/*.yml >/dev/null'
check_not "no placeholder markers in any surface's sources" grep -rniE 'TODO|FIXME|coming soon|not implemented' \
    jarvis-web/src jarvis-core/jarvis jarvis-desktop/jarvis_desktop android-app/app/src/main \
    --include='*.svelte' --include='*.ts' --include='*.css' --include='*.py' --include='*.kt'
check_not "no mutation-stub markers anywhere (CI's static job, mirrored)" \
    grep -rnIiE '\bM[U]TANT\b|\bDELIBERATELY BR[O]KEN\b' --include='*.py' --include='*.kt' --include='*.kts' \
    --include='*.ts' --include='*.js' --include='*.svelte' --include='*.sh' --include='*.yml' --include='*.yaml' \
    --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=build --exclude-dir=.svelte-kit \
    --exclude-dir=__pycache__ --exclude-dir=.venv --exclude=ci.yml .
verify_end
