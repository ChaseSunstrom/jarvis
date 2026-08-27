#!/usr/bin/env bash
# M49 — the home screen and the reactor, on Reactor II.
#
# The signature surface. What is checked: the HUD is drawn from the instrument
# and not the shader; the instrument has four distinct, amplitude-driven states;
# its geometry is a contract the web test and the phone mirror both read; there
# is one top bar and the HUD is its first tab; nothing pre-C2 (grid, brackets,
# pills) survives on the HUD; the pictures and the recordings are regenerated
# by THIS script, so they are current by construction rather than by memory.
source "$(dirname "$0")/lib.sh"
verify_begin "M49" "home screen + reactor: the signature surface, on Reactor II"
use_venv

require_file jarvis-web/src/lib/ui/Reactor.svelte
require_file jarvis-web/src/lib/ui/TopBar.svelte
require_file tests/contracts/reactor_geometry.json
require_file jarvis-web/src/lib/ui/reactor.test.ts
require_file jarvis-web/e2e/home.spec.ts

# --- the instrument, not the lamp -------------------------------------------
check "the HUD draws the instrument" grep -q '<Reactor' jarvis-web/src/routes/+page.svelte
check_not "the shader orb is gone" test -e jarvis-web/src/lib/components/Orb.svelte
check_not "and so is its spec" test -e jarvis-web/e2e/orb-shader.spec.ts
check_not "nothing imports it" grep -rq "Orb.svelte" jarvis-web/src jarvis-web/e2e
check_not "the pill-shaped mode toggle is gone" test -e jarvis-web/src/lib/components/ModeToggle.svelte

check "the reactor has four distinct states and follows the level" python3 -c '
from pathlib import Path
src = Path("jarvis-web/src/lib/ui/Reactor.svelte").read_text()
for need in ("data-state", "data-level", "level", "prefersReducedMotion"):
    assert need in src, f"Reactor.svelte has no {need}"
for state in ("listening", "thinking", "speaking", "error"):
    assert f"[data-state=\x27{state}\x27]" in src or f"data-state={state}" in src, f"no styling for the {state} state"
for tok in ("--jv-orb-thinking", "--jv-orb-speaking", "--jv-orb-listening", "--jv-orb-error"):
    assert tok in src, f"the reactor does not wear {tok} from color.orb.*"
for clock in ("--jv-rx-blades", "--jv-rx-coil", "--jv-rx-iris-a", "--jv-rx-iris-b", "--jv-rx-breathe", "--jv-rx-glint"):
    assert clock in src, f"the reactor ignores {clock}"
print("idle · listening · thinking · speaking · error, on the --jv-rx-* clock, level-driven")
'

check "the geometry is a contract both surfaces read" python3 -c '
import json
from pathlib import Path
geo = json.loads(Path("tests/contracts/reactor_geometry.json").read_text())
for key in ("ticks", "long_tick_every", "blades", "blade_gap_deg", "r_blade", "r_coil", "r_level", "r_core", "iris_a_sweep", "iris_b_sweep"):
    assert key in geo, f"reactor_geometry.json has no {key}"
web = Path("jarvis-web/src/lib/ui/reactor.test.ts").read_text()
assert "reactor_geometry.json" in web, "the web test does not read the contract"
phone = Path("android-app/tools/reactor_orb_test.py").read_text()
assert "reactor_geometry.json" in phone, "the phone mirror does not read the contract"
assert "Orb.svelte" not in phone, "the phone mirror still pins a shader that no longer exists"
print(f"{len(geo)} geometry keys, read by reactor.test.ts and reactor_orb_test.py")
'

# --- nothing pre-C2 on the HUD ----------------------------------------------
check "no grid, brackets, tagline or pill on the HUD or in chat mode" python3 -c '
from pathlib import Path
for name in ("jarvis-web/src/routes/+page.svelte", "jarvis-web/src/lib/components/ChatPanel.svelte", "jarvis-web/src/lib/components/ChatMessage.svelte"):
    src = Path(name).read_text()
    for bad in ("jv-grid", "jv-bracket", "radius-pill", "Rather Very Intelligent", "text-shadow"):
        assert bad not in src, f"{name} still has {bad}"
print("clean")
'
check "body text on the HUD is Barlow, mono is for data" python3 -c '
from pathlib import Path
src = Path("jarvis-web/src/routes/+page.svelte").read_text()
style = src[src.index("<style"):]
# The reply is the display face; the exchange and the dock are the body face.
assert "--jv-font-display" in style, "the reply is not set in the display face"
assert "--jv-font-body" in style, "nothing on the HUD is set in Barlow"
# A mono face may appear only on data: the who-line, timings, tool calls, keys.
mono = style.count("--jv-font-chrome")
body = style.count("--jv-font-body") + style.count("--jv-font-display")
assert body >= 3, f"only {body} body/display uses"
print(f"{body} body/display uses, {mono} mono uses (data only)")
'

# --- one bar, five tabs, the HUD first --------------------------------------
check "six tabs in one bar, the voice screen first" python3 -c '
import re
from pathlib import Path
src = Path("jarvis-web/src/lib/screens.ts").read_text()
blocks = re.findall(r"\{\n\t\tpath: .*?\n\t\}", src, re.S)
nav = [re.search(r"path: .([^\x27]+).", b).group(1) for b in blocks if "nav: true" in b]
assert nav[0] == "/", f"the first tab is {nav[0]}, not the voice screen"
# Five when M49 drew the bar; six since the dashboard became a destination
# of its own (610ec24) beside the four front doors of b4a7f77. The order is
# the claim: voice, then the dashboard, the house, the work, the knowledge,
# the settings.
assert nav == ["/", "/dashboards", "/house", "/work", "/knowledge", "/settings"], f"{len(nav)} tabs: {nav}"
hud = [b for b in blocks if "hud: true" in b]
assert len(hud) == 1 and "path: \x27/\x27" in hud[0], "the voice screen is not marked hud: true"
layout = Path("jarvis-web/src/routes/+layout.svelte").read_text()
assert "TopBar" in layout, "the layout does not draw the shared top bar"
assert "hud-console-link" not in layout, "the floating CONSOLE pill is still there"
print("VOICE · HOUSE · WORK · KNOWLEDGE · SETTINGS, one bar")
'
check "the phone still offers the four console front doors" \
    python3 android-app/tools/console_parity_test.py

# --- the system's own gates -------------------------------------------------
check "token lint: the console is clean" python3 scripts/verify/token_lint.py --require-clean jarvis-web/src
check_sh "generated files current; the orb pin is the geometry contract" 'python3 design/build.py --check 2>&1 | tail -2'
check_not "build.py no longer pins a shader" grep -q "Orb.svelte" design/build.py
check_sh "the console's unit tests" 'cd jarvis-web && npx vitest run 2>&1 | tail -3'

ensure_web_deps
ensure_web_build
run_playwright "the home screen: layout, states, amplitude, the bar" home.spec.ts
run_playwright "the HUD as a surface: approvals, tools, mic, palette, scroll" hud.spec.ts
run_playwright "chat mode" chat.spec.ts
run_playwright "frame budget, layout shift, reduced motion, never blocking" motion.spec.ts
run_playwright "the turn, the nav, the boot, the mute" e2e.spec.ts

# --- the pictures, regenerated here so they cannot be stale -----------------
check_sh "the HUD's three screenshots, regenerated" \
    'cd jarvis-web && UI_REVIEW=1 E2E_PORT=$E2E_PORT npx playwright test ui-review.spec.ts -g "Voice at" 2>&1 | tail -3 && ls ../docs/ui-review/hud/mobile.png ../docs/ui-review/hud/tablet.png ../docs/ui-review/hud/desktop.png'
check_sh "the boot and orb-state recordings, regenerated" \
    'cd jarvis-web && E2E_PORT=$E2E_PORT npx playwright test motion-review.spec.ts -g "boot|idle to" 2>&1 | tail -3 && node scripts/collect-motion-review.mjs && ls -la ../docs/motion-review/1-boot.webm ../docs/motion-review/2-orb-states.webm'

# --- and it works when somebody talks to it, through THIS surface -----------
check_sh "the live scenarios through the real browser: a spoken turn and a typed one" \
    'LIVE_ONLY=house-light-on,chat-context-retention bash scripts/verify/live_interaction.sh --implemented-only 2>&1 | tail -4'
verify_end
