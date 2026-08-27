#!/usr/bin/env bash
# M55 — Simpler menus everywhere.
#
# The operator's words: "clean up the other menus to make them more simple".
# M54 did SETTINGS; this does HOUSE, WORK, KNOWLEDGE and the tools page. What
# "simple" means here is checkable and is pinned in docs/UI_MIGRATION.md §4 as
# a menu inventory — one row per screen: what its rows are, how many controls a
# row shows at rest, which control is the primary, how many search boxes — and
# e2e/menus.spec.ts reads that table and holds every screen to it against the
# mock backend. No duplicate way to the same thing; one control per row where
# one will do; the tools page one search over everything it lists.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M55" "simpler menus everywhere"

require_file docs/UI_MIGRATION.md
require_file jarvis-web/e2e/menus.spec.ts
require_file jarvis-web/src/lib/sections/Tools.svelte

check "the menu inventory names every leaf screen once, with a rows marker, a per-row cap, a primary and a search count" python3 -c '
import re, json, subprocess
from pathlib import Path
doc = Path("docs/UI_MIGRATION.md").read_text()
start = doc.index("### The menu inventory")
block = doc[start: doc.index("\n### ", start + 10) if "\n### " in doc[start + 10:] else len(doc)]
rows = [l for l in block.splitlines() if l.startswith("| ") and "| `/" in l]
routes = [re.search(r"\| `(/[^`]*)`", l).group(1) for l in rows]
assert len(routes) == len(set(routes)), "a route is listed twice"
screens = Path("jarvis-web/src/lib/screens.ts").read_text()
declared = re.findall(r"path: \x27([^\x27]+)\x27", screens)
# A leaf is a screen that draws its own rows: everything declared except the
# voice screen, a detail page, the styleguide and a destination that only
# holds sections. Counting slashes was a proxy for that and broke the day a
# destination had no sections (/dashboards, M62).
holders = set(re.findall(r"within: \x27([^\x27]+)\x27", screens))
leaves = [p for p in declared if "[" not in p and p != "/" and p not in holders and not p.startswith("/styleguide")]
missing = sorted(set(leaves) - set(routes)); extra = sorted(set(routes) - set(leaves) - {"/"})
assert not missing and not extra, f"inventory vs screens.ts: missing {missing}, extra {extra}"
for l in rows:
    cells = [c.strip() for c in l.strip().strip("|").split("|")]
    assert len(cells) >= 7, l
    assert re.fullmatch(r"\d+|—", cells[3]), f"per-row cap must be a number or —: {l}"
    assert re.fullmatch(r"\d+", cells[5]), f"search count must be a number: {l}"
print(f"{len(rows)} screens in the inventory")
'
check "every M55 row in docs/UI_MIGRATION.md is ticked" python3 -c '
from pathlib import Path
doc = Path("docs/UI_MIGRATION.md").read_text()
start = doc.index("### M55 —"); end = doc.index("\n## ", start)
block = doc[start:end]
open_rows = [l for l in block.splitlines() if l.startswith("- [ ]")]
done = [l for l in block.splitlines() if l.startswith("- [x]")]
assert done, "no M55 rows"
assert not open_rows, "unticked:\n" + "\n".join(open_rows)
print(f"{len(done)} rows ticked")
'
check "every list row carries data-jv-row so the per-row cap can be measured" python3 -c '
import re
from pathlib import Path
want = {
  "sections/Devices.svelte": 1, "sections/Areas.svelte": 1, "sections/Automations.svelte": 1,
  "sections/Tasks.svelte": 1, "sections/Code.svelte": 1, "sections/Notes.svelte": 1, "sections/Memory.svelte": 1,
  "sections/Tools.svelte": 2, "components/Extensions.svelte": 1, "components/McpServers.svelte": 1,
  "components/SkillsPanel.svelte": 1,
  # the settings rows (plain and raw) are the shared SettingRow (M107), rendered with a testid; the mark lives there
  "ui/SettingRow.svelte": 1,
}
for rel, n in want.items():
    src = Path("jarvis-web/src/lib", rel).read_text()
    assert src.count("data-jv-row") >= n, f"{rel}: {src.count(chr(100)+chr(97)+chr(116)+chr(97)+chr(45)+chr(106)+chr(118)+chr(45)+chr(114)+chr(111)+chr(119))} data-jv-row, wanted {n}"
print("rows marked")
'
check "the tools page is one search over everything it lists" python3 -c '
from pathlib import Path
import re
src = Path("jarvis-web/src/lib/sections/Tools.svelte").read_text()
markup = re.sub(r"/\*.*?\*/|<!--.*?-->", "", src, flags=re.S)  # comments may name the hook; markup counts
assert markup.count("data-jv-filter") == 1, "one search box on the tools page"
assert "entity-filter" not in src, "the exposure fold still has its own filter"
assert "tool-filter" not in src or src.count("bind:value={query}") >= 1, "the search is not the one query"
for sub in ("Extensions", "McpServers", "SkillsPanel"):
    assert f"<{sub} " in src and "{query}" in src.split(f"<{sub} ")[1].split("/>")[0], f"{sub} does not take the page query"
print("one search")
'
check "no automation row shows four controls at rest" bash -c '! grep -q "Run now" jarvis-web/src/lib/sections/Automations.svelte || grep -q "data-jv-more" jarvis-web/src/lib/sections/Automations.svelte'
check "the dashboard has one way into its layout editor" bash -c 'python3 - <<PY
from pathlib import Path
src = Path("jarvis-web/src/lib/sections/Dashboards.svelte").read_text()
assert "dashboard-add" in src
# The edit toggle is only offered while editing (as DONE); the one way in is + Widget.
i = src.index("dashboard-edit"); before = src[max(0, i - 400): i]
assert "{#if editing}" in before, "Edit layout is still a second way into editing"
print("one way in")
PY'
# The e2e server serves the built console, so the build comes first — a spec
# run against a stale bundle measures the last change, not this one.
check_sh "the console builds" 'cd jarvis-web && npm run build 2>&1 | tail -2'
check "token lint: the console is clean" python3 scripts/verify/token_lint.py --require-clean jarvis-web/src
check "every screen is declared and uses ScreenState" python3 scripts/verify/web_states_check.py
check "no dead controls" node scripts/verify/web_dead_controls.mjs
check_sh "svelte-check finds nothing" 'cd jarvis-web && npx svelte-check --threshold error 2>&1 | tail -2'
check_sh "the console's unit tests" 'cd jarvis-web && npx vitest run 2>&1 | tail -3'
check_sh "the inventory holds against the mock: one primary, unique controls outside rows, the per-row cap, the search" \
    'cd jarvis-web && E2E_PORT=$E2E_PORT npx playwright test menus.spec.ts 2>&1 | tail -4'
check_sh "the specs that drive the trimmed rows still pass: dashboards, automations, tools, areas, notes, memory" \
    'cd jarvis-web && E2E_PORT=$E2E_PORT npx playwright test dashboards.spec.ts e2e.spec.ts console-repairs.spec.ts knowledge.spec.ts notes.spec.ts memory.spec.ts extensions.spec.ts mcp.spec.ts 2>&1 | tail -4'
check_sh "every screen is drawn to the direction (look.spec)" \
    'cd jarvis-web && E2E_PORT=$E2E_PORT npx playwright test look.spec.ts 2>&1 | tail -3'
# --- the pictures, regenerated here so they cannot be stale -----------------
check_sh "the house, work, knowledge and tools screens at three widths, regenerated" \
    'cd jarvis-web && UI_REVIEW=1 E2E_PORT=$E2E_PORT npx playwright test ui-review.spec.ts -g "House|Devices|Areas|Dashboards|Automations|Work|Tasks|Code|Knowledge|Notes|Memory|Tools" 2>&1 | tail -3'
check "one folder per trimmed screen, three pictures each" python3 -c '
from pathlib import Path
for slug in ("house-devices", "house-areas", "dashboards", "house-automations", "work-tasks", "work-code", "knowledge-notes", "knowledge-memory", "settings-tools"):
    d = Path("docs/ui-review", slug)
    assert d.is_dir() and {p.name for p in d.iterdir()} >= {"desktop.png", "tablet.png", "mobile.png"}, slug
print("pictures present")
'
check_sh "the trimmed routes open in the real console with no console error and only token colours" \
    'LIVE_CONSOLE_ROUTES=/house/devices,/house/areas,/dashboards,/house/automations,/work/tasks,/work/code,/knowledge/notes,/knowledge/memory,/settings/tools python3 testing/live/console_pass.py 2>&1 | tail -6'
verify_end
