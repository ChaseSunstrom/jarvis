#!/usr/bin/env bash
# M69 — The house is editable by voice.
#
# "Can you remove all of the elements of the house?" — "I have no tool for
# deleting entities." Now `remove_entities` and `remove_device` exist, Tier 3
# with the targets pinned into the approval as `lock_control` pins its doors,
# running the ONE delete path the console's Devices screen also runs
# (`Jarvis.async_remove_entity` behind `config/entity_registry/remove`); "all
# of the elements" is refused with a sentence before anything is held; and a
# removed thing leaves the state machine, the registry (so the exposure list
# and the house summary), the Devices rows and the dashboard tile, live.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M69" "the house, editable by voice"
use_venv || true

require_file jarvis-core/tests/test_entity_remove.py
require_file jarvis-web/e2e/editable-house.spec.ts

# --- the tools -------------------------------------------------------------
check "remove_entities and remove_device are Tier 3, pinned, and carry a refusal check; list_devices is read-only" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.llm.tools import TIER_APPROVAL, ToolRegistry, register_builtin_tools
r = ToolRegistry(None); register_builtin_tools(r)
for name in ("remove_entities", "remove_device"):
    tool = r.get(name); assert tool is not None, f"no {name}"
    assert tool.tier == TIER_APPROVAL, f"{name} is tier {tool.tier}"
    assert tool.pin is not None, f"{name} pins nothing"
    assert tool.refuse is not None, f"{name} refuses nothing"
assert r.is_read_only(r.get("list_devices")), "list_devices is not read-only"
print("remove_entities 3 (pinned, refusing), remove_device 3 (pinned, refusing), list_devices read-only")
'
check "\"all of the elements\" is refused with a sentence, and nothing reaches a consent surface" python3 -c '
import asyncio, sys, tempfile; sys.path.insert(0, "jarvis-core")
from jarvis.core import Jarvis
from jarvis.llm.tools import EVENT_APPROVAL_REQUIRED, ToolRegistry, register_builtin_tools
async def main():
    with tempfile.TemporaryDirectory() as d:
        jarvis = Jarvis(d); await jarvis.async_setup({})
        held = []; jarvis.bus.listen(EVENT_APPROVAL_REQUIRED, lambda e: held.append(e.data))
        entry = await jarvis.entities.async_get_or_create("light", "demo", "u1", "old_lamp", name="Old Lamp")
        jarvis.states.set(entry.entity_id, "on")
        r = ToolRegistry(jarvis); register_builtin_tools(r)
        for args in ({"name": "everything"}, {"entity_ids": ["*"]}, {}):
            out = await r.call("remove_entities", args, None)
            assert out["status"] == "error" and "list_entities" in out["error"], (args, out)
        assert held == [], "a wildcard removal was held for a human"
        named = await r.call("remove_entities", {"entity_ids": ["light.old_lamp"]}, None)
        assert named["status"] == "approval_required" and named["arguments"] == {"entity_ids": ["light.old_lamp"]}, named
        done = await r.approve_request(named["request_id"], True)
        assert done["result"]["removed"] == ["light.old_lamp"], done
        assert jarvis.states.get("light.old_lamp") is None and jarvis.entities.get("light.old_lamp") is None
        await jarvis.async_stop()
        print("refused: everything, *, nothing named; held and removed: light.old_lamp")
asyncio.run(main())
'
check "the tier table names both tools (no service twin: an automation has no verb for it)" bash -c 'grep -q "\"remove_entities\": None" jarvis-core/tests/test_gated_services.py && grep -q "\"remove_device\": None" jarvis-core/tests/test_gated_services.py'

# --- one delete path -------------------------------------------------------
check "the tool, the websocket command and the REST twin all run Jarvis.async_remove_entity" bash -c 'grep -q "await jarvis.async_remove_entity(entity_id, ctx)" jarvis-core/jarvis/llm/tools.py && grep -q "await jarvis.async_remove_entity(entity_id, api_context())" jarvis-core/jarvis/api/common.py && grep -q "\"config/entity_registry/remove\": WebSocketHandler._cmd_entity_remove" jarvis-core/jarvis/api/websocket.py && grep -q "/config/entity_registry/remove" jarvis-core/jarvis/api/rest.py'
check "a device removal takes its entities first, then the record" grep -q "for entity_id in entity_ids:" jarvis-core/jarvis/core.py
check "the entity knows its platform, so a removal stops the poll loop writing the state back" bash -c 'grep -q "entity.platform = self" jarvis-core/jarvis/entity.py && grep -q "getattr(getattr(entity, \"platform\", None), \"async_remove_entity\", None)" jarvis-core/jarvis/core.py'
check_sh "clients.md documents the two commands, and the documented set equals the handled set" 'grep -q "\`/remove\`" jarvis-core/docs/clients.md && cd jarvis-core && python3 -m pytest tests/test_packaging.py -q --timeout=120 --timeout-method=signal -k "websocket_command_is_documented" 2>&1 | tail -1'
check_pytest "the core suite: removal at the core, the API and the tools; the tier table; the websocket command" 'cd jarvis-core && python3 -m pytest tests/test_entity_remove.py tests/test_gated_services.py tests/test_api.py -q --timeout=120 --timeout-method=signal -k "not test_ws_conversation"'
check_sh "end to end through the real server: a removal confirmed by the next turn, and \"all of the elements\" refused" 'python3 -m pytest testing/e2e/test_ask_and_answer.py -q --timeout=300 --timeout-method=signal -k "removal or elements" 2>&1 | tail -1'

# --- the console -----------------------------------------------------------
check "the Devices screen offers REMOVE, twice, and re-reads the registries on their events" bash -c 'grep -q "testid=\"remove-{state.entity_id}\"" jarvis-web/src/lib/sections/Devices.svelte && grep -q "REMOVE — SURE?" jarvis-web/src/lib/sections/Devices.svelte && grep -q "entity_registry_updated" jarvis-web/src/lib/sections/Devices.svelte'
check "a dashboard tile whose entity went says so" grep -q "was removed from this Jarvis" jarvis-web/src/lib/dashboards/EntityTile.svelte
check "the mock answers both remove commands with the state_changed and the registry event" bash -c 'grep -q "case '"'"'config/entity_registry/remove'"'"'" tests/web/mock-ha.mjs && grep -q "case '"'"'config/device_registry/remove'"'"'" tests/web/mock-ha.mjs'
check "the console's knowledge graph holds notes and memory, so a removed entity has nothing there to leave" grep -q "export type NodeKind = 'note' | 'memory';" jarvis-web/src/lib/knowledge/graph.ts
ensure_web_deps
ensure_web_build
run_playwright "REMOVE on the Devices screen, a removal from elsewhere, and the tile, in a browser" 'editable-house.spec.ts'

verify_end
