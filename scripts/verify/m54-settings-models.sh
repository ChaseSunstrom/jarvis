#!/usr/bin/env bash
# M54 — settings that make sense, and the real models.
#
# Two claims. The MODELS panel lists what the model server ACTUALLY serves —
# the ids llama-swap (or vLLM, llama.cpp, Ollama) answers with, not the
# gateway's aliases — with each one's role, whether it is loaded, and which
# Jarvis job uses it; and a person can choose the chat, fast and vision model
# from that list. And SETTINGS is cut to what a person changes — Assistant ·
# Voice · House · Console · Tools — with plain labels, every old setting still
# reachable behind "everything".
source "$(dirname "$0")/lib.sh"
verify_begin "M54" "settings that make sense, and the real models"
use_venv

require_file jarvis-core/jarvis/llm/catalogue.py
require_file jarvis-core/tests/test_llm_catalogue.py
require_file jarvis-web/src/lib/components/Models.svelte
require_file jarvis-web/e2e/models.spec.ts
require_file jarvis-web/e2e/settings.spec.ts
require_file docs/UI_MIGRATION.md

check "every M54 row in docs/UI_MIGRATION.md is ticked" python3 -c '
import re
from pathlib import Path
doc = Path("docs/UI_MIGRATION.md").read_text()
assert "### M54" in doc, "docs/UI_MIGRATION.md has no M54 list"
section = re.split(r"^##+ ", doc.split("### M54")[1], maxsplit=1, flags=re.M)[0]
open_rows = re.findall(r"^- \[ \] (.*)$", section, re.M)
assert not open_rows, f"{len(open_rows)} unticked: " + "; ".join(r[:60] for r in open_rows[:8])
done = re.findall(r"^- \[x\] ", section, re.M)
assert len(done) >= 8, f"only {len(done)} rows — the move is not written down"
print(f"{len(done)} rows ticked")
'

# --- core: the command that reads the model server -------------------------
require_grep "jarvis/llm/models is a websocket command" '"jarvis/llm/models"' jarvis-core/jarvis/api/websocket.py
require_grep "GET /api/llm/models is a REST route" '"/llm/models"' jarvis-core/jarvis/api/rest.py
require_grep "llm.fast_model is an editable setting" 'key="llm.fast_model"' jarvis-core/jarvis/settings.py
require_grep "vision.model is an editable setting" 'key="vision.model"' jarvis-core/jarvis/settings.py
check "the catalogue resolves a gateway alias to the model behind it, and never guesses a parameter count" python3 -c '
from pathlib import Path
src = Path("jarvis-core/jarvis/llm/catalogue.py").read_text()
for need in ("/model/info", "/running", "status", "in_use_for", "loaded", "as named by the server"):
    assert need in src, f"catalogue.py never mentions {need!r}"
print("gateway, llama-swap and TEI shapes all handled")
'
check_pytest "core: the catalogue, the settings and the packaging pins" 'cd jarvis-core && python3 -m pytest tests/test_llm_catalogue.py tests/test_settings.py tests/test_settings_api.py tests/test_packaging.py tests/test_api.py -q --timeout=120 --timeout-method=signal'
check_not "no network in the catalogue tests" grep -nE "127\.0\.0\.1:(4000|8080|7997|7998)|tail05d9af" jarvis-core/tests/test_llm_catalogue.py

# --- the mock backend serves the same shape --------------------------------
require_grep "the mock answers jarvis/llm/models" "'jarvis/llm/models'" tests/web/mock-ha.mjs
require_grep "the mock serves GET /api/llm/models" "'/api/llm/models'" tests/web/mock-ha.mjs
check "the mock lists a realistic set: a chat, a fast, a vision, an embeddings and a rerank model" python3 -c '
import re
from pathlib import Path
src = Path("tests/web/mock-ha.mjs").read_text()
for role in ("chat", "fast", "vision", "embeddings", "rerank"):
    assert re.search(r"role: .%s." % role, src), f"the mock lists no {role} model"
for key in ("llm.fast_model", "vision.model", "voice.wake_word", "jarvis.unit_system", "jarvis.language"):
    assert f"key: \x27{key}\x27" in src, f"the mock has no {key} setting row"
print("five roles, and the settings the new sections feature")
'

# --- the information architecture, statically ------------------------------
check "SETTINGS has exactly six sections, in order: Assistant · Voice · House · Console · System · Tools" python3 -c '
import re
from pathlib import Path
src = Path("jarvis-web/src/lib/screens.ts").read_text()
blocks = re.findall(r"\{\n\t\tpath: .*?\n\t\}", src, re.S)
sections = [re.search(r"path: .([^\x27]+).", b).group(1) for b in blocks if "within: \x27/settings\x27" in b]
# System (M114: the .env catalogue) sits beside Console, the other "this installation" section; Tools stays last.
want = ["/settings/assistant", "/settings/voice", "/settings/house", "/settings/console", "/settings/system", "/settings/tools"]
assert sections == want, f"settings sections are {sections}"
for path in want:
    assert (Path("jarvis-web/src/routes") / path.lstrip("/") / "+page.svelte").is_file(), f"{path} has no page"
print(" · ".join(p.rsplit("/", 1)[-1] for p in want))
'
check "the desktop page moved into Console and still answers its old address" python3 -c '
from pathlib import Path
old = Path("jarvis-web/src/routes/settings/desktop/+page.ts")
assert old.is_file() and "/settings/console" in old.read_text(), "/settings/desktop does not redirect to /settings/console"
assert not Path("jarvis-web/src/routes/settings/desktop/+page.svelte").exists(), "two pages for the desktop"
assert not Path("jarvis-web/src/lib/sections/Desktop.svelte").exists(), "Desktop.svelte survives beside its replacement"
moved = Path("jarvis-web/src/lib/screens.ts").read_text()
assert "\x27/desktop\x27: \x27/settings/console\x27" in moved, "MOVED still sends /desktop to the old page"
print("/desktop → /settings/desktop → /settings/console")
'
check "the palette can find the new sections" python3 -c '
from pathlib import Path
src = Path("jarvis-web/src/lib/commandPalette.ts").read_text()
for path in ("/settings/voice", "/settings/house", "/settings/console"):
    assert f"\x27{path}\x27:" in src, f"{path} has no palette keywords"
assert "/settings/desktop" not in src, "the palette still indexes the page that moved"
print("voice, house, console indexed")
'
check "every plain row says why in one line" python3 -c '
from pathlib import Path
src = Path("jarvis-web/src/lib/sections/settingsPlan.ts").read_text()
import re
rows = re.findall(r"key: \x27([a-z_.]+)\x27,\s*label: \x27([^\x27]+)\x27,\s*why: \x27([^\x27]+)\x27", src)
assert len(rows) >= 8, f"only {len(rows)} featured rows"
for key, label, why in rows:
    assert "." not in label and "_" not in label, f"{key}: the label {label!r} is a key, not words"
    assert 12 <= len(why) <= 140, f"{key}: the why line is {len(why)} characters"
print(f"{len(rows)} featured rows, each with a why")
'

# --- the system's own gates -------------------------------------------------
check "token lint: the console is clean" python3 scripts/verify/token_lint.py --require-clean jarvis-web/src
check "every screen is declared and uses ScreenState" python3 scripts/verify/web_states_check.py
check "no dead controls" node scripts/verify/web_dead_controls.mjs
ensure_web_deps
check_sh "svelte-check finds nothing" 'cd jarvis-web && npx svelte-check --threshold error 2>&1 | tail -2'
check_sh "the console's unit tests" 'cd jarvis-web && npx vitest run 2>&1 | tail -3'
check "the phone offers the same front doors as the browser" \
    python3 android-app/tools/console_parity_test.py

ensure_web_build
run_playwright "the MODELS panel: rows from the server, a role choice writes the setting, four states" models.spec.ts
run_playwright "the six sections, plain rows, and every old setting behind everything" settings.spec.ts
run_playwright "the look, measured on the settings screens" 'look.spec.ts -g "Settings|Assistant|Voice settings|House settings|Console|Tools"'
run_playwright "the settings screens in every state, every control live, nothing overflowing" \
    'states.spec.ts controls.spec.ts responsive.spec.ts -g "Assistant|Voice settings|House settings|Console|Tools|overflows"'
run_playwright "what the rest of the suite says about settings" \
    'e2e.spec.ts console-repairs.spec.ts -g "settings|pair|voice|enrol|readable|number setting|bar links|console password|backend|dropped socket|phone width|fits a phone"'

# --- the pictures, regenerated here so they cannot be stale -----------------
check_sh "the settings screens at three widths, regenerated" \
    'cd jarvis-web && UI_REVIEW=1 E2E_PORT=$E2E_PORT npx playwright test ui-review.spec.ts -g "Settings|Assistant|Voice settings|House settings|Console|Tools" 2>&1 | tail -3'
check "one folder per settings screen, three pictures each, none for the page that moved" python3 -c '
from pathlib import Path
for slug in ("settings", "settings-assistant", "settings-voice", "settings-house", "settings-console", "settings-tools"):
    for width in ("mobile", "tablet", "desktop"):
        assert (Path("docs/ui-review") / slug / f"{width}.png").is_file(), f"missing {slug}/{width}.png"
assert not Path("docs/ui-review/settings-desktop").exists(), "docs/ui-review/settings-desktop is a picture of a page that no longer exists"
print("6 screens × 3 widths")
'

# --- and against the real console, on the real stack -----------------------
# The deployed console on :8199 is what people use. Not optional and not
# skippable: a stack that is not there is a failing check that says so.
#
# And never from a git worktree. The live checks belong to the main checkout
# — an agent's worktree re-created the house's containers from its own copy
# once, and the rig (`live_interaction.sh`, `testing/live/stack.py`) refuses
# from a worktree for that reason. This walk only opens a browser at :8199,
# but the rule is one rule: a worktree reports the check as not run, red,
# and the integrator runs it from `/opt/jarvis`. `.git` is a file in a
# worktree and a directory in the main checkout.
check_sh "the settings routes open in the real console with no console error and only token colours" \
    'if [ -f .git ] && [ -z "${JARVIS_ALLOW_WORKTREE_COMPOSE:-}" ]; then echo "refused from a git worktree — the live console pass runs from the main checkout"; exit 1; fi
     curl -fsS -m 3 http://127.0.0.1:8199/healthz > /dev/null || { echo "no stack on :8199 — the live check did not run (make up, then re-run)"; exit 1; }
     LIVE_CONSOLE_ROUTES=/settings,/settings/assistant python3 testing/live/console_pass.py 2>&1 | tail -6'
verify_end
