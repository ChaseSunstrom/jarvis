#!/usr/bin/env bash
# M86 — Jarvis notices.
#
# The narrator was built with no shipped rules and never enabled on the house
# (the agentic audit, 27 Aug 2026). Now the house carries default rules — a way
# in opened, a lock unlocked, smoke or gas, a device gone unavailable — and a
# rule may OFFER what Jarvis could do about it: asked as a question with Yes
# and No through companion.ask, done only on the yes. The live half flips the
# demo lock and reads the narrator's own record back through `recent_events`.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M86" "Jarvis notices"

check "configuration.yaml enables the narrator with the default rules, and the lock and garage rules carry an offer" python3 -c '
import yaml
from pathlib import Path
loader = yaml.SafeLoader
loader.add_multi_constructor("!", lambda l, s, n: None)
cfg = yaml.load(Path("jarvis-core/config/configuration.yaml").read_text(), Loader=loader)
n = cfg.get("narrate")
assert n and n.get("enabled") is True and n.get("quiet_hours") == ["23:00", "07:00"], n
rules = n["rules"]
kinds = [r.get("device_class") or r.get("domains") for r in rules]
assert any("door" in (k or []) for k in kinds) and any("smoke" in (k or []) for k in kinds)
offers = [r["offer"]["service"] for r in rules if r.get("offer")]
assert offers == ["lock.lock", "cover.close_cover"], offers
assert all(r.get("quiet_hours") is False for r in rules if r.get("offer")), "an offer must not wait for morning"
print(len(rules), "rules;", "offers:", ", ".join(offers))
'
check "a rule parses its offer, and a malformed one is dropped" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.integrations.narrate import build_rule
r = build_rule({"domains": ["lock"], "on_state": "unlocked", "offer": {"service": "lock.lock", "question": "Shall I lock it?"}}, 0, {})
assert r.offer == {"service": "lock.lock", "question": "Shall I lock it?"}
assert build_rule({"domains": ["lock"], "offer": {"service": "nope"}}, 0, {}).offer is None
print("offer parsed")
'
check_pytest "the narrate suite: rules, ceilings, quiet hours, the offer asked and done only on a yes" 'cd jarvis-core && python3 -m pytest tests/test_sensors.py -q --timeout=120 --timeout-method=signal -k "narrat or offer or notice"'

use_venv
check "on the house, recent_events is a registered tool (the narrator is loaded)" python3 -c '
import asyncio, json, os
import websockets
def token():
    for line in open("jarvis-core/.env"):
        if line.startswith("JARVIS_TOKEN="):
            return line.split("=", 1)[1].strip().strip(chr(34))
    return ""
async def main():
    url = os.environ.get("JARVIS_URL", "http://127.0.0.1:8080").replace("http", "ws", 1) + "/api/websocket"
    async with websockets.connect(url, max_size=None) as ws:
        await ws.recv(); await ws.send(json.dumps({"type": "auth", "access_token": token()}))
        assert json.loads(await ws.recv())["type"] == "auth_ok"
        await ws.send(json.dumps({"id": 1, "type": "jarvis/tools/list"}))
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == 1 and m.get("type") == "result":
                names = [t["name"] for t in m["result"]["tools"]]
                assert "recent_events" in names, "recent_events is not registered (rebuild with the narrate: block)"
                print("recent_events among", len(names), "tools"); return
asyncio.run(main())
'
check "on the house, an unlocked demo lock is noticed: the narrator's record says so within ten seconds" python3 -c '
import asyncio, json, os, time
import httpx, websockets
def token():
    for line in open("jarvis-core/.env"):
        if line.startswith("JARVIS_TOKEN="):
            return line.split("=", 1)[1].strip().strip(chr(34))
    return ""
base = os.environ.get("JARVIS_URL", "http://127.0.0.1:8080")
H = {"Authorization": "Bearer " + token()}
lock = "lock.front_door_lock"
state = httpx.get(f"{base}/api/states/{lock}", headers=H, timeout=10).json()
before = state.get("state")
httpx.post(f"{base}/api/services/lock/unlock", headers=H, json={"entity_id": lock}, timeout=20)
async def main():
    url = base.replace("http", "ws", 1) + "/api/websocket"
    deadline = time.time() + 10
    while time.time() < deadline:
        async with websockets.connect(url, max_size=None) as ws:
            await ws.recv(); await ws.send(json.dumps({"type": "auth", "access_token": token()}))
            await ws.recv()
            await ws.send(json.dumps({"id": 1, "type": "jarvis/tools/call", "name": "recent_events", "arguments": {"minutes": 5}}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == 1 and m.get("type") == "result":
                    text = json.dumps(m.get("result"))
                    if "unlocked" in text.lower() and "front door" in text.lower():
                        print("noticed:", text[:160].replace(chr(10), " ")); return
                    break
        await asyncio.sleep(1)
    raise SystemExit("the narrator did not record the unlock within ten seconds")
try:
    asyncio.run(main())
finally:
    if before == "locked":
        httpx.post(f"{base}/api/services/lock/lock", headers=H, json={"entity_id": lock}, timeout=20)
'

verify_end
