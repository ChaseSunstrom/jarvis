#!/usr/bin/env bash
# M58 — the sky: the next ISS pass for the house, what is overhead now, the
# moon's phase, the planets tonight. Computed here with skyfield from cached
# orbital elements and a cached ephemeris, so it keeps answering offline and
# says how old the elements are.
#
# This gate is green on unit tests alone, on purpose. Everything below runs
# against a fixed element set (a real ISS TLE from 2026-08-25), a three-month
# excerpt of de421, a frozen clock and London's coordinates — no network, no
# stack. What it cannot prove is that the running Jarvis, asked out loud, picks
# the tool and answers with a time and a direction. That is the live scenario
# `sky-iss-pass` (gated on this milestone), and it is a separate line the
# integrator runs against the stack, never from this script:
#
#     LIVE_CAPABILITY=sky bash scripts/verify/live_interaction.sh --full
#
# It is not folded in here because this rig has no skip state: a check that
# needs containers this host may not have would be a red line on every box
# without them, and a gate that is red for reasons unrelated to the work stops
# saying anything about the work.
source "$(dirname "$0")/lib.sh"
verify_begin "M58" "the sky: ISS passes, what is overhead, the moon and the planets — offline"
use_venv

require_file jarvis-core/jarvis/integrations/sky/__init__.py
require_file jarvis-core/tests/test_sky.py
require_file jarvis-core/tests/fixtures/tle/iss.csv
require_file jarvis-core/tests/fixtures/tle/iss.tle
require_file jarvis-core/tests/fixtures/ephemeris/de421-2026q3.bsp
require_file jarvis-core/config/examples/sky.yaml
require_file testing/live/scenarios/sky-iss-pass.yaml

# One minor release, not a range: the almanac API this leans on (find_risings /
# find_settings with a horizon, EarthSatellite.from_omm) moved between minor
# versions, and an image rebuilt on a different one would compute different
# answers to the tests below without anything having changed in this tree.
# Written as `>=X.Y,<X.Y+1` because test_packaging requires both bounds on
# every line; this check reads it back as the pin it is.
check "skyfield is pinned to one minor release in jarvis-core/requirements.txt" python3 -c '
import re
from pathlib import Path
line = re.search(r"^skyfield(.*)$", Path("jarvis-core/requirements.txt").read_text(), re.M)
assert line, "skyfield is not in requirements.txt"
m = re.fullmatch(r">=(\d+)\.(\d+),<(\d+)\.(\d+)", line.group(1).strip())
assert m, f"not a one-minor pin: skyfield{line.group(1)}"
lo_major, lo_minor, hi_major, hi_minor = map(int, m.groups())
assert (hi_major, hi_minor) == (lo_major, lo_minor + 1), f"wider than one minor: skyfield{line.group(1)}"
print(f"skyfield {lo_major}.{lo_minor} and nothing else")
'
check "the venv has the pinned skyfield, and its timescale loads without the network" python3 -c '
import re
from pathlib import Path
import skyfield
want = re.search(r"^skyfield>=([0-9.]+),", Path("jarvis-core/requirements.txt").read_text(), re.M).group(1)
assert skyfield.__version__.startswith(want), f"venv has skyfield {skyfield.__version__}, requirements pin {want}"
from skyfield.api import load
ts = load.timescale(builtin=True)
print(f"skyfield {skyfield.__version__}, builtin timescale, {ts.now().utc_iso()}")
'

# The fixture element set is a real one, and it says when it is from: an OMM
# row whose epoch is not in 2026 is not the fixture the tests describe. CSV is
# what is fetched (the catalogue passed six digits in July 2026, which a TLE
# cannot carry); the TLE beside it is the same set for the hand-typed fallback.
check "the ISS fixture is a real 2026 element set, as OMM CSV with a TLE twin" python3 -c '
import csv, io
from pathlib import Path
rows = list(csv.DictReader(io.StringIO(Path("jarvis-core/tests/fixtures/tle/iss.csv").read_text())))
assert len(rows) == 1 and rows[0]["OBJECT_NAME"] == "ISS (ZARYA)", rows
assert rows[0]["NORAD_CAT_ID"] == "25544" and rows[0]["EPOCH"].startswith("2026-"), rows[0]["EPOCH"]
lines = [l.strip() for l in Path("jarvis-core/tests/fixtures/tle/iss.tle").read_text().splitlines() if l.strip()]
assert lines[0].startswith("ISS (ZARYA)") and lines[1].startswith("1 25544U") and lines[1][18:20] == "26", lines
epoch = rows[0]["EPOCH"]
print(f"ISS (ZARYA), OMM epoch {epoch}; TLE epoch day {lines[1][20:32].strip()} of 2026")
'

# The tools, as the model sees them: four names, tier 1, read-only — so they
# keep running after a turn has read a hostile page, which is the whole point
# of read_only in the taint gate.
check "the sky integration registers its four tools at tier 1, read-only" python3 -c '
import asyncio, sys, tempfile
sys.path.insert(0, "jarvis-core")
from jarvis.core import Jarvis
from jarvis.llm.tools import TIER_DIRECT, ToolRegistry
from jarvis.integrations import sky

async def main():
    with tempfile.TemporaryDirectory() as d:
        j = Jarvis(d)
        j.config = {"jarvis": {"latitude": 51.5072, "longitude": -0.1276,
                               "elevation": 11, "time_zone": "Europe/London"}}
        j.data["llm_tools"] = ToolRegistry(j)
        await j.async_start()
        ok = await sky.async_setup(j, {
            "download": False,
            "tle_cache": "jarvis-core/tests/fixtures/tle",
            "ephemeris": "jarvis-core/tests/fixtures/ephemeris/de421-2026q3.bsp",
        })
        assert ok is True
        reg = j.data["llm_tools"]
        for name in ("next_pass", "overhead_now", "moon_phase", "planets_tonight"):
            tool = reg.get(name)
            assert tool is not None, f"{name} is not registered"
            assert tool.tier == TIER_DIRECT, f"{name} is tier {tool.tier}"
            assert tool.read_only, f"{name} is not read-only"
        ids = sorted(s.entity_id for s in j.states.all() if s.entity_id.startswith("sky."))
        assert "sky.iss_next_pass" in ids and "sky.moon" in ids, ids
        await j.async_stop()
        print("next_pass, overhead_now, moon_phase, planets_tonight at tier 1, read-only; " + ", ".join(ids))
asyncio.run(main())
'

check "nothing in the sky integration fetches at import or setup by default in tests" python3 -c '
from pathlib import Path
text = Path("jarvis-core/tests/test_sky.py").read_text()
assert "AsyncClient" in text and "monkeypatch" in text, "the tests do not pin the network shut"
print("httpx.AsyncClient is replaced for the whole module")
'

check_pytest "the sky tests" 'cd jarvis-core && python3 -m pytest tests/test_sky.py -q \
        --timeout=120 --timeout-method=signal'
check_pytest "packaging: the example parses and every shipped option is read" 'cd jarvis-core && python3 -m pytest tests/test_packaging.py -q \
        --timeout=120 --timeout-method=signal'

# Written against the target state, gated on this milestone: the router must
# say `sky`, and the reply must name a time and a direction.
check "the live scenario parses, is gated on M58, and asks for the sky" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.scenario import load_scenario
s = load_scenario("testing/live/scenarios/sky-iss-pass.yaml")
assert s.capability == "sky", s.capability
assert s.gated_on == "M58", s.gated_on
assert set(s.variants) == {"voice", "text"}, s.variants
expect = s.turns[0].expect
assert expect.get("capability") == "sky", expect
assert "reply_means" in expect, expect
print(f"{s.name}: {len(s.turns)} turn(s), variants {list(s.variants)}, gated on {s.gated_on}")
'
check "the router knows the four sky tools as one capability" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.capability import TOOL_CAPABILITY, capability_of
for tool in ("next_pass", "overhead_now", "moon_phase", "planets_tonight"):
    assert TOOL_CAPABILITY.get(tool) == "sky", f"{tool} -> {TOOL_CAPABILITY.get(tool)}"
assert capability_of([], [], ["next_pass"], "") == "sky"
assert capability_of([], [], ["get_state", "moon_phase"], "") == "sky"
print("next_pass / overhead_now / moon_phase / planets_tonight route to sky")
'

check "ruff is clean on the sky integration and its tests" \
    python3 -m ruff check jarvis-core/jarvis/integrations/sky jarvis-core/tests/test_sky.py
check_not "no mutation-stub marker in the sky tree (CI's static job, mirrored)" \
    grep -rnIiE '\bM[U]TANT\b|\bDELIBERATELY BR[O]KEN\b' jarvis-core/jarvis/integrations/sky jarvis-core/tests/test_sky.py

check "the sky integration is switched on in the deployed config, with its note" python3 -c '
from pathlib import Path
text = Path("jarvis-core/config/configuration.yaml").read_text()
assert "\nsky:\n" in text, "sky: is not in the deployed configuration.yaml — an integration verified only against the harness config is one the house never had (see the notes and notifications blocks)"
i = text.index("\nsky:\n")
note = text[max(0, i - 900):i]
assert "M58" in note and "CelesTrak" in note, "the sky block has no note saying what it is and where the data comes from"
print("sky: on, with its note")
'
check "the changelog and the claims register name M58" bash -c \
    'grep -q "M58" CHANGELOG.md && grep -q "(M58)" docs/verification.md'

verify_end
