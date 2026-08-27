#!/usr/bin/env bash
# M92 — The house by voice, beyond lights.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M92" "the house by voice, beyond lights"

check "ten scenarios, gated on M92, cover climate, a cover, media, the vacuum, a fan and a switch, sensors, the moon, a note appended, a question answered, the surface" python3 -c '
import yaml
from pathlib import Path
names = ["house-climate", "house-cover", "house-media", "house-vacuum", "house-fan-and-switch", "sensors-compare", "sky-moon", "notes-append", "ask-which-light", "surface-by-voice"]
caps = set()
for n in names:
    s = yaml.safe_load(Path(f"testing/live/scenarios/{n}.yaml").read_text())
    assert s["gated-on"] == "M92", n
    caps.add(s["capability"])
assert caps == {"house", "sensors", "sky", "notes", "surface"}, caps
print(len(names), "scenarios;", ", ".join(sorted(caps)))
'
check "the rig routes show/move_panel/clear_screen to surface, and knows the surface expectation" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.capability import TOOL_CAPABILITY, capability_of
assert {TOOL_CAPABILITY[t] for t in ("show", "move_panel", "clear_screen")} == {"surface"}
assert capability_of([], [], ["show"], "Up it goes, Sir.") == "surface"
from testing.live import scenario
src = open(scenario.__file__).read()
assert "\"surface\"," in src
print("surface: three tools, one expectation")
'
check_pytest "the rig tests" 'python3 -m pytest testing/live/tests -q --timeout=120'
check_sh "on the house: the ten scenarios, spoken and typed" \
    'LIVE_ONLY=house-climate,house-cover,house-media,house-vacuum,house-fan-and-switch,sensors-compare,sky-moon,notes-append,ask-which-light,surface-by-voice bash scripts/verify/live_interaction.sh --full 2>&1 | tail -8'

verify_end
