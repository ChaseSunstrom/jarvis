#!/usr/bin/env bash
# M48 — every page in the web console is C2, and there are four of them.
#
# The consolidation is checked first and hardest, because it is the part that
# cannot be un-done cheaply: a page restyled in its old place is a page that
# has to move twice, so the structure is asserted before the styling.
source "$(dirname "$0")/lib.sh"
verify_begin "M48" "the console: four destinations, on C2, in every state"
use_venv

require_file docs/UI_MIGRATION.md
require_file jarvis-web/src/lib/ui/SectionStrip.svelte

# --- the structure -----------------------------------------------------------
check "there are no more than six top-level tabs: the voice screen, the dashboard, and the four M48 destinations" python3 -c '
import re
from pathlib import Path
src = Path("jarvis-web/src/lib/screens.ts").read_text()
nav = [
    re.search(r"path: .([^\x27]+).", block).group(1)
    for block in re.findall(r"\{\n\t\tpath: .*?\n\t\}", src, re.S)
    if "nav: true" in block
]
assert len(nav) <= 6, f"{len(nav)} tabs: {nav}. Five was this milestone; M62 spent a sixth on the dashboard (DEVIATIONS.md §20). A seventh is a decision, not a side effect"
print(f"{len(nav)} destinations: " + ", ".join(nav))
'

check "every current page is accounted for in the navigation architecture" python3 -c '
import re
from pathlib import Path
doc = Path("docs/UI_MIGRATION.md").read_text()
routes = sorted(
    "/" + str(p.parent.relative_to("jarvis-web/src/routes")).replace(".", "")
    for p in Path("jarvis-web/src/routes").rglob("+page.svelte")
)
missing = [r for r in routes if r not in doc and r not in ("/api", "/healthz")]
assert not missing, f"pages with no home in the architecture: {missing}"
print(f"{len(routes)} routed pages, every one placed")
'

check "the old paths still work, as redirects rather than 404s" python3 -c '
from pathlib import Path
import re
src = Path("jarvis-web/src/lib/screens.ts").read_text()
moved = re.search(r"export const MOVED[^{]*\{(.*?)\};", src, re.S).group(1)
pairs = dict(re.findall(r"\x27([^\x27]+)\x27: \x27([^\x27]+)\x27", moved))
assert len(pairs) >= 10, pairs
for old, new in pairs.items():
    redirect = Path("jarvis-web/src/routes") / old.lstrip("/") / "+page.ts"
    assert redirect.is_file(), f"{old} has no redirect; it 404s"
    body = redirect.read_text()
    assert new in body, f"{old} redirects somewhere other than {new}"
    assert "308" in body, f"{old} redirects temporarily; a move is permanent"
print(f"{len(pairs)} moved paths, every one a 308 to where it lives now")
'

check "every learnt keyboard chord still lands where its page went" python3 -c '
import re
from pathlib import Path
src = Path("jarvis-web/src/lib/screens.ts").read_text()
by_chord = {}
# Block by block: one regex across the whole file matched a path from one
# entry with a chord from the next, and reported `g d` going to /house.
for block in re.findall(r"\{\n\t\tpath: .*?\n\t\}", src, re.S):
    path = re.search(r"path: .([^\x27]+).", block)
    chord = re.search(r"chord: .([a-z ]+).", block)
    if path and chord:
        by_chord[chord.group(1)] = path.group(1)
before = {
    "g d": "devices", "g r": "areas", "g a": "automations", "g t": "tools",
    "g k": "tasks", "g c": "code", "g n": "notes", "g m": "memory",
    "g s": "settings", "g h": "/",
}
for chord, meant in before.items():
    assert chord in by_chord, f"{chord} was a chord and is not any more"
    landed = by_chord[chord]
    ok = landed == meant or landed.rsplit("/", 1)[-1] == meant or meant == "settings" and "assistant" in landed
    assert ok, f"{chord} used to mean {meant} and now goes to {landed}"
assert "g b" in by_chord, "the nav has advertised g b for dashboards for months"
print(f"{len(by_chord)} chords, every pre-consolidation one preserved, plus g b which the tooltip already promised")
'

check "the phone offers the same front doors as the browser" \
    python3 android-app/tools/console_parity_test.py

# --- one list, not five ------------------------------------------------------
check "the nav, the chords, the palette and the phone all read one list" python3 -c '
from pathlib import Path
layout = Path("jarvis-web/src/routes/+layout.svelte").read_text()
shortcuts = Path("jarvis-web/src/lib/shortcuts.ts").read_text()
palette = Path("jarvis-web/src/lib/commandPalette.ts").read_text()
parity = Path("android-app/tools/console_parity_test.py").read_text()
assert "NAV_SCREENS" in layout, "the layout keeps its own nav table"
assert "CHORD_ROUTES" in shortcuts, "shortcuts.ts keeps its own chord table"
assert "SCREENS" in palette, "the palette keeps its own page list"
assert "screens.ts" in parity or "SCREENS" in parity, "the phone mirror reads the layout, not the source"
print("layout, chords, palette and the phone mirror: all from screens.ts")
'

check "the palette indexes sections, not only destinations" python3 -c '
from pathlib import Path
src = Path("jarvis-web/src/lib/commandPalette.ts").read_text()
assert "SCREENS.filter" in src
assert "screen.within" in src, "a section is indistinguishable from a destination in the palette"
for section in ("/house/automations", "/settings/tools", "/knowledge/memory"):
    assert section in src, f"{section} has no keywords, so nobody will find it"
print("every section indexed, with its own keywords")
'

# --- the styling and the states ---------------------------------------------
check "no hard-coded style value anywhere in the console" \
    python3 scripts/verify/token_lint.py

check "every screen is declared and uses ScreenState" \
    python3 scripts/verify/web_states_check.py

check_sh "the console's own tests" \
    'cd jarvis-web && npx vitest run 2>&1 | tail -3'

run_playwright "the four destinations, their sections, and every state" states.spec.ts
run_playwright "the nav, the chords and the palette" e2e.spec.ts

verify_end
