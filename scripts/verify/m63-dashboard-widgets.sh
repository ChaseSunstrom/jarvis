#!/usr/bin/env bash
# M63 — The dashboard shows the house.
#
# From M62 a dashboard was a twelve-column grid of graphs. The operator's ask
# was "the dashboard a main thing, full functionality": the house at a glance.
# So a widget has a KIND now — a graph, one entity with its switch, the newest
# readings by room, a still from a camera, the sky tonight, the newest moments
# — end to end: the contract both suites read, the three commands behind the
# new kinds, the vision integration's still (a look, with a look's consent
# and audit), the mock that serves all of it, the console that draws it, and a
# shipped House the console opens on. Each check below names the link of that
# chain it holds, so a red line says which piece is missing.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M63" "the dashboard shows the house"

require_file tests/contracts/dashboard_layout.json
require_file jarvis-core/config/dashboards/house.yaml
require_file jarvis-core/tests/test_dashboard_widgets.py
require_file jarvis-web/src/lib/dashboards/widgets.ts
require_file jarvis-web/src/lib/dashboards/widgets.test.ts
require_file jarvis-web/src/lib/dashboards/tiles.test.ts
for tile in EntityTile Readings CameraStill SkyTonight Moments; do
    require_file "jarvis-web/src/lib/dashboards/$tile.svelte"
done

check "the contract: six kinds, each naming what it needs, a widget without a kind is a graph, and the three commands" python3 -c '
import json
c = json.load(open("tests/contracts/dashboard_layout.json"))
kinds = c["kinds"]
assert set(kinds) == {"metric", "entity", "readings", "camera", "sky", "moments"}, sorted(kinds)
assert all("needs" in k and "what" in k for k in kinds.values())
assert kinds["entity"]["needs"] == ["entity"] and kinds["metric"]["needs"] == ["series"]
assert "kind" in c["widget"]["required"] and "absent means `metric`" in c["widget"]["fields"]["kind"]
for cmd in ("jarvis/sensors/readings", "jarvis/sky/summary", "jarvis/vision/still"):
    assert cmd in c["commands"], cmd
print("six kinds, three commands")
'
check "the server: the kinds are the ones the contract names, an unkinded widget is a graph, a tile needs an entity" python3 -c '
import json, sys
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.dashboards import KINDS, clean_widget
c = json.load(open("tests/contracts/dashboard_layout.json"))
assert set(KINDS) == set(c["kinds"]), (KINDS, list(c["kinds"]))
assert clean_widget({"type": "line", "series": ["a"]}, 0)["kind"] == "metric"
assert clean_widget({"kind": "entity", "entity": "hall lamp"}, 0) is None
assert clean_widget({"kind": "entity", "entity": "light.hall"}, 0)["entity"] == "light.hall"
assert clean_widget({"kind": "camera"}, 0)["camera"] == ""
print("server kinds:", ", ".join(KINDS))
'
check "the console: the kinds are the ones the contract names, and a widget with no kind reads as a graph" python3 -c '
import json, re
from pathlib import Path
c = json.load(open("tests/contracts/dashboard_layout.json"))
src = Path("jarvis-web/src/lib/dashboards/chartTypes.ts").read_text()
block = src[src.index("export const WIDGET_KINDS"): src.index("} as const;", src.index("export const WIDGET_KINDS"))]
kinds = re.findall(r"^\t(\w+): \{", block, re.M)
assert set(kinds) == set(c["kinds"]), (kinds, list(c["kinds"]))
layout = Path("jarvis-web/src/lib/dashboards/layout.ts").read_text()
assert "source.kind === undefined" in layout and "\x27metric\x27" in layout, "toWidget does not default the kind"
assert "wireWidget" in layout, "no wireWidget: the console would send every kind\x27s fields on every widget"
print("console kinds:", ", ".join(kinds))
'
check "the three commands are websocket commands, the client calls them, and the mock serves them" python3 -c '
from pathlib import Path
ws = Path("jarvis-core/jarvis/api/websocket.py").read_text()
mock = Path("tests/web/mock-ha.mjs").read_text()
client = Path("jarvis-web/src/lib/jarvisClient.ts").read_text()
for cmd in ("jarvis/sensors/readings", "jarvis/sky/summary", "jarvis/vision/still"):
    assert f"\"{cmd}\"" in ws, f"{cmd} is not in the websocket table"
    assert f"case \x27{cmd}\x27" in mock, f"the mock does not serve {cmd}"
    assert f"\x27{cmd}\x27" in client, f"the client never sends {cmd}"
print("three commands, three sides")
'
check "a still is a look: VisionManager.still rides the snapshot path (consent, rate limit, audit) and never touches disk" python3 -c '
from pathlib import Path
src = Path("jarvis-core/jarvis/integrations/vision/__init__.py").read_text()
i = src.index("    async def still(")
body = src[i: src.index("    async def _snapshot(", i)]
assert "self._snapshot(" in body, "still() does not go through the snapshot path"
assert "base64.b64encode(frame.data)" in body, "still() does not attach the frame"
assert "write_snapshot_sync" not in body, "still() writes to disk"
common = Path("jarvis-core/jarvis/api/common.py").read_text()
assert "manager.still(" in common and "configured" in common
print("still = snapshot + bytes")
'
check "the shipped House opens first and names no device nobody owns" python3 -c '
import sys, yaml
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.dashboards import clean_dashboard
raw = yaml.safe_load(open("jarvis-core/config/dashboards/house.yaml"))
board = clean_dashboard(raw)
assert board and board["id"] == "house" and raw.get("order", 100) == 0
kinds = {w["kind"] for w in board["widgets"]}
assert kinds >= {"entity", "readings", "camera", "sky", "moments"}, kinds
tiles = [w["entity"] for w in board["widgets"] if w["kind"] == "entity"]
assert tiles == ["sun.sun"], tiles
print("House first:", ", ".join(sorted(kinds)))
'
check "the mock opens on a House with one widget of every non-graph kind, a lit light to switch, and a camera that refuses" python3 -c '
import re
from pathlib import Path
src = Path("tests/web/mock-ha.mjs").read_text()
i = src.index("let dashboards = [")
first = src[i: src.index("id: \x27homelab\x27", i)]
assert "id: \x27house\x27" in first, "the House is not the first dashboard"
for kind in ("entity", "readings", "camera", "sky", "moments"):
    assert f"kind: \x27{kind}\x27" in first, f"the House has no {kind} widget"
assert "entity: \x27light.hall_lamp\x27" in first and "mkState(\x27light.hall_lamp\x27, \x27on\x27" in src
assert re.search(r"name: \x27Front Door\x27, consent: \x27never\x27", src), "no consent: never camera"
print("mock House: five kinds")
'
check "the menu inventory allows the one control at rest on an entity tile, and says why" python3 -c '
from pathlib import Path
doc = Path("docs/UI_MIGRATION.md").read_text()
row = next(l for l in doc.splitlines() if l.startswith("| DASHBOARDS | `/dashboards` |"))
cells = [c.strip() for c in row.strip("|").split("|")]
assert cells[3] == "1", f"per row at rest is {cells[3]!r}, not 1"
assert "switch" in cells[6] and "M63" in cells[6], "the notes do not say why"
print("per row at rest: 1 (the switch)")
'
check "the docs: the milestone, the plan, the changelog, the claims" bash -c '
grep -q "M63 — The dashboard shows the house" MILESTONES.md &&
grep -q "^| M63 |" docs/OVERHAUL_PLAN.md &&
grep -q "M63 — the dashboard shows the house" CHANGELOG.md &&
grep -q "### The dashboard shows the house (M63)" docs/verification.md &&
grep -q "### M63 — the dashboard shows the house" docs/UI_MIGRATION.md && echo "all five"'

use_venv
check_sh "jarvis-core: the layout, the kinds, layouts saved before kinds, the three commands, the refused still" \
    'cd jarvis-core && python3 -m pytest tests/test_dashboards.py tests/test_dashboard_widgets.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -1'
check "token lint: the console is clean" python3 scripts/verify/token_lint.py --require-clean jarvis-web/src
check "every screen is declared and uses ScreenState" python3 scripts/verify/web_states_check.py
check "no dead controls" node scripts/verify/web_dead_controls.mjs
ensure_web_deps
check_sh "svelte-check finds nothing" 'cd jarvis-web && npx svelte-check --threshold error 2>&1 | tail -1'
check_sh "the console's unit tests: the contract, the widgets' arithmetic, every kind rendered with its empty sentence" \
    'cd jarvis-web && npx vitest run src/lib/dashboards 2>&1 | tail -3'
# The e2e server serves the built console, so the build comes first — a spec
# run against a stale bundle measures the last change, not this one.
check_sh "the console builds" 'cd jarvis-web && npm run build 2>&1 | tail -2'
check_sh "in a browser: the House opens first; a tile's press is the Devices row's call_service and the tile follows the backend; a consent: never camera refuses; a moment lands live; the kind picker saves each kind" \
    'cd jarvis-web && E2E_PORT=$E2E_PORT npx playwright test dashboards.spec.ts 2>&1 | tail -3'
check_sh "the inventory holds against the House (one control per row at rest), and the dashboard's four states" \
    'cd jarvis-web && E2E_PORT=$E2E_PORT npx playwright test menus.spec.ts states.spec.ts -g "DASHBOARDS|Dashboards|inventory names" 2>&1 | tail -3'
check_sh "three pictures of the House, at three widths, regenerated" \
    'cd jarvis-web && UI_REVIEW=1 E2E_PORT=$E2E_PORT npx playwright test ui-review.spec.ts -g "Dashboards" 2>&1 | tail -2 && cd .. && test -f docs/ui-review/dashboards/desktop.png && test -f docs/ui-review/dashboards/tablet.png && test -f docs/ui-review/dashboards/mobile.png && echo "docs/ui-review/dashboards: desktop, tablet, mobile"'

verify_end
