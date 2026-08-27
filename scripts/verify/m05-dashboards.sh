#!/usr/bin/env bash
# M05 — customisable dashboards: widgets (add/remove/resize/reorder), persisted
# per user, several chart types, a data-source abstraction with the internal
# metrics source first.
source "$(dirname "$0")/lib.sh"
verify_begin "M05" "dashboards + internal metrics source"
use_venv
CORE=jarvis-core/jarvis

require_file "$CORE/metrics/__init__.py"
check "DataSource abstraction" grep -qE 'class DataSource' "$CORE/metrics/__init__.py"
require_file "$CORE/metrics/sources/internal.py"
require_file "$CORE/integrations/dashboards/__init__.py"
for cmd in jarvis/dashboards/list jarvis/dashboards/save jarvis/dashboards/delete jarvis/metrics/query jarvis/metrics/sources; do
    check "WS command $cmd" grep -q "\"$cmd\"" "$CORE/api/websocket.py"
done
check "dashboards are stored per user (token identity)" grep -qE 'token_id|user_id|owner' "$CORE/integrations/dashboards/__init__.py"
require_file tests/contracts/dashboard_layout.json
check "jarvis-core reads the layout contract" grep -rlq dashboard_layout.json jarvis-core/tests
check "jarvis-web reads the layout contract" grep -rlq dashboard_layout.json jarvis-web/src

require_file jarvis-web/src/lib/sections/Dashboards.svelte
# Reachable, not "named in the layout". Both of these grepped for the word
# in a file, and both went red when M48 made dashboards a SECTION of HOUSE
# rather than an eleventh tab — the page was more reachable than before
# (it kept its chord and gained a real one in `g b`), and the checks said
# it had gone.
check "dashboards is reachable, from the console and from the phone" python3 -c '
import re
from pathlib import Path
src = Path("jarvis-web/src/lib/screens.ts").read_text()
block = [b for b in re.findall(r"\{\n\t\tpath: .*?\n\t\}", src, re.S) if "/dashboards" in b]
assert block, "dashboards is not a declared screen at all"
entry = block[0]
within = re.search(r"within: .([^\x27]+).", entry)
chord = re.search(r"chord: .([a-z ]+).", entry)
nav = re.search(r"nav: true", entry)
# A screen is reachable when it is a section of a destination (`within`, the
# M48 shape) OR a destination itself (`nav: true`, the shape M63 gave it back
# when the dashboard became the house at a glance). Either links to it; a
# screen with neither is the one this check exists to catch.
assert within or nav, "dashboards belongs to no destination and is none, so nothing links to it"
assert chord, "dashboards lost its keyboard chord"
# And the phone can get there: it offers the destination dashboards is in.
tabs = Path("android-app/app/src/main/kotlin/ai/jarvis/app/ui/ConsoleTab.kt").read_text()
door = within.group(1) if within else "/dashboards"
front_door = door.lstrip("/").upper()
needle = chr(34) + front_door + chr(34) + ", " + chr(34) + door + chr(34)
assert needle in tabs, "the phone has no " + front_door + " tab"
how = f"a section of {within.group(1)}" if within else "a destination of its own"
print(f"dashboards: {how}, chord {chord.group(1)!r}, reachable from the phone via {front_door}")
'
check "console parity mirror passes" python3 android-app/tools/console_parity_test.py
require_file jarvis-web/src/lib/dashboards/chartTypes.ts
check_sh ">= 4 chart types" \
    '[ "$(grep -cE "^\s*(line|area|bar|stat|gauge|table|scatter|heatmap)\s*:" jarvis-web/src/lib/dashboards/chartTypes.ts)" -ge 4 ]'
check "widgets can be resized" grep -rqi resize jarvis-web/src/lib/dashboards
check "widgets can be reordered" grep -rqiE 'reorder|drag' jarvis-web/src/lib/dashboards
check "mock backend serves dashboards" grep -q 'jarvis/dashboards/' tests/web/mock-ha.mjs
check "mock backend serves metrics queries" grep -q 'jarvis/metrics/query' tests/web/mock-ha.mjs

check_sh "jarvis-core dashboard + metrics tests" \
    'cd jarvis-core && python3 -m pytest tests/test_dashboards.py tests/test_metrics.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
ensure_web_deps
check_sh "web dashboard unit tests" 'cd jarvis-web && npx vitest run src/lib/dashboards 2>&1 | tail -3'
require_file jarvis-web/e2e/dashboards.spec.ts
ensure_web_build
run_playwright "dashboards e2e (add, resize, reorder, persist, remove, every chart type)" e2e/dashboards.spec.ts
verify_end
