"""M83 — pull things up: the surface, its panels, and the three tools.

"Have jarvis able to pull things up and display them on the voice screen…
and able to move things around." A panel is a spec the screen draws live;
this file pins the store (one per house, a file, an event), the placement
(slots around the instrument, one panel per thing, the oldest making room),
and the tools (an entity by name through the same resolver every house tool
uses; a camera; a room; a note; a page; clear; move by the words a person
uses for a place).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import surface as surface_mod  # noqa: E402
from jarvis.integrations.surface import (  # noqa: E402
    COLUMNS,
    EVENT_SURFACE_CHANGED,
    MAX_PANELS,
    Surface,
)
from jarvis.llm.tools import Exposure, ToolRegistry  # noqa: E402


async def booted(tmp_path: Path) -> tuple[Jarvis, ToolRegistry, Surface]:
    jarvis = Jarvis(tmp_path)
    await jarvis.async_setup({"demo": {}})
    registry = ToolRegistry(jarvis, exposure=Exposure.from_config(None))
    jarvis.data["llm_tools"] = registry
    assert await surface_mod.async_setup(jarvis, {})
    surface = surface_mod.get_surface(jarvis)
    assert surface is not None
    return jarvis, registry, surface


async def test_a_panel_is_placed_in_a_free_slot_and_the_screen_is_told(tmp_path):
    jarvis, _registry, surface = await booted(tmp_path)
    try:
        seen: list[dict] = []
        jarvis.bus.listen(EVENT_SURFACE_CHANGED, lambda event: seen.append(event.data))
        first = await surface.async_place({"kind": "camera", "camera": "front door", "title": "Front door"})
        second = await surface.async_place({"kind": "sky"})
        assert first["status"] == "ok" and second["status"] == "ok"
        assert (first["panel"]["x"], first["panel"]["y"]) == (0, 0)
        assert (second["panel"]["x"], second["panel"]["y"]) == (COLUMNS - 4, 0), "the second panel went over the first"
        assert len(seen) == 2 and [p["kind"] for p in seen[-1]["panels"]] == ["camera", "sky"]
        # Persisted: a fresh Surface reads the same two.
        again = Surface(jarvis)
        await again.async_load()
        assert [p["kind"] for p in again.panels] == ["camera", "sky"]
    finally:
        await jarvis.async_stop()


async def test_the_same_thing_twice_is_one_panel_and_the_oldest_makes_room(tmp_path):
    jarvis, _registry, surface = await booted(tmp_path)
    try:
        await surface.async_place({"kind": "camera", "camera": "front door"})
        await surface.async_place({"kind": "camera", "camera": "front door", "title": "Front"})
        assert len(surface.panels) == 1 and surface.panels[0]["title"] == "Front"
        for i in range(MAX_PANELS + 2):
            await surface.async_place({"kind": "note", "note": f"note {i}"})
        assert len(surface.panels) == MAX_PANELS
        assert surface.panels[0]["kind"] == "note" and "front door" not in [p.get("camera") for p in surface.panels]
    finally:
        await jarvis.async_stop()


async def test_show_puts_an_entity_up_by_its_name_and_clear_takes_it_down(tmp_path):
    jarvis, registry, surface = await booted(tmp_path)
    try:
        demo_light = next(e.entity_id for e in jarvis.entities.entities.values() if e.entity_id.startswith("light."))
        name = jarvis.states.get(demo_light).attributes.get("friendly_name") or demo_light
        shown = await registry.call("show", {"what": name}, None)
        assert shown["status"] == "ok", shown
        assert shown["panel"]["kind"] == "entity" and shown["panel"]["entity"] == demo_light
        assert "on the screen" in shown["message"]

        camera = await registry.call("show", {"what": "the front door camera"}, None)
        assert camera["status"] == "ok" and camera["panel"]["kind"] == "camera" and camera["panel"]["camera"] == "the front door"
        nothing = await registry.call("show", {"what": "the flux capacitor", "kind": "entity"}, None)
        assert nothing["status"] == "error"

        moved = await registry.call("move_panel", {"panel": "front door", "place": "bottom right"}, None)
        assert moved["status"] == "ok" and (moved["panel"]["x"], moved["panel"]["y"]) == (COLUMNS - 4, 8)
        bigger = await registry.call("move_panel", {"panel": "front door", "size": "bigger"}, None)
        assert bigger["panel"]["w"] == camera["panel"]["w"] + 2

        one = await registry.call("clear_screen", {"panel": "front door"}, None)
        assert one["status"] == "ok" and one["count"] == 1
        everything = await registry.call("clear_screen", {}, None)
        assert everything["status"] == "ok" and everything["removed"] == 1 and surface.panels == []
    finally:
        await jarvis.async_stop()


async def test_a_drag_from_the_screen_is_kept_and_clamped(tmp_path):
    jarvis, _registry, surface = await booted(tmp_path)
    try:
        placed = await surface.async_place({"kind": "sky"})
        moved = await surface.async_move(placed["panel"]["id"], x=99, y=-3, w=40, h=0)
        assert moved["panel"]["x"] == COLUMNS - moved["panel"]["w"] and moved["panel"]["y"] == 0
        assert moved["panel"]["w"] == COLUMNS and moved["panel"]["h"] == 1
        missing = await surface.async_move("nope", x=1)
        assert missing["status"] == "error"
    finally:
        await jarvis.async_stop()


async def test_a_chart_panel_draws_the_entitys_history_in_its_unit(tmp_path):
    """The chart is the sensor's numeric history from the same recorder the
    history tools read; with no recorder the current state is one point, so
    a chart draws a level rather than nothing; a state that is not a number
    is not a point; an unknown entity is an empty series, not an error."""
    jarvis, _registry, _surface = await booted(tmp_path)
    try:
        sensor = next(e.entity_id for e in jarvis.entities.entities.values() if e.entity_id.startswith("sensor."))
        state = jarvis.states.get(sensor)
        payload = await surface_mod.async_history_series(jarvis, sensor, hours=6)
        series = payload["series"][0]
        assert series["key"] == sensor and series["unit"] == (state.attributes.get("unit_of_measurement") or "")
        assert len(series["points"]) >= 1 and series["points"][-1][1] == float(state.state)
        assert payload["hours"] == 6
        light = next(e.entity_id for e in jarvis.entities.entities.values() if e.entity_id.startswith("light."))
        assert (await surface_mod.async_history_series(jarvis, light))["series"][0]["points"] == []
        assert (await surface_mod.async_history_series(jarvis, "sensor.nothing"))["series"][0]["points"] == []
        assert (await surface_mod.async_history_series(jarvis, ""))["series"] == []
    finally:
        await jarvis.async_stop()
