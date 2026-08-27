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


# ---------------------------------------------------------------------------
# M88: a plan on the screen — the surface follows a background job on its own
# ---------------------------------------------------------------------------
async def test_a_background_job_with_steps_is_a_task_panel_while_it_runs_and_a_note_when_done(tmp_path):
    from jarvis.tasks import EVENT_TASK_ADDED, EVENT_TASK_UPDATED

    jarvis, _registry, surface = await booted(tmp_path)
    job = {"id": "job1", "kind": "background", "title": "Audit every sensor", "status": "running",
           "steps": [{"title": "list them", "status": "done"}, {"title": "read each", "status": "running"}]}
    await jarvis.bus.async_fire(EVENT_TASK_ADDED, {"task": job})
    await jarvis.async_block_till_done()
    panels = surface.as_payload()["panels"]
    assert [p["kind"] for p in panels] == ["task"] and panels[0]["task"] == "job1"
    assert panels[0]["title"] == "Audit every sensor"

    # The same job again is the same panel, not two.
    await jarvis.bus.async_fire(EVENT_TASK_UPDATED, {"task": {**job, "steps": job["steps"] + [{"title": "write it up", "status": "queued"}]}})
    await jarvis.async_block_till_done()
    assert len(surface.as_payload()["panels"]) == 1

    await jarvis.bus.async_fire(EVENT_TASK_UPDATED, {"task": {**job, "status": "done", "result": "Two sensors look wrong: the garage humidity and the hall CO2."}})
    await jarvis.async_block_till_done()
    panels = surface.as_payload()["panels"]
    assert [p["kind"] for p in panels] == ["note"], panels
    assert panels[0]["title"] == "Finished: Audit every sensor" and "garage humidity" in panels[0]["text"]
    await jarvis.async_stop()


async def test_a_job_without_steps_or_of_another_kind_is_not_followed(tmp_path):
    from jarvis.tasks import EVENT_TASK_ADDED, EVENT_TASK_UPDATED

    jarvis, _registry, surface = await booted(tmp_path)
    await jarvis.bus.async_fire(EVENT_TASK_ADDED, {"task": {"id": "a", "kind": "background", "title": "x", "status": "running", "steps": []}})
    await jarvis.bus.async_fire(EVENT_TASK_ADDED, {"task": {"id": "b", "kind": "research", "title": "y", "status": "running", "steps": [{"title": "s", "status": "running"}]}})
    await jarvis.async_block_till_done()
    assert surface.as_payload()["panels"] == []
    # An errored job leaves nothing behind either — no note of a failure on the screen.
    await jarvis.bus.async_fire(EVENT_TASK_ADDED, {"task": {"id": "c", "kind": "background", "title": "z", "status": "running", "steps": [{"title": "s", "status": "running"}]}})
    await jarvis.async_block_till_done()
    assert len(surface.as_payload()["panels"]) == 1
    await jarvis.bus.async_fire(EVENT_TASK_UPDATED, {"task": {"id": "c", "kind": "background", "title": "z", "status": "error", "error": "boom", "steps": []}})
    await jarvis.async_block_till_done()
    assert surface.as_payload()["panels"] == []
    await jarvis.async_stop()


async def test_a_note_is_a_one_row_brief_at_the_side_and_notes_stack_down(tmp_path):
    """M112. The operator's report of 27 Aug 2026: note panels a third of the
    page tall, spread slot by slot over the instrument. A note or a page is
    4×1 now, in the right-hand column, each under the last; a panel already
    there is walked past by its real height, not its slot corner."""
    from jarvis.integrations.surface import DEFAULT_SIZE

    assert DEFAULT_SIZE["note"] == (4, 1) and DEFAULT_SIZE["page"] == (4, 1)
    jarvis, _registry, surface = await booted(tmp_path)
    try:
        alone = await surface.async_place({"kind": "note", "note": "sensor audit", "text": "# Sensor audit\n\nAll fine."})
        assert (alone["panel"]["x"], alone["panel"]["y"], alone["panel"]["w"], alone["panel"]["h"]) == (COLUMNS - 4, 0, 4, 1)
        await surface.async_clear() if hasattr(surface, "async_clear") else None
        surface.panels.clear()
        camera = await surface.async_place({"kind": "camera", "camera": "front door"})
        sky = await surface.async_place({"kind": "sky"})
        assert (camera["panel"]["x"], camera["panel"]["y"]) == (0, 0)
        assert (sky["panel"]["x"], sky["panel"]["y"], sky["panel"]["h"]) == (COLUMNS - 4, 0, 2)
        first = await surface.async_place({"kind": "note", "note": "first", "text": "one"})
        second = await surface.async_place({"kind": "note", "note": "second", "text": "two"})
        page = await surface.async_place({"kind": "page", "url": "https://example.com", "text": "three"})
        assert (first["panel"]["x"], first["panel"]["y"]) == (COLUMNS - 4, 2), "under the sky, by its real height"
        assert (second["panel"]["x"], second["panel"]["y"]) == (COLUMNS - 4, 3)
        assert (page["panel"]["x"], page["panel"]["y"]) == (COLUMNS - 4, 4)
        assert all(p["h"] == 1 for p in (first["panel"], second["panel"], page["panel"]))
        # Opened from the console: the same move a drag makes, persisted.
        grown = await surface.async_move(first["panel"]["id"], h=4)
        assert grown["status"] == "ok" and surface.find(first["panel"]["id"])["h"] == 4
    finally:
        await jarvis.async_stop()
