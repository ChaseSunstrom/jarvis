#!/usr/bin/env bash
# M83 — Pull things up.
#
# "have jarvis able to pull things up and display them on the voice screen,
# kind of like iron man, and able to move things around". A `show` tool puts a
# thing on the surface beside the instrument; the console draws it live from
# the house, a person drags it, the server keeps where it landed.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M83" "pull things up"

check "the surface integration: one store per house, an event on every change" bash -c 'grep -q "class Surface" jarvis-core/jarvis/integrations/surface/__init__.py && grep -q "EVENT_SURFACE_CHANGED = \"jarvis_surface_changed\"" jarvis-core/jarvis/integrations/surface/__init__.py'
check "three tools, all Tier 1: show, clear_screen, move_panel" bash -c '[ $(grep -cE "name=\"(show|clear_screen|move_panel)\"" jarvis-core/jarvis/integrations/surface/__init__.py) -eq 3 ]'
check "show resolves an entity through the same resolver every house tool uses" grep -q "resolve_entities(" jarvis-core/jarvis/integrations/surface/__init__.py
check "five websocket commands" bash -c '[ $(grep -c "jarvis/surface/" jarvis-core/jarvis/api/websocket.py) -ge 5 ]'
check "the config enables it" grep -q "^surface:" jarvis-core/config/configuration.yaml
check "the voice page mounts the surface over the stage" grep -q "<Surface {conn} />" jarvis-web/src/routes/+page.svelte
check "a panel is the dashboard's own widget for its kind (never a copy of the house)" bash -c 'grep -q "EntityTile" jarvis-web/src/lib/components/SurfacePanel.svelte && grep -q "CameraStillView" jarvis-web/src/lib/components/SurfacePanel.svelte && grep -q "Readings" jarvis-web/src/lib/components/SurfacePanel.svelte'
check "the mock serves the five commands and the show hook" bash -c '[ $(grep -c "case '"'"'jarvis/surface/" tests/web/mock-ha.mjs) -eq 5 ] && grep -q "jarvis/test/surface_show" tests/web/mock-ha.mjs'
check_pytest "the surface suite: slots, one panel per thing, the oldest makes room, the tools, a clamped drag" 'cd jarvis-core && python3 -m pytest tests/test_surface.py -q --timeout=120 --timeout-method=signal'
check_sh "no new hard-coded value in the two components" 'python3 scripts/verify/token_lint.py 2>&1 | tail -1'
ensure_web_deps
ensure_web_build
run_playwright "a shown entity and camera appear beside the instrument and leave on ×; a drag lands on the grid and the server is told" 'e2e/surface.spec.ts'
run_playwright "the voice page still holds its shape with the surface on it" 'e2e/voice-layout.spec.ts e2e/look.spec.ts e2e/states.spec.ts'
check "on the running house, show puts a panel up and the surface lists it (rebuilt after M83)" bash -c '
TOKEN=$(grep "^JARVIS_TOKEN=" jarvis-core/.env | cut -d= -f2-)
python3 - "$TOKEN" <<'"'"'PY'"'"'
import asyncio, json, sys
import websockets
token = sys.argv[1]
async def main():
    async with websockets.connect("ws://127.0.0.1:8080/api/websocket", max_size=2**22) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": token})); await ws.recv()
        await ws.send(json.dumps({"id": 1, "type": "jarvis/surface/clear"})); await ws.recv()
        await ws.send(json.dumps({"id": 2, "type": "jarvis/tools/call", "name": "show", "arguments": {"what": "the sky", "kind": "sky"}}))
        shown = json.loads(await ws.recv())
        await ws.send(json.dumps({"id": 3, "type": "jarvis/surface/list"}))
        listed = json.loads(await ws.recv())
        panels = (listed.get("result") or {}).get("panels") or []
        print("shown:", json.dumps(shown.get("result") or shown)[:160]); print("panels:", [p["kind"] for p in panels])
        assert any(p["kind"] == "sky" for p in panels), listed
        await ws.send(json.dumps({"id": 4, "type": "jarvis/surface/clear"})); await ws.recv()
asyncio.run(main())
PY'

verify_end
