#!/usr/bin/env bash
# M84 — Jarvis volunteers a briefing.
#
# The integration was built and tested long before this milestone and never
# enabled on the house (the audit of 27 Aug 2026): no `briefing:` block, so no
# 07:00/22:00 digest and no `get_briefing` for "what's my briefing?". The
# checks here are the configuration, the suite, the rig's routing, and — on
# the running house — the tool's presence and one spoken briefing.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M84" "Jarvis volunteers a briefing"

check "configuration.yaml enables the briefing at 07:00 and 22:00 with the documented sections" python3 -c '
import yaml, re
from pathlib import Path
text = Path("jarvis-core/config/configuration.yaml").read_text()
loader = yaml.SafeLoader
loader.add_multi_constructor("!", lambda l, s, n: None)
cfg = yaml.load(text, Loader=loader)
b = cfg.get("briefing")
assert isinstance(b, dict), "no briefing: block"
assert b.get("morning") == "07:00" and b.get("evening") == "22:00", b
assert set(b.get("include") or []) == {"weather", "calendar", "tasks", "house", "unavailable_entities"}, b
print("briefing:", b["morning"], b["evening"], ", ".join(b["include"]))
'
check "the briefing needs llm and companion, and both are configured" python3 -c '
import re
from pathlib import Path
src = Path("jarvis-core/jarvis/integrations/briefing/__init__.py").read_text()
assert "DEPENDENCIES = [\"llm\", \"companion\"]" in src
cfg = Path("jarvis-core/config/configuration.yaml").read_text()
assert re.search(r"^llm:", cfg, re.M), "no llm: block"
print("depends on llm (configured) and companion (loaded by the platform)")
'
check_pytest "the briefing suite: sections, the empty case, delivery by presence, the length cap" 'cd jarvis-core && python3 -m pytest tests/test_features.py -q --timeout=120 --timeout-method=signal -k briefing'
check "the rig routes get_briefing to its own capability" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.capability import TOOL_CAPABILITY, capability_of
assert TOOL_CAPABILITY.get("get_briefing") == "briefing"
assert capability_of([], [], ["get_briefing"], "Good morning, Sir.") == "briefing"
print("get_briefing -> briefing")
'
check "the scenario exists, is gated on M84, and asks for the briefing in both variants" python3 -c '
import yaml
from pathlib import Path
s = yaml.safe_load(Path("testing/live/scenarios/briefing-on-demand.yaml").read_text())
assert s["gated-on"] == "M84" and s["capability"] == "briefing" and set(s["variants"]) == {"voice", "text"}
assert s["turns"][0]["expect"]["capability"] == "briefing"
print(s["name"], "-", s["turns"][0]["say"])
'

# On the running house (after the rebuild that loads the block).
use_venv
check "on the house, get_briefing is a registered tool" python3 -c '
import asyncio, json, os, sys
import websockets
def token():
    for line in open("jarvis-core/.env"):
        if line.startswith("JARVIS_TOKEN="):
            return line.split("=", 1)[1].strip().strip(chr(34))
    return ""
async def main():
    url = os.environ.get("JARVIS_URL", "http://127.0.0.1:8080").replace("http", "ws", 1) + "/api/websocket"
    async with websockets.connect(url, max_size=None) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": token()}))
        assert json.loads(await ws.recv())["type"] == "auth_ok", "not authenticated"
        await ws.send(json.dumps({"id": 1, "type": "jarvis/tools/list"}))
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == 1 and m.get("type") == "result":
                names = [t["name"] for t in m["result"]["tools"]]
                assert "get_briefing" in names, "get_briefing is not registered on the house (rebuild with the briefing: block)"
                print("get_briefing registered among", len(names), "tools"); return
asyncio.run(main())
'
check_sh "on the house, 'What is my briefing?' is one get_briefing call and one short digest, spoken and typed" \
    'LIVE_ONLY=briefing-on-demand bash scripts/verify/live_interaction.sh --full 2>&1 | tail -5'

verify_end
