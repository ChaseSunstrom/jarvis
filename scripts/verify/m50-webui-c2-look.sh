#!/usr/bin/env bash
# M50 — every page looks like C2, not only lints like it.
#
# M48 proved structure: tokens, states, breakpoints, four destinations. This
# proves the LOOK, which is a thing a browser can measure: what face the body
# text is set in, what is in the DOM, what radius a control has, what colour a
# panel renders. And it regenerates the pictures rather than trusting them.
source "$(dirname "$0")/lib.sh"
verify_begin "M50" "every page looks like C2"
use_venv

require_file docs/UI_MIGRATION.md
require_file jarvis-web/e2e/look.spec.ts
require_file jarvis-web/e2e/knowledge.spec.ts
require_file testing/live/console_pass.py

check "every M50 row in docs/UI_MIGRATION.md is ticked" python3 -c '
import re
from pathlib import Path
doc = Path("docs/UI_MIGRATION.md").read_text()
section = doc.split("### M50")[1].split("### M51")[0]
open_rows = re.findall(r"^- \[ \] (.*)$", section, re.M)
assert not open_rows, f"{len(open_rows)} unticked: " + "; ".join(r[:60] for r in open_rows[:8])
done = re.findall(r"^- \[x\] ", section, re.M)
print(f"{len(done)} rows ticked")
'

# --- the library grew, and is documented ------------------------------------
check "the components the pages needed exist, are exported and are documented" python3 -c '
from pathlib import Path
need = ("TopBar", "SectionStrip", "StatusReadout", "StagesBar", "CallLine", "DayStrip", "ProgressRing", "Figure", "Graph")
barrel = Path("jarvis-web/src/lib/ui/index.ts").read_text()
readme = Path("jarvis-web/src/lib/ui/README.md").read_text()
guide = Path("jarvis-web/src/routes/styleguide/+page.svelte").read_text()
ssr = Path("jarvis-web/src/lib/ui/ssr.test.ts").read_text()
for name in need:
    assert (Path("jarvis-web/src/lib/ui") / f"{name}.svelte").is_file(), f"no {name}.svelte"
    assert f"{name}.svelte" in barrel, f"{name} is not exported"
    assert f"## {name}" in readme, f"{name} has no README section"
    assert f"<{name}" in guide, f"{name} is not on the style guide"
    assert f"{name}:" in ssr, f"{name} has no SSR test props"
print(", ".join(need))
'

# --- nothing pre-C2 survives ------------------------------------------------
check "chrome.css has no grid, brackets or console pills/buttons" python3 -c '
from pathlib import Path
css = Path("jarvis-web/src/lib/styles/chrome.css").read_text()
for bad in (".jv-grid", ".jv-bracket", ".console .pill", ".console .btn", ".console button.btn", "grid-mask", "bracket-size"):
    assert bad not in css, f"chrome.css still has {bad}"
print("chrome.css is layout, not the previous direction")
'
check_not "no page or component draws the grid or the brackets" \
    grep -rqE "jv-grid|jv-bracket" jarvis-web/src --include=*.svelte --include=*.css --include=*.ts
check "pill radii only where a thing is round" python3 -c '
import re
from pathlib import Path
# Dots and rings are round. Controls, tags and inputs are not (Reactor II is 6px).
allowed = {"Toggle.svelte", "TopBar.svelte", "StatusReadout.svelte", "CallLine.svelte", "Reactor.svelte", "Tabs.svelte", "OfflineState.svelte", "base.css", "tokens.css"}
hits = []
for p in Path("jarvis-web/src").rglob("*"):
    if p.suffix not in (".svelte", ".css"):
        continue
    if p.name in allowed:
        continue
    for n, line in enumerate(p.read_text().splitlines(), 1):
        if "radius-pill" in line:
            hits.append(f"{p}:{n}")
assert not hits, "pill radius on a control: " + ", ".join(hits[:10])
print("only dots, rings and the toggle track are round")
'
check "body text is never mono: the console ground and the panels are Barlow" python3 -c '
from pathlib import Path
css = Path("jarvis-web/src/lib/styles/chrome.css").read_text()
base = Path("jarvis-web/src/lib/styles/base.css").read_text()
assert "font-family: var(--jv-font-body)" in base, "the ground is not Barlow"
# The console furniture may set mono only on data-shaped things: ids, code, pre, timings.
import re
mono_rules = re.findall(r"([^{}]+)\{[^{}]*--jv-font-chrome[^{}]*\}", css)
bad = [r.strip()[:60] for r in mono_rules if re.search(r"\.(lede|panel-head|empty|notice|muted|toolbar|btn|pill)\b|h1\b", r)]
assert not bad, "mono on prose: " + "; ".join(bad)
print(f"{len(mono_rules)} mono rules, all on data")
'
check "no text-shadow glow on wordmarks or titles" python3 -c '
import re
from pathlib import Path
hits = []
for p in Path("jarvis-web/src").rglob("*.svelte"):
    for n, line in enumerate(p.read_text().splitlines(), 1):
        if "text-shadow" in line:
            hits.append(f"{p.name}:{n}")
assert not hits, ", ".join(hits)
print("no glowing text")
'

# --- the system's own gates -------------------------------------------------
check "token lint: the console is clean" python3 scripts/verify/token_lint.py --require-clean jarvis-web/src
check "every screen is declared and uses ScreenState" python3 scripts/verify/web_states_check.py
check "no dead controls" node scripts/verify/web_dead_controls.mjs
check_sh "generated files current" 'python3 design/build.py --check 2>&1 | tail -2'
check_sh "the console's unit tests" 'cd jarvis-web && npx vitest run 2>&1 | tail -3'
check "the phone offers the same front doors as the browser" \
    python3 android-app/tools/console_parity_test.py

ensure_web_deps
ensure_web_build
run_playwright "the look, measured on every screen" look.spec.ts
run_playwright "the knowledge graph" knowledge.spec.ts
run_playwright "every screen, every state; nothing overflows; every control works" states.spec.ts responsive.spec.ts controls.spec.ts
run_playwright "the whole suite" 

# --- the pictures, regenerated here so they cannot be stale -----------------
check_sh "every screen at three widths, regenerated" \
    'cd jarvis-web && UI_REVIEW=1 E2E_PORT=$E2E_PORT npx playwright test ui-review.spec.ts 2>&1 | tail -3'
check "one folder per screen, three pictures each" python3 -c '
import re
from pathlib import Path
src = Path("jarvis-web/src/lib/screens.ts").read_text()
paths = [p for p in re.findall(r"path: \x27([^\x27]+)\x27", src) if "[" not in p]
missing = []
for path in paths:
    slug = "hud" if path == "/" else path.strip("/").replace("/", "-")
    for width in ("mobile", "tablet", "desktop"):
        if not (Path("docs/ui-review") / slug / f"{width}.png").is_file():
            missing.append(f"{slug}/{width}")
assert not missing, f"missing: {missing}"
print(f"{len(paths)} screens × 3 widths")
'
check_sh "the navigation and task recordings, regenerated" \
    'cd jarvis-web && E2E_PORT=$E2E_PORT npx playwright test motion-review.spec.ts 2>&1 | tail -3 && node scripts/collect-motion-review.mjs'

# --- and against the real console, on the real stack -----------------------
check_sh "every route opens in the real console with no console error and only token colours" \
    'python3 testing/live/console_pass.py 2>&1 | tail -6'
check_sh "the live smoke scenarios still pass" \
    'LIVE_ONLY=house-light-on,chat-context-retention,lock-needs-a-human bash scripts/verify/live_interaction.sh --implemented-only 2>&1 | tail -4'
verify_end
