#!/usr/bin/env bash
# M52 — VOICE: the graph and the living activity around the reactor.
#
# The voice tab is where the operator looks; before this milestone it showed
# the instrument, the exchange and the turn's stages, and nothing of what
# Jarvis was doing elsewhere — a task stepping, a sensor changing, a camera
# being looked at. Every check here is against the console's mock backend,
# which emits the same bus events the core does, plus one route pass against
# the real console. Fails first: nothing below exists yet.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M52" "the voice tab, alive"

require_file jarvis-web/src/routes/+page.svelte
require_file jarvis-web/src/lib/ui/Graph.svelte
require_file tests/web/mock-ha.mjs

# --- the surface ------------------------------------------------------------
# The components render the testids from props; the browser test below is
# what proves they are on the page. These two say the page asks for them.
check "the voice tab has the graph" grep -q 'testid="voice-graph"' jarvis-web/src/routes/+page.svelte
check "the voice tab has the activity strip" grep -q '<Activity rows=' jarvis-web/src/routes/+page.svelte
check "the activity strip is its own component, in the library" test -f jarvis-web/src/lib/ui/Activity.svelte
check "the activity feed is a store the phone can mirror" test -f jarvis-web/src/lib/activity.svelte.ts
check "every activity kind is a row kind the strip can draw" python3 -c '
import re
from pathlib import Path
store = Path("jarvis-web/src/lib/activity.svelte.ts").read_text()
kinds = set(re.findall(r"kind: \x27([a-z_]+)\x27", store)) | set(re.findall(r"\x27([a-z_]+)\x27", store[store.find("export type ActivityKind"):store.find(";", store.find("export type ActivityKind"))]))
needed = {"tool", "task", "sensor", "camera", "memory", "moment", "approval", "error"}
missing = needed - kinds
assert not missing, f"activity kinds missing: {sorted(missing)} (have {sorted(kinds)})"
print(f"{len(needed)} activity kinds")
'
check "the mock backend emits what the strip needs" python3 -c '
from pathlib import Path
mock = Path("tests/web/mock-ha.mjs").read_text()
for event in ("jarvis_notification", "state_changed", "jarvis_task_updated", "memory_changed", "vision_look"):
    assert event in mock, f"the mock never broadcasts {event}"
print("the mock speaks every event the strip reads")
'

# --- measured in the browser -----------------------------------------------
require_file jarvis-web/e2e/voice-live.spec.ts
ensure_web_build
check_sh "the graph and the activity strip, driven by events" \
    'cd jarvis-web && E2E_PORT=$E2E_PORT npx playwright test voice-live.spec.ts 2>&1 | tail -3'
check_sh "the voice tab still holds at five widths and in its four states" \
    'cd jarvis-web && E2E_PORT=$E2E_PORT npx playwright test home.spec.ts hud.spec.ts responsive.spec.ts states.spec.ts 2>&1 | tail -3'
check_sh "the look, measured on the voice tab" \
    'cd jarvis-web && E2E_PORT=$E2E_PORT npx playwright test look.spec.ts -g "Voice" 2>&1 | tail -3'
check "no value typed by hand" python3 scripts/verify/token_lint.py --require-clean jarvis-web/src
check_sh "the pictures, regenerated" \
    'cd jarvis-web && UI_REVIEW=1 E2E_PORT=$E2E_PORT npx playwright test ui-review.spec.ts -g "Voice" 2>&1 | tail -2'
check_sh "the voice tab opens in the real console with no console error" \
    'LIVE_CONSOLE_ROUTES=/ python3 testing/live/console_pass.py 2>&1 | tail -3'

verify_end
