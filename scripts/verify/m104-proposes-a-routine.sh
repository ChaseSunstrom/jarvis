#!/usr/bin/env bash
# M104 — Jarvis proposes a routine: the same thing at the same time on enough
# days becomes a card and a question; a yes makes the automation.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M104" "proposes a routine"

check "the routines integration: a miner over the recorder, a card and a question, accept through create_automation, never an unlock" python3 -c '
from pathlib import Path
r = Path("jarvis-core/jarvis/integrations/routines/__init__.py").read_text()
for s in ("def mine(", "states_between", "kind=\"proposal\"", "async_create_automation(", "name=\"proposed_routines\"", "DECLINE_SECONDS"):
    assert s in r, s
assert "(\"lock\", \"unlocked\")" not in r, "an unlock must never be proposed"
assert "\nroutines:\n  at:" in Path("jarvis-core/config/configuration.yaml").read_text()
assert "## `routines:`" in Path("jarvis-core/docs/configuration.md").read_text() and "## routines" in Path("jarvis-core/docs/features.md").read_text()
print("miner, card, question, accept, decline, tool; configured, documented")
'
use_venv
check_pytest "the routines suite" 'cd jarvis-core && python3 -m pytest tests/test_routines.py -q --timeout=120 --timeout-method=signal'
check "on the house: the miner runs on the real history, and a draft accepted over the websocket becomes a routine the house lists" python3 -c '
import asyncio, json, os, time
import websockets
def token():
    for line in open("jarvis-core/.env"):
        if line.startswith("JARVIS_TOKEN="):
            return line.split("=", 1)[1].strip().strip(chr(34))
    return ""
url = os.environ.get("JARVIS_URL", "http://127.0.0.1:8080").replace("http", "ws", 1) + "/api/websocket"
async def result(ws, n):
    deadline = time.time() + 60
    while time.time() < deadline:
        m = json.loads(await asyncio.wait_for(ws.recv(), 60))
        if m.get("id") == n and m.get("type") == "result":
            return m
    raise AssertionError("no result for %d" % n)
async def main():
    async with websockets.connect(url, max_size=None) as ws:
        await ws.recv(); await ws.send(json.dumps({"type": "auth", "access_token": token()}))
        assert json.loads(await ws.recv())["type"] == "auth_ok"
        await ws.send(json.dumps({"id": 1, "type": "call_service", "domain": "routines", "service": "propose", "service_data": {"ask": False}, "return_response": True}))
        mined = await result(ws, 1); assert mined.get("success"), mined
        answer = (mined["result"] or {}).get("response") or mined["result"]
        found = answer.get("candidates") if isinstance(answer, dict) else None
        assert isinstance(found, list), answer
        for c in found:
            assert c["service"].split(".", 1)[1] != "unlock", c
        await ws.send(json.dumps({"id": 2, "type": "call_service", "domain": "routines", "service": "accept", "service_data": {"draft": {"entity_id": "light.kitchen_lights", "state": "off", "at": "22:30"}}, "return_response": True}))
        made = await result(ws, 2); assert made.get("success"), made
        body = (made["result"] or {}).get("response") or made["result"]
        assert body.get("status") == "ok", body
        auto_id = str((body.get("automation") or {}).get("id") or "")
        assert auto_id, body
        await ws.send(json.dumps({"id": 3, "type": "config/automation/list"}))
        listed = await result(ws, 3)
        rows = listed["result"] if isinstance(listed["result"], list) else listed["result"].get("automations") or []
        assert any(str(r.get("id")) == auto_id for r in rows), "the routine is not listed"
        await ws.send(json.dumps({"id": 4, "type": "config/automation/delete", "automation_id": auto_id}))
        gone = await result(ws, 4); assert gone.get("success"), gone
        print("candidates on the house: %d; a draft became routine %s and was removed again" % (len(found), auto_id))
asyncio.run(main())
'
verify_end
