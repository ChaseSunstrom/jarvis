#!/usr/bin/env bash
# M02 — the component library is a real library (documented, tested, rendered
# on a style-guide page), not CSS classes copied between pages.
source "$(dirname "$0")/lib.sh"
verify_begin "M02" "component library: primitives, ScreenState, the reactor, on the style guide"
ensure_web_deps

UI=jarvis-web/src/lib/ui
require_file "$UI/index.ts"
require_file "$UI/README.md"
require_file jarvis-web/src/routes/styleguide/+page.svelte
require_file "$UI/Reactor.svelte"
require_file "$UI/ScreenState.svelte"
check "the reactor component is on the style guide" grep -q "<Reactor" jarvis-web/src/routes/styleguide/+page.svelte
check_sh "index.ts exports >= 12 components" \
    '[ "$(grep -cE "^export \{ default as [A-Z][A-Za-z0-9]+ \} from" jarvis-web/src/lib/ui/index.ts)" -ge 12 ]'
check_sh "every export exists, has a <!-- @component doc, a README section, and is on the style guide" '
    rc=0
    for name in $(grep -oE "^export \{ default as [A-Z][A-Za-z0-9]+" jarvis-web/src/lib/ui/index.ts | awk "{print \$5}"); do
        f=jarvis-web/src/lib/ui/$name.svelte
        [ -f "$f" ] || { echo "$name: no $f"; rc=1; continue; }
        # Svelte'"'"'s documented form puts `@component` on the line after `<!--`,
        # so the block is the file'"'"'s first two lines rather than one grep-able line.
        head -2 "$f" | tr "\n" " " | grep -q "<!-- *@component" || { echo "$name: no @component doc block at the top"; rc=1; }
        grep -qE "^## $name\b" jarvis-web/src/lib/ui/README.md || { echo "$name: no \"## $name\" section in ui/README.md"; rc=1; }
        grep -q "<$name\b" jarvis-web/src/routes/styleguide/+page.svelte || { echo "$name: not rendered on /styleguide"; rc=1; }
    done
    exit $rc'
check_not "empty-state markup is no longer hand-copied across pages" grep -rn 'class="jv-empty"' jarvis-web/src/routes
require_file "$UI/ssr.test.ts"
check_sh "ui component unit tests pass" 'cd jarvis-web && npx vitest run src/lib/ui 2>&1 | tail -4'
require_file jarvis-web/e2e/styleguide.spec.ts
check "token lint: the component library is clean" python3 scripts/verify/token_lint.py --require-clean jarvis-web/src/lib/ui
ensure_web_build
run_playwright "style-guide e2e (renders every section, screenshot kept under .verify/)" e2e/styleguide.spec.ts
verify_end
