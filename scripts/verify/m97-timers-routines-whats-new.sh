#!/usr/bin/env bash
# M97 — Timers, routines read back, what's new.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M97" "timers, routines read back, what's new"

check "a routine authored by voice is read back, listed, and an authored tool is on the record at tier 2" python3 -c '
from pathlib import Path
tools = Path("jarvis-core/jarvis/llm/tools.py").read_text()
assert "\"readback\": readback" in tools and "name=\"list_automations\"" in tools
authored = Path("jarvis-core/jarvis/automation/authored.py").read_text()
assert "def describe(config" in authored
common = Path("jarvis-core/jarvis/api/common.py").read_text()
assert "spec[\"tier\"] = 2" in common and "note_capability(" in common
notes = Path("jarvis-core/jarvis/integrations/notifications/__init__.py").read_text()
assert "name=\"whats_new\"" in notes and "async def note_capability" in notes
assert "note_capability" in Path("jarvis-core/jarvis/integrations/mcp/__init__.py").read_text()
assert "note_capability" in Path("jarvis-core/jarvis/integrations/extensions/__init__.py").read_text()
print("readback, list_automations, tier 2, capability moments, whats_new")
'
check "the readback reads like a person would say it" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.automation.authored import describe
cfg = {"alias": "Kitchen at seven", "trigger": [{"platform": "time", "at": "07:00"}],
       "condition": [{"condition": "time", "weekday": ["mon", "tue", "wed", "thu", "fri"]}],
       "action": [{"service": "light.turn_on", "target": {"entity_id": "light.kitchen_lights"}}]}
assert describe(cfg) == "weekdays at 07:00: turn on light.kitchen_lights", describe(cfg)
print(describe(cfg))
'
check_pytest "the automation API suite" 'cd jarvis-core && python3 -m pytest tests/test_automation_api.py -q --timeout=120 --timeout-method=signal'
check_pytest "the create_tool suite" 'cd jarvis-core && python3 -m pytest tests/test_create_tool_handler.py -q --timeout=120 --timeout-method=signal'
check_pytest "the notifications suite (whats_new)" 'cd jarvis-core && python3 -m pytest tests/test_notifications.py -q --timeout=120 --timeout-method=signal'
check "a timer is an entity on the house's clock, with one tool and five services, enabled in the config" python3 -c '
from pathlib import Path
t = Path("jarvis-core/jarvis/integrations/timer/__init__.py").read_text()
assert "EntityPlatform(jarvis, DOMAIN, DOMAIN)" in t and "get_clock(self.jarvis)" in t
for s in ("start", "pause", "resume", "cancel", "snooze"):
    assert "jarvis.services.register(DOMAIN, \"" + s + "\"" in t, s
assert "name=\"timer\"" in t and "device_of(jarvis, context)" in t and "\"kind\": \"say\"" in t
assert "\ntimer: {}\n" in Path("jarvis-core/config/configuration.yaml").read_text()
assert "## `timer:`" in Path("jarvis-core/docs/configuration.md").read_text()
assert "## timer" in Path("jarvis-core/docs/features.md").read_text()
print("timer.<label>: five services, one tool, chimes where asked, configured, documented")
'
check_pytest "the timer suite" 'cd jarvis-core && python3 -m pytest tests/test_timer.py -q --timeout=120 --timeout-method=signal'
check "three scenarios, gated on M97" python3 -c '
import yaml
from pathlib import Path
for n in ("timer-by-voice", "routine-by-voice", "tool-authored-and-listed"):
    assert yaml.safe_load(Path(f"testing/live/scenarios/{n}.yaml").read_text())["gated-on"] == "M97"
print("timer-by-voice, routine-by-voice, tool-authored-and-listed")
'
check_sh "on the house: a routine by voice read back and listed; what is new" \
    'LIVE_ONLY=routine-by-voice,tool-authored-and-listed bash scripts/verify/live_interaction.sh --full 2>&1 | tail -6'

use_venv
check "on the house: a five-second timer becomes an entity, counts down, finishes, and leaves its card" python3 -c '
import asyncio, json, os, time
import websockets
def token():
    for line in open("jarvis-core/.env"):
        if line.startswith("JARVIS_TOKEN="):
            return line.split("=", 1)[1].strip().strip(chr(34))
    return ""
url = os.environ.get("JARVIS_URL", "http://127.0.0.1:8080").replace("http", "ws", 1) + "/api/websocket"
async def cmd(ws, n, payload):
    await ws.send(json.dumps({"id": n, "type": payload.pop("type"), **payload}))
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), 30))
        if m.get("id") == n and m.get("type") == "result":
            return m
async def main():
    async with websockets.connect(url, max_size=None) as ws:
        await ws.recv(); await ws.send(json.dumps({"type": "auth", "access_token": token()}))
        assert json.loads(await ws.recv())["type"] == "auth_ok"
        r = await cmd(ws, 1, {"type": "jarvis/tools/call", "name": "timer", "arguments": {"action": "start", "duration": "5s", "name": "gate probe"}})
        assert r.get("success") and r["result"]["result"]["status"] == "ok", r
        entity = r["result"]["result"]["timer"]["entity_id"]
        assert entity == "timer.gate_probe", entity
        r = await cmd(ws, 2, {"type": "get_states"})
        states = {s["entity_id"]: s for s in r["result"]}
        assert states[entity]["state"] == "active" and 0 < states[entity]["attributes"]["remaining"] <= 5, states.get(entity)
        deadline = time.time() + 20; final = None
        while time.time() < deadline:
            r = await cmd(ws, 3, {"type": "get_states"})
            final = {s["entity_id"]: s for s in r["result"]}.get(entity)
            if final and final["state"] == "finished": break
            await asyncio.sleep(1)
        assert final and final["state"] == "finished", final
        r = await cmd(ws, 4, {"type": "jarvis/notifications/list"})
        titles = [n.get("title") for n in r["result"]["notifications"]]
        assert any("gate probe timer is done" in str(t) for t in titles), titles[:5]
        print(entity, "active -> finished in <20 s; card left")
asyncio.run(main())
'
verify_end
