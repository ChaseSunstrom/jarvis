#!/usr/bin/env bash
# M62 — The dashboard is a destination, not a section.
#
# From M48 the dashboard was a section of HOUSE: two taps deep, behind the
# device list, on both the console and the phone. It is the thing a person
# opens the console to look at, so it is the first console tab now — its own
# path, no sections, the one destination that does not redirect — and the
# phone's native strip (bound to the console's by console_parity_test.py)
# opens on it. Everything that knew the old placement is checked here: the
# registry, the redirect, the bar, the palette, the phone, the inventory, the
# pictures, and the e2e that drives the bar.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M62" "the dashboard, a destination"

require_file jarvis-web/src/routes/dashboards/+page.svelte
require_file jarvis-web/src/routes/house/dashboards/+page.ts
require_file android-app/app/src/main/kotlin/ai/jarvis/app/ui/ConsoleTab.kt

check "the registry: /dashboards is a top-level tab with no destination above it, and the section is gone" python3 -c '
import re
from pathlib import Path
src = Path("jarvis-web/src/lib/screens.ts").read_text()
blocks = re.findall(r"\{\s*\n(.*?)\n\t\}", src, re.S)
dash = [b for b in blocks if "path: \x27/dashboards\x27" in b]
assert len(dash) == 1, "no /dashboards screen"
assert "nav: true" in dash[0] and "within:" not in dash[0], dash[0]
assert not [b for b in blocks if "path: \x27/house/dashboards\x27" in b], "/house/dashboards is still a screen"
nav = [re.search(r"path: \x27([^\x27]+)\x27", b).group(1) for b in blocks if "nav: true" in b]
assert nav[:2] == ["/", "/dashboards"], f"the dashboard is not the first console tab: {nav}"
print("first console tab:", nav)
'
check "the old path is a permanent redirect, and nothing serves a page there" python3 -c '
from pathlib import Path
old = Path("jarvis-web/src/routes/house/dashboards")
assert "redirect(308, \x27/dashboards\x27)" in (old / "+page.ts").read_text(), "not a 308 to /dashboards"
assert not (old / "+page.svelte").exists(), "the old section page is still served"
assert not Path("jarvis-web/src/routes/dashboards/+page.ts").exists(), "the destination still redirects away"
print("308 /house/dashboards -> /dashboards")
'
check "MOVED remembers the move (the M48 gate checks every entry has its redirect)" python3 -c '
from pathlib import Path
assert "\x27/house/dashboards\x27: \x27/dashboards\x27" in Path("jarvis-web/src/lib/screens.ts").read_text()
print("remembered")
'
check "the page is a destination: a title and a lede of its own above the section" bash -c 'grep -q "ScreenTitle" jarvis-web/src/routes/dashboards/+page.svelte && grep -q "Jarvis · Dashboards" jarvis-web/src/routes/dashboards/+page.svelte'
check "the palette indexes it under its own path" python3 -c '
import re
from pathlib import Path
src = Path("jarvis-web/src/lib/commandPalette.ts").read_text()
assert re.search(r"^\s*\x27/dashboards\x27: \x27[^\x27]+\x27", src, re.M), "no keywords under /dashboards"
assert "\x27/house/dashboards\x27" not in src, "keywords still filed under the old path"
print("indexed")
'
check "the phone opens the console on it, and its strip mirrors the bar" python3 -c '
from pathlib import Path
src = Path("android-app/app/src/main/kotlin/ai/jarvis/app/ui/ConsoleTab.kt").read_text()
assert "DASHBOARDS(\"DASHBOARDS\", \"/dashboards\")" in src, "no DASHBOARDS tab"
assert "val DEFAULT = DASHBOARDS" in src, "MANAGE still lands on HOUSE"
print("DASHBOARDS first, and the default")
'
check_sh "the console parity mirror agrees (five front doors, in the bar's order)" 'python3 android-app/tools/console_parity_test.py 2>&1 | tail -2'
check "the menu inventory lists it as a destination" grep -q "^| DASHBOARDS | \`/dashboards\` |" docs/UI_MIGRATION.md
check "the migration doc says where it went" grep -q "A destination of its own since M62" docs/UI_MIGRATION.md
check "the bar's e2e drives five destinations, the dashboard first" bash -c 'grep -q "five destinations" jarvis-web/e2e/e2e.spec.ts && grep -q "\[\"nav-dashboards\", \"/dashboards\"\]" jarvis-web/e2e/e2e.spec.ts'
check_sh "every screen is declared once and served once" 'python3 scripts/verify/web_states_check.py 2>&1 | tail -1'
check_sh "the console builds" 'cd jarvis-web && npm run build > /dev/null 2>&1 && echo built'
check_sh "the bar, the dashboard, its states and its menu row, in a browser" \
    'cd jarvis-web && E2E_PORT=$E2E_PORT npx playwright test e2e.spec.ts dashboards.spec.ts states.spec.ts menus.spec.ts -g "five destinations|Dashboards|dashboard" 2>&1 | tail -3'
check_sh "three pictures of it, at three widths" \
    'cd jarvis-web && UI_REVIEW=1 E2E_PORT=$E2E_PORT npx playwright test ui-review.spec.ts -g "Dashboards" 2>&1 | tail -2 && cd .. && test -f docs/ui-review/dashboards/desktop.png && test -f docs/ui-review/dashboards/tablet.png && test -f docs/ui-review/dashboards/mobile.png && ! test -d docs/ui-review/house-dashboards && echo "docs/ui-review/dashboards: desktop, tablet, mobile"'

verify_end
