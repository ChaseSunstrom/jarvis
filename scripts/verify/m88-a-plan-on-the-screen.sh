#!/usr/bin/env bash
# M88 — A plan on the screen.
#
# A background job with steps puts a `task` panel on the voice screen's
# surface while it runs — the steps, the current one live, a stop — and a
# `note` with its result when it is done, without anybody saying "show me
# the job". The server side follows the task events; the console draws the
# panel from the task record; the mock mirrors both for the e2e.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M88" "a plan on the screen"

check "the surface has a task kind, follows background jobs, and can be told not to" python3 -c '
from pathlib import Path
src = Path("jarvis-core/jarvis/integrations/surface/__init__.py").read_text()
assert "\"task\")" in src.split("KINDS = ", 1)[1].split("\n", 1)[0]
assert "async def async_follow_task" in src and "FOLLOWED_KINDS" in src
assert "plans" in src, "no surface: plans: switch"
print("kind task; follows background; surface: plans: false turns it off")
'
check_sh "the surface suite: a job with steps is a task panel while it runs and a note when done; no steps, another kind, an error leave nothing" \
    'cd jarvis-core && python3 -m pytest tests/test_surface.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -1'
check "the console draws a task panel from the task record, with a stop" python3 -c '
from pathlib import Path
panel = Path("jarvis-web/src/lib/components/SurfacePanel.svelte").read_text()
surface = Path("jarvis-web/src/lib/components/Surface.svelte").read_text()
assert "panel.kind === \x27task\x27" in panel and "TaskCard" in panel
assert "jarvis_task_updated" in surface and "getTask(" in surface
print("SurfacePanel: kind task -> TaskCard; Surface: getTask + jarvis_task_updated")
'
check "the mock follows a job the way the server does" bash -c 'grep -q "followTaskOnSurface" tests/web/mock-ha.mjs'
ensure_web_build
run_playwright "the surface specs: panels, layout, and the plan that follows a job" e2e/surface.spec.ts

use_venv
check "on the house, a background job with steps puts a task panel up, and its result a note" python3 -c '
import asyncio, json, os, time
import websockets
def token():
    for line in open("jarvis-core/.env"):
        if line.startswith("JARVIS_TOKEN="):
            return line.split("=", 1)[1].strip().strip(chr(34))
    return ""
url = os.environ.get("JARVIS_URL", "http://127.0.0.1:8080").replace("http", "ws", 1) + "/api/websocket"
async def cmd(ws, n, **fields):
    await ws.send(json.dumps({"id": n, **fields}))
    while True:
        m = json.loads(await ws.recv())
        if m.get("id") == n and m.get("type") == "result":
            return m
async def main():
    async with websockets.connect(url, max_size=None) as ws:
        await ws.recv(); await ws.send(json.dumps({"type": "auth", "access_token": token()}))
        assert json.loads(await ws.recv())["type"] == "auth_ok"
        started = await cmd(ws, 1, type="jarvis/tools/call", name="run_background_task", arguments={
            "description": "Go through every sensor in the house one at a time, work out which ones look wrong, and write it up.",
        })
        task_id = (started.get("result") or {}).get("task_id")
        assert task_id, started
        deadline = time.time() + 45
        seen = False
        while time.time() < deadline:
            listing = await cmd(ws, 2, type="jarvis/surface/list")
            panels = (listing.get("result") or {}).get("panels") or []
            if any(p.get("kind") == "task" and p.get("task") == task_id for p in panels):
                seen = True; print("task panel up for", task_id); break
            await asyncio.sleep(2)
        assert seen, "no task panel within 45 s: " + json.dumps(panels)[:300]
        deadline = time.time() + 360
        while time.time() < deadline:
            listing = await cmd(ws, 3, type="jarvis/surface/list")
            panels = (listing.get("result") or {}).get("panels") or []
            note = [p for p in panels if p.get("kind") == "note" and p.get("note") == f"task:{task_id}"]
            if note:
                print("note:", note[0]["title"][:80]); return
            await asyncio.sleep(5)
        raise SystemExit("the job did not end in a note within six minutes")
asyncio.run(main())
'

verify_end
