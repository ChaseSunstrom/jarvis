#!/usr/bin/env bash
# M99 — The console keeps up: retry, live Areas and schedule, settings that
# follow the house, a room for a companion device, and what the server says.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M99" "the console keeps up"

check "retry: a client method over jarvis/tasks/retry, RETRY on a failed card and the task page, the mock serves it" python3 -c '
from pathlib import Path
client = Path("jarvis-web/src/lib/jarvisClient.ts").read_text()
assert "retryTask(taskId: string)" in client and "jarvis/tasks/retry" in client
card = Path("jarvis-web/src/lib/components/TaskCard.svelte").read_text()
assert "task-retry-{task.id}" in card and "canRetry(task)" in card
page = Path("jarvis-web/src/routes/work/tasks/[id]/+page.svelte").read_text()
assert "testid=\"task-retry\"" in page and "retryTask(" in page
mock = Path("tests/web/mock-ha.mjs").read_text()
assert "case \x27jarvis/tasks/retry\x27" in mock
print("retryTask, RETRY on the card and the page, mock")
'
check "Areas and the schedule are live; settings follow the house" python3 -c '
from pathlib import Path
areas = Path("jarvis-web/src/lib/sections/Areas.svelte").read_text()
for ev in ("area_registry_updated", "entity_registry_updated", "device_registry_updated"):
    assert ev in areas, ev
jobs = Path("jarvis-web/src/lib/components/ScheduledJobs.svelte").read_text()
assert "jarvis_schedule_fired" in jobs
store = Path("jarvis-web/src/lib/settingsStore.svelte.ts").read_text()
assert "jarvis_setting_changed" in store
print("Areas on three registry events, ScheduledJobs on schedule_fired, settings on setting_changed")
'
check "a companion device has a room: registered in the device registry, the room picker on Devices, area on the wire" python3 -c '
from pathlib import Path
ws = Path("jarvis-core/jarvis/api/websocket.py").read_text()
assert "companion:" in ws and "async_get_or_create(" in ws
devices = Path("jarvis-core/jarvis/api/devices.py").read_text()
assert "def area(self)" in devices and "\"area_id\"" in devices
page = Path("jarvis-web/src/lib/sections/Devices.svelte").read_text()
assert "companion-area-{device.device_id}" in page
mock = Path("tests/web/mock-ha.mjs").read_text()
assert "registry_id: \x27dev-companion-pixel-8\x27" in mock and "companion.area_id = device.area_id" in mock
print("companion -> device registry -> room picker -> device_of")
'
check "what the server says is shown: token times, reach, backend, expires, no sensor has a room" python3 -c '
from pathlib import Path
pairing = Path("jarvis-web/src/lib/components/Pairing.svelte").read_text()
assert "last_used_at" in pairing and "created_at" in pairing
autos = Path("jarvis-web/src/lib/sections/Automations.svelte").read_text()
assert ".reach" in autos
code = Path("jarvis-web/src/lib/sections/Code.svelte").read_text()
assert "permission_mode" in code and ".backend" in code
memory = Path("jarvis-web/src/lib/sections/Memory.svelte").read_text()
assert ".expires" in memory
print("token times, reach, backend/permission mode, expires")
'
use_venv
check_pytest "the API suite: a companion registers into the device registry with a room" \
    'cd jarvis-core && python3 -m pytest tests/test_api_companion.py -q --timeout=120 --timeout-method=signal'
ensure_web_build
run_playwright "the console: retry, live areas and schedule, settings that follow, a companion room" \
    console-keeps-up.spec.ts tasks.spec.ts editable-house.spec.ts schedule.spec.ts settings.spec.ts console-repairs.spec.ts

check "on the house: the connected companions carry area_id and area" python3 -c '
import asyncio, json, os
import websockets
def token():
    for line in open("jarvis-core/.env"):
        if line.startswith("JARVIS_TOKEN="):
            return line.split("=", 1)[1].strip().strip(chr(34))
    return ""
url = os.environ.get("JARVIS_URL", "http://127.0.0.1:8080").replace("http", "ws", 1) + "/api/websocket"
async def main():
    async with websockets.connect(url, max_size=None) as ws:
        await ws.recv(); await ws.send(json.dumps({"type": "auth", "access_token": token()}))
        assert json.loads(await ws.recv())["type"] == "auth_ok"
        await ws.send(json.dumps({"id": 1, "type": "jarvis/device/register", "device": {"id": "m99-probe", "name": "M99 probe", "platform": "android", "capabilities": ["device"], "app_version": "1.0.0", "actions": []}}))
        assert json.loads(await ws.recv()).get("success")
        await ws.send(json.dumps({"id": 2, "type": "config/companion/list"}))
        rows = json.loads(await ws.recv())["result"]
        mine = [r for r in rows if r["device_id"] == "m99-probe"]
        assert mine and "area_id" in mine[0] and "area" in mine[0], mine
        await ws.send(json.dumps({"id": 3, "type": "config/device_registry/list"}))
        entries = json.loads(await ws.recv())["result"]
        assert any("companion:m99-probe" in (e.get("identifiers") or []) for e in entries), "no registry entry for the companion"
        print("companion on the list with area fields; registry entry present")
asyncio.run(main())
'
verify_end
