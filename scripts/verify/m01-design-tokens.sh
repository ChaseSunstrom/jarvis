#!/usr/bin/env bash
# M01 — the design system: one token source (design/tokens.json), generated into
# every surface, drift-checked, linted (no hard-coded value in app code, ratchet
# baseline for legacy files), and rendered on the style-guide page.
source "$(dirname "$0")/lib.sh"
verify_begin "M01" "design system: tokens, generators, token lint, style guide"
use_venv

require_file design/tokens.json
require_file design/build.py
check "tokens.json: six groups, DTCG leaves, valid colours" python3 scripts/verify/tokens_check.py
check "generated outputs current + orb palette not drifted (design/build.py --check)" python3 design/build.py --check

KT=android-app/app/src/main/kotlin/ai/jarvis/app/ui
for f in jarvis-web/src/lib/styles/tokens.css \
         jarvis-web/src/lib/tokens.ts \
         jarvis-desktop/jarvis_desktop/tokens.py \
         "$KT/theme/JarvisTokens.kt" \
         "$KT/theme/JarvisTheme.kt" \
         android-app/app/src/main/res/values/tokens.xml \
         android-app/app/src/main/res/values/colors.xml; do
    require_generated "$f"
done
check_sh "tokens.css declares >= 100 --jv- properties" \
    '[ "$(grep -cE "^\s*--jv-" jarvis-web/src/lib/styles/tokens.css)" -ge 100 ]'
check "JarvisTheme.kt is a Compose theme" grep -qE 'MaterialTheme|darkColorScheme|@Composable' "$KT/theme/JarvisTheme.kt"
check "Compose is enabled for it" grep -qE 'compose\s*=\s*true' android-app/app/build.gradle.kts
check "the self-hosted faces ship with the console" test -f jarvis-web/static/fonts/barlow-400.woff2
check "…and their licences" test -f jarvis-web/static/fonts/OFL-Barlow.txt

# Consumers alias the generated values; no hand copy remains.
# The palette constants alias the generated tokens. Alpha variants inside the
# builders (`0x553FD8FF`, an accent stroke at 33 %) are counted by the token lint
# and belong to M08, which drives the Android baseline to zero.
check_not "JarvisUi.kt palette constants are aliases, not literals" grep -nE 'const val [A-Z_]+ = 0x[0-9A-Fa-f]{8}' "$KT/JarvisUi.kt"
check_not "desktop theme.py has no colour literals (imports tokens.py)" grep -nE '#[0-9a-fA-F]{6}' jarvis-desktop/jarvis_desktop/theme.py
check_not "colors.xml is aliases only" grep -nE '#[0-9A-Fa-f]{6,8}' android-app/app/src/main/res/values/colors.xml
check "motion.ts reads its durations from the tokens" grep -q "tokenMs('--jv-dur-base')" jarvis-web/src/lib/motion.ts

# The lint: no new hard-coded value anywhere, legacy counts only ever fall.
require_file design/token-lint.baseline.json
check "token lint (ratchet against the baseline)" python3 scripts/verify/token_lint.py
check_not "token-lint baseline lists no generated file" grep -nE '"[^"]*(tokens\.css|tokens\.ts|tokens\.py|JarvisTokens\.kt|JarvisTheme\.kt)"' design/token-lint.baseline.json

# The style guide renders every group, on tokens only.
require_file jarvis-web/src/routes/styleguide/+page.svelte
for group in color type space radius elevation motion chrome; do
    check "style guide renders the $group tokens (data-tokens=\"$group\")" \
        grep -q "data-tokens=\"$group\"" jarvis-web/src/routes/styleguide/+page.svelte
done
# The four states, asserted as CONTROLS rather than as a marker attribute.
# This grepped for `data-state="offline"`, which the page stopped emitting
# when the states moved into `<ScreenState>` — so the check was red while the
# states were more real than they had ever been. `e2e/styleguide.spec.ts`
# clicks each of these and asserts the page changes, which is the claim.
check "style guide can be driven into all four screen states" python3 -c '
from pathlib import Path
src = Path("jarvis-web/src/routes/styleguide/+page.svelte").read_text()
wanted = ("loading", "empty", "error", "offline")
missing = [s for s in wanted if s not in src]
assert not missing, f"the style guide cannot show: {missing}"
assert "ScreenState" in src, "the states are drawn by something other than the shared component"
assert "state-{s}" in src, "no per-state control for e2e/styleguide.spec.ts to click"
print("loading, empty, error and offline, each with a control")
'
check "style guide has no dead controls" node scripts/verify/web_dead_controls.mjs jarvis-web/src/routes/styleguide

# The skill that binds future sessions, and the rule that loads it.
require_file .claude/skills/jarvis-design-system/SKILL.md
require_file .claude/rules/design-system.md
check "the skill names the source, the generator and the lint" grep -q 'design/tokens.json' .claude/skills/jarvis-design-system/SKILL.md
check "the skill mandates the four states" grep -qiE 'loading.*empty.*error.*offline' .claude/skills/jarvis-design-system/SKILL.md

# The parity tests every surface already had keep holding against the source.
ensure_web_deps
check_sh "web token parity + contrast (vitest tokens.test.ts, motion.test.ts, icons.test.ts)" \
    'cd jarvis-web && npx vitest run src/lib/tokens.test.ts src/lib/motion.test.ts src/lib/icons.test.ts 2>&1 | tail -4'
check_sh "desktop theme tests" 'cd jarvis-desktop && python3 -m pytest tests/test_theme.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
check "android design_token mirror" python3 android-app/tools/design_token_test.py
check "android type_scale mirror" python3 android-app/tools/type_scale_test.py
check "android gradle-script spec" python3 android-app/tools/gradle_script_test.py
ensure_web_build
run_playwright "style-guide e2e (renders every section, screenshot kept under .verify/)" e2e/styleguide.spec.ts
verify_end
