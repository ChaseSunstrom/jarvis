"""`scene:` — a named set of entity states, applied through domain services.

    scene:
      - name: Movie time
        entities:
          light.living_room:
            state: on
            brightness: 40
          media_player.tv: playing
          cover.blinds: closed

``scene.turn_on`` activates a configured scene; ``scene.apply`` takes a raw
``entities:`` mapping so an LLM or script can build one on the fly.

Each target is applied with the *right verb for its domain* (``turn_on`` with
attributes, ``open_cover``/``close_cover``, ``lock``/``unlock``,
``set_hvac_mode`` + ``set_temperature``, ``select_option``, ``set_value``…).
Entities whose domain has no matching service fall back to writing the state
directly, so scenes still work for virtual/template entities.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ...automation.util import as_list
from ...bus import Context
from ...const import STATE_UNKNOWN
from ...entity import Entity, EntityPlatform
from ...services import ServiceCall
from ...state import split_entity_id

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "scene"
DATA_SCENES = "scenes"

SERVICE_TURN_ON = "turn_on"
SERVICE_APPLY = "apply"
SERVICE_RELOAD = "reload"

# Attributes that describe an entity rather than configure it.
#
# The target keys are in here for safety, not tidiness: a scene entry copied
# out of a live state dump carries whatever attributes that entity exposes,
# and light/media groups expose an `entity_id` attribute listing their
# members. Letting that through would splice a second target into the service
# data and actuate an entity the scene never named.
_TARGET_ATTRS = frozenset({"entity_id", "area_id", "device_id", "label_id", "floor_id"})
_SKIP_ATTRS = (
    frozenset(
        {
            "friendly_name",
            "supported_features",
            "device_class",
            "icon",
            "unit_of_measurement",
        }
    )
    | _TARGET_ATTRS
)

ON_WORDS = frozenset({"on", "true", "home", "open", "playing", "locked", "active"})
OFF_WORDS = frozenset(
    {"off", "false", "not_home", "closed", "idle", "unlocked", "standby", "paused"}
)


class SceneEntity(Entity):
    """`scene.<slug>` — state is the timestamp of the last activation."""

    def __init__(self, scene: "Scene") -> None:
        self._scene = scene
        self._attr_name = scene.name
        self._attr_unique_id = f"scene_{scene.scene_id}"
        self._attr_icon = scene.icon or "mdi:palette"

    @property
    def state(self) -> str:
        return self._scene.last_activated or STATE_UNKNOWN

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "friendly_name": self._scene.name,
            "id": self._scene.scene_id,
            "entity_id": list(self._scene.entities),
        }


class Scene:
    """One `scene:` entry."""

    def __init__(self, jarvis: "Jarvis", config: dict[str, Any], index: int = 0) -> None:
        self.jarvis = jarvis
        self.config = dict(config or {})
        self.name = str(self.config.get("name") or self.config.get("id") or f"scene_{index}")
        self.scene_id = str(self.config.get("id") or self.name)
        self.icon = self.config.get("icon")
        self.entities: dict[str, Any] = dict(self.config.get("entities") or {})
        self.last_activated: str | None = None
        self.entity = SceneEntity(self)

    @property
    def entity_id(self) -> str:
        return self.entity.entity_id

    @property
    def domains(self) -> list[str]:
        """Domains this scene actuates. Same purpose as `Script.domains`:
        a caller can tell that `scene.come_home` touches `lock` before it
        activates it."""
        return sorted({split_entity_id(str(e))[0] for e in self.entities})

    async def async_activate(self, context: Context | None = None) -> None:
        await async_apply_entities(self.jarvis, self.entities, context)
        self.last_activated = datetime.now().astimezone().isoformat()
        self.entity.async_write_state()


# ---------------------------------------------------------------------------
# applying states
# ---------------------------------------------------------------------------
def _split_spec(spec: Any) -> tuple[str | None, dict[str, Any]]:
    """`{state: on, brightness: 40}` or a bare ``"on"`` -> (state, attributes)."""
    if isinstance(spec, dict):
        state = spec.get("state")
        attributes = {
            key: value
            for key, value in spec.items()
            if key != "state" and key not in _SKIP_ATTRS
        }
        return (None if state is None else str(state)), attributes
    if spec is None:
        return None, {}
    return str(spec), {}


async def _async_call(
    jarvis: "Jarvis",
    domain: str,
    service: str,
    data: dict[str, Any],
    context: Context | None,
) -> bool:
    if not jarvis.services.has_service(domain, service):
        return False
    try:
        await jarvis.async_call_service(domain, service, data, context=context)
    except Exception:
        _LOGGER.exception("Scene: %s.%s failed for %s", domain, service, data)
    return True


async def _async_apply_one(
    jarvis: "Jarvis",
    entity_id: str,
    state: str | None,
    attributes: dict[str, Any],
    context: Context | None,
) -> None:
    domain = split_entity_id(entity_id)[0]
    word = (state or "").strip().lower()
    # The scene's own target wins over anything left in `attributes`.
    on_data = {**attributes, "entity_id": entity_id}
    off_data = {"entity_id": entity_id}

    if domain == "cover":
        if word in ("open", "opening") and await _async_call(
            jarvis, domain, "open_cover", off_data, context
        ):
            return
        if word in ("closed", "closing") and await _async_call(
            jarvis, domain, "close_cover", off_data, context
        ):
            return
        if "position" in attributes and await _async_call(
            jarvis,
            domain,
            "set_cover_position",
            {"entity_id": entity_id, "position": attributes["position"]},
            context,
        ):
            return

    elif domain == "lock":
        service = "lock" if word == "locked" else "unlock" if word == "unlocked" else None
        if service and await _async_call(jarvis, domain, service, off_data, context):
            return

    elif domain == "climate":
        handled = False
        if state and await _async_call(
            jarvis, domain, "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": state}, context
        ):
            handled = True
        if "temperature" in attributes and await _async_call(
            jarvis,
            domain,
            "set_temperature",
            {"entity_id": entity_id, "temperature": attributes["temperature"]},
            context,
        ):
            handled = True
        if handled:
            return

    elif domain == "media_player":
        mapping = {"playing": "media_play", "paused": "media_pause", "idle": "media_stop"}
        service = mapping.get(word)
        if service and await _async_call(jarvis, domain, service, off_data, context):
            return

    elif domain in ("select", "input_select"):
        if state and await _async_call(
            jarvis, domain, "select_option", {"entity_id": entity_id, "option": state}, context
        ):
            return

    elif domain in ("number", "input_number", "text", "input_text"):
        if state is not None and await _async_call(
            jarvis, domain, "set_value", {"entity_id": entity_id, "value": state}, context
        ):
            return

    if word in ON_WORDS and await _async_call(jarvis, domain, "turn_on", on_data, context):
        return
    if word in OFF_WORDS and await _async_call(jarvis, domain, "turn_off", off_data, context):
        return

    # Nothing served this entity: write the state straight into the machine.
    current = jarvis.states.get(entity_id)
    merged = dict(current.attributes) if current else {}
    merged.update(attributes)
    jarvis.states.set(
        entity_id,
        state if state is not None else (current.state if current else STATE_UNKNOWN),
        merged,
        context=context,
    )


async def async_apply_entities(
    jarvis: "Jarvis", entities: Any, context: Context | None = None
) -> None:
    """Apply an ``entities:`` mapping (the guts of both scene services)."""
    if not isinstance(entities, dict):
        _LOGGER.warning("Scene entities must be a mapping, got %r", entities)
        return
    for entity_id, spec in entities.items():
        state, attributes = _split_spec(spec)
        await _async_apply_one(jarvis, str(entity_id), state, attributes, context)


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
def _resolve(jarvis: "Jarvis", entity_ids: Any) -> list[Scene]:
    scenes: dict[str, Scene] = jarvis.data.get(DATA_SCENES, {})
    wanted = [str(e) for e in as_list(entity_ids)]
    found = []
    for entity_id in wanted:
        scene = scenes.get(entity_id)
        if scene is None:
            scene = next((s for s in scenes.values() if s.name == entity_id), None)
        if scene is None:
            _LOGGER.warning("No scene %s", entity_id)
            continue
        found.append(scene)
    return found


async def async_setup(jarvis: "Jarvis", config: Any) -> bool:
    platform: EntityPlatform = jarvis.data.get("scene_platform") or EntityPlatform(
        jarvis, DOMAIN, DOMAIN
    )
    jarvis.data["scene_platform"] = platform
    scenes: dict[str, Scene] = jarvis.data.setdefault(DATA_SCENES, {})

    for index, raw in enumerate(as_list(config)):
        if not isinstance(raw, dict):
            _LOGGER.warning("scene: expected a mapping, got %r", raw)
            continue
        scene = Scene(jarvis, raw, index)
        await platform.async_add_entities([scene.entity])
        scenes[scene.entity_id] = scene

    async def _handle_turn_on(call: ServiceCall) -> None:
        for scene in _resolve(jarvis, call.get("entity_id")):
            await scene.async_activate(call.context)

    async def _handle_apply(call: ServiceCall) -> None:
        await async_apply_entities(jarvis, call.get("entities"), call.context)

    async def _handle_reload(call: ServiceCall) -> None:
        try:
            from ...config import load_config

            # load_config walks the config dir (!include/!secret) — real,
            # blocking file I/O, so keep it off the event loop.
            fresh = await asyncio.to_thread(load_config, jarvis.config_dir)
        except Exception:
            _LOGGER.exception("Could not re-read configuration; keeping scenes")
            return
        jarvis.config = fresh
        for scene in list(scenes.values()):
            jarvis.states.remove(scene.entity_id)
            jarvis.data.get("entity_objects", {}).pop(scene.entity_id, None)
        scenes.clear()
        platform.entities.clear()
        await async_setup(jarvis, fresh.get(DOMAIN))

    jarvis.services.register(
        DOMAIN,
        SERVICE_TURN_ON,
        _handle_turn_on,
        description="Activate a scene.",
        fields={
            "entity_id": {"description": "Scene(s) to activate.", "required": True},
            "transition": {"description": "Transition time in seconds (if supported)."},
        },
    )
    jarvis.services.register(
        DOMAIN,
        SERVICE_APPLY,
        _handle_apply,
        description="Apply a set of entity states without a stored scene.",
        fields={
            "entities": {
                "description": "Mapping of entity_id to state (or {state, attributes}).",
                "required": True,
            }
        },
    )
    jarvis.services.register(
        DOMAIN,
        SERVICE_RELOAD,
        _handle_reload,
        description="Re-read scenes from the configuration directory.",
    )
    return True


__all__ = ["DOMAIN", "Scene", "async_apply_entities", "async_setup"]
