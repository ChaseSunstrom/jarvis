"""Taking things out of the house (M69).

"Can you remove all of the elements of the house?" — "I have no tool for
deleting entities." The console could not delete one either: the Devices
screen wrote names, areas and exposure, and the only way to make an entity go
away was to edit `.storage/` by hand.

There is one delete path now, on the core, and three doors to it — the
websocket command the Devices screen uses, its REST twin, and the assistant's
`remove_entities` / `remove_device`, which are Tier 3 with the targets pinned
into the approval exactly as `lock_control` pins its doors. The claims here:

* removing an entity takes its live object, its state and its registry entry,
  so it leaves the exposure list and the house summary the model reads, and a
  `state_changed` with no new state reaches every surface;
* the platform's poll loop cannot write it back;
* "all of the elements" is refused with a sentence before anything is held —
  an approval must show exactly what it removes;
* what runs after the yes is what the approval showed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis.api.common import ApiError, async_remove_device, async_remove_entity  # noqa: E402
from jarvis.const import (  # noqa: E402
    EVENT_DEVICE_REGISTRY_UPDATED,
    EVENT_ENTITY_REGISTRY_UPDATED,
    EVENT_STATE_CHANGED,
)
from jarvis.core import Jarvis  # noqa: E402
from jarvis.entity import Entity, EntityPlatform  # noqa: E402
from jarvis.llm.tools import (  # noqa: E402
    EVENT_APPROVAL_REQUIRED,
    MAX_REMOVE_AT_ONCE,
    TIER_APPROVAL,
    ToolRegistry,
    register_builtin_tools,
)
from test_llm import build_house, make_registry, shutdown  # noqa: E402

pytestmark = pytest.mark.asyncio


class _Polled(Entity):
    """A polled entity: the poll loop would write its state back."""

    _attr_should_poll = True

    def __init__(self, name: str, unique_id: str) -> None:
        super().__init__()
        self._attr_name = name
        self._attr_unique_id = unique_id
        self.updates = 0

    async def async_update(self) -> None:
        self.updates += 1
        self._attr_state = "on"


async def _jarvis(tmp_path: Path) -> Jarvis:
    jarvis = Jarvis(tmp_path)
    await jarvis.async_start()
    return jarvis


async def _entity(jarvis: Jarvis, object_id: str = "kitchen", device_id: str | None = None) -> str:
    entry = await jarvis.entities.async_get_or_create(
        "light", "demo", f"uid-{object_id}", object_id, name=object_id.title(), device_id=device_id
    )
    jarvis.states.set(entry.entity_id, "on", {"brightness": 200})
    return entry.entity_id


def _events(jarvis: Jarvis, *types: str) -> list[tuple[str, dict]]:
    seen: list[tuple[str, dict]] = []
    for event_type in types:
        jarvis.bus.listen(event_type, lambda event, t=event_type: seen.append((t, dict(event.data))))
    return seen


# ===========================================================================
# the core: one delete path
# ===========================================================================
async def test_removing_an_entity_takes_its_state_and_its_entry(tmp_path: Path):
    jarvis = await _jarvis(tmp_path)
    entity_id = await _entity(jarvis)
    seen = _events(jarvis, EVENT_STATE_CHANGED, EVENT_ENTITY_REGISTRY_UPDATED)

    outcome = await jarvis.async_remove_entity(entity_id)

    assert outcome["removed"] is True
    assert outcome["state"] is True and outcome["registry"] is True
    assert jarvis.states.get(entity_id) is None
    assert jarvis.entities.get(entity_id) is None
    # Every surface hears it the way it hears a rename's old id: a state
    # change with nothing new, and a registry event that says remove.
    assert (EVENT_STATE_CHANGED, {"entity_id": entity_id, "old_state": seen[0][1]["old_state"], "new_state": None}) in seen
    assert (EVENT_ENTITY_REGISTRY_UPDATED, {"action": "remove", "entity_id": entity_id}) in seen
    await jarvis.async_stop()


async def test_removal_is_saved_so_a_restart_does_not_bring_it_back(tmp_path: Path):
    jarvis = await _jarvis(tmp_path)
    entity_id = await _entity(jarvis)
    await jarvis.async_remove_entity(entity_id)
    await jarvis.async_stop()

    again = Jarvis(tmp_path)
    await again.entities.load()
    assert again.entities.get(entity_id) is None


async def test_removing_what_is_not_there_says_so(tmp_path: Path):
    jarvis = await _jarvis(tmp_path)
    outcome = await jarvis.async_remove_entity("light.never")
    assert outcome["removed"] is False
    await jarvis.async_stop()


async def test_a_platforms_poll_loop_cannot_write_the_state_back(tmp_path: Path):
    """The object goes too. Dropping only the registry entry and the state
    left the entity in the platform's poll loop, which wrote it straight back."""
    jarvis = await _jarvis(tmp_path)
    platform = EntityPlatform(jarvis, "light", "test", scan_interval=0.01)
    lamp = _Polled("Old Lamp", "old-lamp")
    await platform.async_add_entities([lamp])
    entity_id = lamp.entity_id
    assert jarvis.states.get(entity_id) is not None
    assert lamp.platform is platform

    outcome = await jarvis.async_remove_entity(entity_id)

    assert outcome["object"] is True
    assert entity_id not in platform.entities
    assert jarvis.entity_object(entity_id) is None
    import asyncio

    updates = lamp.updates
    await asyncio.sleep(0.05)
    assert lamp.updates == updates, "the poll loop still updates a removed entity"
    assert jarvis.states.get(entity_id) is None
    await platform.async_shutdown()
    await jarvis.async_stop()


async def test_a_removed_entity_leaves_the_exposure_list_and_the_house_summary(tmp_path: Path):
    jarvis, _ = await build_house(tmp_path)
    registry = make_registry(jarvis)
    assert "light.reading_lamp" in registry.exposure.entity_ids(jarvis)

    await jarvis.async_remove_entity("light.reading_lamp")

    assert "light.reading_lamp" not in registry.exposure.entity_ids(jarvis)
    listed = await registry.call("list_entities", {"domain": "light"}, None)
    assert "light.reading_lamp" not in {e["entity_id"] for e in listed["entities"]}
    await shutdown(jarvis)


async def test_removing_a_device_takes_its_entities_first(tmp_path: Path):
    jarvis = await _jarvis(tmp_path)
    device = await jarvis.devices.async_get_or_create(["hue:1"], "Hue Bridge", "hue")
    first = await _entity(jarvis, "hall", device.id)
    second = await _entity(jarvis, "porch", device.id)
    other = await _entity(jarvis, "study")
    seen = _events(jarvis, EVENT_DEVICE_REGISTRY_UPDATED)

    outcome = await jarvis.async_remove_device(device.id)

    assert outcome["removed"] is True
    assert outcome["name"] == "Hue Bridge"
    assert outcome["entities"] == sorted([first, second])
    assert jarvis.devices.devices.get(device.id) is None
    assert jarvis.entities.get(first) is None and jarvis.states.get(second) is None
    # A device that was not this one's is untouched.
    assert jarvis.entities.get(other) is not None
    assert (EVENT_DEVICE_REGISTRY_UPDATED, {"action": "remove", "device_id": device.id}) in seen
    assert (await jarvis.async_remove_device("nope"))["removed"] is False
    await jarvis.async_stop()


# ===========================================================================
# the API door
# ===========================================================================
async def test_the_api_removes_and_answers_with_what_it_removed(tmp_path: Path):
    jarvis = await _jarvis(tmp_path)
    entity_id = await _entity(jarvis)
    result = await async_remove_entity(jarvis, {"entity_id": entity_id})
    assert result == {
        "entity_id": entity_id,
        "removed": True,
        "had_state": True,
        "had_registry_entry": True,
    }
    with pytest.raises(ApiError) as missing:
        await async_remove_entity(jarvis, {"entity_id": entity_id})
    assert missing.value.code == "not_found"
    with pytest.raises(ApiError) as blank:
        await async_remove_entity(jarvis, {})
    assert blank.value.code == "invalid_format"
    await jarvis.async_stop()


async def test_the_api_removes_a_device(tmp_path: Path):
    jarvis = await _jarvis(tmp_path)
    device = await jarvis.devices.async_get_or_create(["hue:1"], "Hue Bridge", "hue")
    entity_id = await _entity(jarvis, "hall", device.id)
    result = await async_remove_device(jarvis, {"device_id": device.id})
    assert result["removed"] is True and result["entities"] == [entity_id]
    with pytest.raises(ApiError):
        await async_remove_device(jarvis, {"device_id": device.id})
    await jarvis.async_stop()


# ===========================================================================
# the assistant's door: Tier 3, pinned, and "all" refused
# ===========================================================================
def _held(jarvis) -> list[dict]:
    seen: list[dict] = []
    jarvis.bus.listen(EVENT_APPROVAL_REQUIRED, lambda event: seen.append(event.data))
    return seen


async def test_remove_entities_is_tier_three_with_the_ids_pinned(tmp_path: Path):
    jarvis, _ = await build_house(tmp_path)
    registry = make_registry(jarvis)
    assert registry.get("remove_entities").tier == TIER_APPROVAL
    assert registry.get("remove_device").tier == TIER_APPROVAL
    held = _held(jarvis)

    result = await registry.call(
        "remove_entities", {"entity_ids": ["light.reading_lamp", "Light.Kitchen_Counter"]}, None
    )

    assert result["status"] == "approval_required"
    # Concrete, normalised ids on the card — what the human agrees to.
    assert held[0]["arguments"] == {
        "entity_ids": ["light.reading_lamp", "light.kitchen_counter"]
    }
    assert jarvis.states.get("light.reading_lamp") is not None, "held, so nothing ran"
    await shutdown(jarvis)


async def test_the_confirmed_removal_removes_exactly_what_was_shown(tmp_path: Path):
    jarvis, _ = await build_house(tmp_path)
    registry = make_registry(jarvis)
    held = _held(jarvis)
    await registry.call("remove_entities", {"entity_ids": ["light.reading_lamp"]}, None)

    outcome = await registry.approve_request(held[0]["request_id"], True)

    assert outcome["status"] == "executed"
    assert outcome["result"]["status"] == "ok"
    assert outcome["result"]["removed"] == ["light.reading_lamp"]
    assert jarvis.states.get("light.reading_lamp") is None
    assert jarvis.entities.get("light.reading_lamp") is None
    assert jarvis.states.get("light.kitchen_counter") is not None
    await shutdown(jarvis)


async def test_a_name_is_resolved_and_pinned_like_a_locks(tmp_path: Path):
    jarvis, _ = await build_house(tmp_path)
    registry = make_registry(jarvis)
    held = _held(jarvis)
    await registry.call("remove_entities", {"name": "reading lamp"}, None)
    assert held[0]["arguments"] == {"entity_ids": ["light.reading_lamp"]}
    await shutdown(jarvis)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"entity_ids": []},
        {"entity_ids": ["*"]},
        {"entity_ids": ["all"]},
        {"name": "everything"},
        {"name": "all of them"},
        {"name": "the house"},
        {"entity_ids": ["light.reading_lamp", "everything"]},
    ],
)
async def test_all_of_the_elements_is_refused_with_a_sentence(tmp_path: Path, arguments):
    """The operator's request, and every spelling of it: refused before it is
    held, with what to do instead, and nothing on any consent surface."""
    jarvis, _ = await build_house(tmp_path)
    registry = make_registry(jarvis)
    held = _held(jarvis)

    result = await registry.call("remove_entities", arguments, None)

    assert result["status"] == "error"
    assert "list_entities" in result["error"]
    assert held == [], "a wildcard removal reached a consent surface"
    assert jarvis.states.get("light.reading_lamp") is not None
    await shutdown(jarvis)


async def test_an_unknown_id_is_refused_rather_than_held(tmp_path: Path):
    jarvis, _ = await build_house(tmp_path)
    registry = make_registry(jarvis)
    held = _held(jarvis)
    result = await registry.call("remove_entities", {"entity_ids": ["light.never_was"]}, None)
    assert result["status"] == "error"
    assert "light.never_was" in result["error"]
    assert held == []
    await shutdown(jarvis)


async def test_too_many_at_once_is_refused(tmp_path: Path):
    jarvis = await _jarvis(tmp_path)
    ids = [await _entity(jarvis, f"lamp_{i}") for i in range(MAX_REMOVE_AT_ONCE + 1)]
    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    held = _held(jarvis)
    result = await registry.call("remove_entities", {"entity_ids": ids}, None)
    assert result["status"] == "error"
    assert str(MAX_REMOVE_AT_ONCE) in result["error"]
    assert held == []
    await jarvis.async_stop()


async def test_the_refusal_is_the_registrys_and_a_rereg_cannot_drop_it(tmp_path: Path):
    """The refusal is part of what the tool is; a re-registration without one
    is a weakening, like losing a gate."""
    jarvis, _ = await build_house(tmp_path)
    registry = make_registry(jarvis)
    with pytest.raises(ValueError, match="refusal"):
        registry.register(
            name="remove_entities",
            description="x",
            handler=registry.get("remove_entities").handler,
            tier=TIER_APPROVAL,
            pin=registry.get("remove_entities").pin,
        )
    await shutdown(jarvis)


async def test_remove_device_pins_the_device_and_everything_on_it(tmp_path: Path):
    jarvis = await _jarvis(tmp_path)
    device = await jarvis.devices.async_get_or_create(["hue:1"], "Hue Bridge", "hue")
    first = await _entity(jarvis, "hall", device.id)
    second = await _entity(jarvis, "porch", device.id)
    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    held = _held(jarvis)

    result = await registry.call("remove_device", {"name": "hue bridge"}, None)

    assert result["status"] == "approval_required"
    assert held[0]["arguments"] == {
        "device_id": device.id,
        "name": "Hue Bridge",
        "entity_ids": sorted([first, second]),
    }
    outcome = await registry.approve_request(held[0]["request_id"], True)
    assert outcome["result"]["status"] == "ok"
    assert outcome["result"]["entities"] == sorted([first, second])
    assert jarvis.devices.devices.get(device.id) is None
    assert jarvis.states.get(first) is None
    await jarvis.async_stop()


@pytest.mark.parametrize(
    "arguments", [{}, {"name": "all"}, {"device_id": "*"}, {"name": "no such bridge"}]
)
async def test_remove_device_refuses_the_vague_and_the_unknown(tmp_path: Path, arguments):
    jarvis = await _jarvis(tmp_path)
    await jarvis.devices.async_get_or_create(["hue:1"], "Hue Bridge", "hue")
    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    held = _held(jarvis)
    result = await registry.call("remove_device", arguments, None)
    assert result["status"] == "error"
    assert "list_devices" in result["error"]
    assert held == []
    await jarvis.async_stop()


async def test_two_devices_with_the_same_word_are_not_guessed_between(tmp_path: Path):
    jarvis = await _jarvis(tmp_path)
    await jarvis.devices.async_get_or_create(["hue:1"], "Hue Bridge", "hue")
    await jarvis.devices.async_get_or_create(["hue:2"], "Hue Bridge Upstairs", "hue")
    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    result = await registry.call("remove_device", {"name": "bridge"}, None)
    assert result["status"] == "error"
    assert "2 devices match" in result["error"]
    # The exact name still wins over the substring.
    exact = await registry.call("remove_device", {"name": "Hue Bridge"}, None)
    assert exact["status"] == "approval_required"
    await jarvis.async_stop()


async def test_list_devices_names_them_with_their_entities(tmp_path: Path):
    """The refusal says "call list_devices", so list_devices must exist and
    answer with the ids the removal wants."""
    jarvis = await _jarvis(tmp_path)
    area = await jarvis.areas.create("Hall")
    device = await jarvis.devices.async_get_or_create(
        ["hue:1"], "Hue Bridge", "hue", manufacturer="Signify", area_id=area.id
    )
    entity_id = await _entity(jarvis, "hall", device.id)
    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)

    listed = await registry.call("list_devices", {}, None)

    assert listed["count"] == 1
    assert listed["devices"][0] == {
        "device_id": device.id,
        "name": "Hue Bridge",
        "entities": [entity_id],
        "manufacturer": "Signify",
        "area": "Hall",
    }
    assert (await registry.call("list_devices", {"area": "hall"}, None))["count"] == 1
    assert (await registry.call("list_devices", {"area": "loft"}, None))["count"] == 0
    assert registry.is_read_only(registry.get("list_devices"))
    await jarvis.async_stop()
