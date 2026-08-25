#!/usr/bin/env bash
# M46 — the management surface.
#
# The claim is that a switch on a page moves something in the running system.
# A page that lists what is installed and cannot change it is a report; a page
# that changes a list the model never reads is a filter. Both are easy to build
# and neither is this.
source "$(dirname "$0")/lib.sh"
verify_begin "M46" "the management surface: what is installed, and turning it off"
use_venv

require_file jarvis-core/jarvis/integrations/extensions/state.py
require_file jarvis-core/jarvis/integrations/extensions/scaffold.py
require_file jarvis-web/src/lib/components/Extensions.svelte
require_file jarvis-web/e2e/extensions.spec.ts

# --- the enforcement, against a real registry -------------------------------
check_sh "turning a plugin off takes its tools off the MODEL, not off a list" \
    'cd jarvis-core && python3 -m pytest tests/test_extensions.py -q --timeout=120 -k "disabling or revoking or survives or back_on or resurrect" 2>&1 | tail -2'

check "an edited permission scope withdraws exactly the tools that needed it" python3 -c '
import asyncio, sys, tempfile
sys.path.insert(0, "jarvis-core")
from jarvis.core import Jarvis
from jarvis.integrations.extensions import async_setup
from jarvis.integrations.plugins import PluginTool, ToolPlugin, get_registry
from jarvis.llm.tools import ToolRegistry

class Demo(ToolPlugin):
    domain = "demo"
    def tools(self):
        return [
            PluginTool("demo_read", "Reads.", {}, lambda **_: None, read_only=True),
            PluginTool("demo_write", "Writes.", {}, lambda **_: None),
        ]
    async def health(self):
        return {"ok": True}

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        jarvis = Jarvis(config_dir=tmp)
        jarvis.data["llm_tools"] = ToolRegistry(jarvis)
        plugin = Demo(jarvis)
        get_registry(jarvis).add(plugin)
        plugin.register()
        await async_setup(jarvis, None)
        tools = jarvis.data["llm_tools"]
        assert tools.get("demo_write") is not None
        await jarvis.services.async_call(
            "extensions", "set", {"key": "plugin:demo", "permissions": ["read_state"]},
            blocking=True, return_response=True)
        assert tools.get("demo_write") is None, "the writer survived losing `act`"
        assert tools.get("demo_read") is not None, "the reader went with it"
        await jarvis.services.async_call(
            "extensions", "set", {"key": "plugin:demo", "permissions": None},
            blocking=True, return_response=True)
        assert tools.get("demo_write") is not None, "it did not come back"
        print("act revoked: writer withdrawn, reader kept, both restored")
asyncio.run(main())
'

check "a decision outlives the process" python3 -c '
import asyncio, sys, tempfile
sys.path.insert(0, "jarvis-core")
from pathlib import Path
from jarvis.core import Jarvis
from jarvis.integrations.extensions import async_setup
from jarvis.integrations.skills import SkillStore

BUNDLED = Path("jarvis-core/jarvis/integrations/skills/bundled")

async def boot(tmp):
    jarvis = Jarvis(config_dir=tmp)
    store = SkillStore(Path(tmp) / "skills", bundled_root=BUNDLED)
    store.load()
    jarvis.data["skills"] = store
    await async_setup(jarvis, None)
    return jarvis, store

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        jarvis, store = await boot(tmp)
        await jarvis.services.async_call(
            "extensions", "set", {"key": "skill:diary", "enabled": False},
            blocking=True, return_response=True)
        assert "diary" not in store.skills
        again, store2 = await boot(tmp)
        assert "diary" not in store2.skills, "it came back after a restart"
        assert "note-taking" in store2.skills, "the others went with it"
        print("one skill off, still off after a restart, the rest untouched")
asyncio.run(main())
'

# --- guided creation --------------------------------------------------------
check "a skill can be created without anybody writing YAML, and it validates" python3 -c '
import sys, tempfile
from pathlib import Path
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.extensions.registry import skill_manifest
from jarvis.integrations.extensions.scaffold import ScaffoldError, scaffold_skill
from jarvis.integrations.skills import parse_skill_md

with tempfile.TemporaryDirectory() as tmp:
    path = scaffold_skill(Path(tmp), name="bin-day", description="Which bin, which night.",
                          tools=["get_state", "web_search"])
    manifest = skill_manifest(parse_skill_md(path.read_text(), path))
    assert "network" in manifest.permissions, manifest.permissions
    assert not manifest.under_declared()
    refused = 0
    for bad in ("../escape", "Bin Day", "", "x", "bin-day"):
        try:
            scaffold_skill(Path(tmp), name=bad, description="x")
        except ScaffoldError:
            refused += 1
    assert refused == 5, f"only {refused} of 5 bad names refused"
    print("scaffolded, validates, and 5 bad names refused (traversal, case, empty, short, clash)")
'

# Behaviour, not a grep: the first version of this check looked for
# `replace(` and failed on the line that turns "bin-day" into a "Bin Day"
# heading, which is a false alarm that teaches somebody to delete a check.
check "a name is refused rather than corrected into a different one" python3 -c '
import sys, tempfile
from pathlib import Path
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.extensions.scaffold import ScaffoldError, scaffold_skill

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    path = scaffold_skill(root, name="bin-day", description="x")
    assert path.parent.name == "bin-day", path
    for nearly in ("Bin_Day", "bin day", "bin/day", "../bin-day"):
        try:
            scaffold_skill(root, name=nearly, description="x")
        except ScaffoldError:
            continue
        raise SystemExit(f"{nearly!r} was accepted and became something else")
    made = sorted(p.name for p in root.iterdir())
    assert made == ["bin-day"], f"something other than the name asked for: {made}"
print("one folder, named exactly what was asked for; 4 near-misses refused")
'

# --- the surface ------------------------------------------------------------
check "the section is on an existing page, not an eleventh tab" python3 -c '
import json
from pathlib import Path
inventory = Path("jarvis-web/src/lib/screens.ts").read_text()
nav = inventory.count("nav: true")
assert nav <= 11, f"{nav} top-level destinations; M48 is reducing these, not adding to them"
page = Path("jarvis-web/src/lib/sections/Tools.svelte").read_text()
assert "Extensions" in page, "the section is not mounted anywhere"
assert not Path("jarvis-web/src/routes/extensions").exists(), "it took a route of its own"
print(f"mounted on /tools; {nav} top-level destinations, unchanged")
'

check "every state the section can be in is a real state" python3 -c '
from pathlib import Path
src = Path("jarvis-web/src/lib/components/Extensions.svelte").read_text()
for needed, why in (
    ("SkeletonRows", "loading"),
    ("EmptyState", "empty"),
    ("extensions-error", "error"),
    ("ext-rejected-", "a manifest that would not load"),
):
    assert needed in src, f"no {why} state"
print("loading, empty, error and rejected-manifest all rendered")
'

check_sh "the surface, in a browser" \
    'cd jarvis-web && E2E_PORT=${E2E_PORT:-8299} npx playwright test e2e/extensions.spec.ts 2>&1 | tail -2'

check "no hard-coded style value in any of it" \
    python3 scripts/verify/token_lint.py

# --- against the real containers --------------------------------------------
# `gated-on: M46`, so it needs --full. The scenario itself says what it can and
# cannot show: no ToolPlugin is configured in a default deployment, so the
# tool-withdrawal half is the pytest above and the skill half is this.
if [ "${M46_LIVE:-1}" = "1" ] && docker compose -f jarvis-core/docker-compose.yml ps jarvis-core 2>/dev/null | grep -q healthy; then
    # This scenario's own result, not the runner's exit code. The runner also
    # runs `stack-logs-clean`, which is M29's whole-stack check and is
    # currently red on this box for a reason that has nothing to do with M46:
    # jarvis-core makes an UNAUTHENTICATED `GET /v1/models` to the gateway
    # every thirty seconds, and the 401 it earns is an ERROR-level line in the
    # gateway's log. Recorded against M29; failing M46 for it would be failing
    # the wrong milestone, and passing the whole runner would be claiming a
    # stack this box does not have.
    check_sh "a switch flipped mid-conversation reaches the running Jarvis" \
        'set -a; . ./.env >/dev/null 2>&1; set +a; timeout 1800 python3 -m testing.live.runner --full --target stack --only extensions-toggle-enforced --no-browser 2>&1 | grep -v pthread_setaffinity > /tmp/m46-live.log; grep -qE "^  ok   extensions-toggle-enforced" /tmp/m46-live.log || { grep -A6 "extensions-toggle" /tmp/m46-live.log; exit 1; }; grep -E "^  ok   extensions-toggle|^live: " /tmp/m46-live.log | tail -2'
else
    check "the live scenario is written and parses, even with no stack up" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.scenario import load_scenario
s = load_scenario("testing/live/scenarios/extensions-toggle-enforced.yaml")
assert s.gated_on == "M46" and len(s.turns) == 3
print(f"{s.name}: {len(s.turns)} turns, needs a stack (M46_LIVE=1 with one up)")
'
fi

verify_end
