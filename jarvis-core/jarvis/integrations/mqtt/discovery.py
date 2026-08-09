"""HA-style MQTT discovery.

Listens on ``<prefix>/<component>/[<node_id>/]<object_id>/config`` and turns the
JSON payloads Zigbee2MQTT, Tasmota, ESPHome and Shelly publish there into real
Jarvis devices and entities. Also handles the newer device-bundle form
(``<prefix>/device/<object_id>/config`` with a ``components``/``cmps`` map).

An empty payload on a config topic removes the entity again.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from .abbreviations import (
    DEVICE_ABBREVIATIONS,
    ORIGIN_ABBREVIATIONS,
    expand_abbreviations,
)
from .client import MqttClientBase, MqttMessage
from .entity import ENTITY_CLASSES, MqttEntity, create_entity

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis
    from . import MqttPlatforms

_LOGGER = logging.getLogger(__name__)

DEFAULT_DISCOVERY_PREFIX = "homeassistant"

# Components we knowingly ignore rather than warn about.
IGNORED_COMPONENTS = frozenset({"device_automation", "tag", "event", "update", "scene"})

TOPIC_KEY_SUFFIX = "_topic"


def expand_topic_prefix(config: dict[str, Any]) -> dict[str, Any]:
    """Resolve the `~` base-topic shorthand used by Tasmota/Z2M/ESPHome."""
    base = config.pop("~", None)
    if not base:
        return config
    base = str(base)

    def _expand(value: Any, key: str) -> Any:
        if not isinstance(value, str):
            return value
        if key != "topic" and not key.endswith(TOPIC_KEY_SUFFIX):
            return value
        if value.startswith("~"):
            return base + value[1:]
        if value.endswith("~"):
            return value[:-1] + base
        return value

    for key, value in list(config.items()):
        if key == "availability" and isinstance(value, list):
            config[key] = [
                {k: _expand(v, k) for k, v in item.items()} if isinstance(item, dict) else item
                for item in value
            ]
        else:
            config[key] = _expand(value, key)
    return config


def normalize_discovery_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Abbreviations -> canonical keys, then `~` expansion."""
    config = expand_abbreviations(data)

    device = config.get("device")
    if isinstance(device, dict):
        config["device"] = expand_abbreviations(device, DEVICE_ABBREVIATIONS)
    origin = config.get("origin")
    if isinstance(origin, dict):
        config["origin"] = expand_abbreviations(origin, ORIGIN_ABBREVIATIONS)

    availability = config.get("availability")
    if isinstance(availability, dict):
        availability = [availability]
    if isinstance(availability, list):
        config["availability"] = [
            expand_abbreviations(item) if isinstance(item, dict) else item
            for item in availability
        ]

    return expand_topic_prefix(config)


def parse_discovery_topic(topic: str, prefix: str) -> tuple[str, str | None, str] | None:
    """`<prefix>/<component>/[<node_id>/]<object_id>/config` -> parts."""
    parts = topic.split("/")
    prefix_parts = prefix.split("/")
    if len(parts) < len(prefix_parts) + 3 or parts[-1] != "config":
        return None
    if parts[: len(prefix_parts)] != prefix_parts:
        return None
    rest = parts[len(prefix_parts) : -1]
    if len(rest) == 2:
        return rest[0], None, rest[1]
    if len(rest) == 3:
        return rest[0], rest[1], rest[2]
    return None


class MqttDiscovery:
    """Owns every entity created from a discovery topic."""

    def __init__(
        self,
        jarvis: "Jarvis",
        client: MqttClientBase,
        platforms: "MqttPlatforms",
        prefix: str = DEFAULT_DISCOVERY_PREFIX,
    ) -> None:
        self.jarvis = jarvis
        self.client = client
        self.platforms = platforms
        self.prefix = prefix.rstrip("/")
        self.entities: dict[str, MqttEntity] = {}
        self._children: dict[str, list[str]] = {}
        self._unsubs: list[Any] = []
        # unique_id -> the discovery_id allowed to use it.
        self._owners: dict[str, str] = {}

    @property
    def discovered_ids(self) -> list[str]:
        return sorted(self.entities)

    async def async_start(self) -> None:
        for pattern in (f"{self.prefix}/+/+/config", f"{self.prefix}/+/+/+/config"):
            self._unsubs.append(
                await self.client.async_subscribe(pattern, self.async_handle_message)
            )
        _LOGGER.info("MQTT discovery listening on %s/#", self.prefix)

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    # --- message handling -------------------------------------------------
    async def async_handle_message(self, message: MqttMessage) -> None:
        parsed = parse_discovery_topic(message.topic, self.prefix)
        if parsed is None:
            return
        component, node_id, object_id = parsed
        discovery_id = "_".join(p for p in (component, node_id, object_id) if p)

        payload = message.payload.strip()
        if not payload:
            await self.async_remove(discovery_id)
            return

        try:
            data = json.loads(payload)
        except ValueError:
            _LOGGER.warning("Invalid discovery JSON on %s: %r", message.topic, payload[:200])
            return
        if not isinstance(data, dict):
            _LOGGER.warning("Discovery payload on %s is not an object", message.topic)
            return

        if component == "device" or "cmps" in data or "components" in data:
            await self._async_handle_device_bundle(discovery_id, data)
            return

        config = normalize_discovery_payload(data)
        config.setdefault("object_id", object_id)
        await self._async_apply(discovery_id, component, config)

    async def _async_handle_device_bundle(
        self, discovery_id: str, data: dict[str, Any]
    ) -> None:
        """Newer single-topic form: one payload carrying many components."""
        # `~` applies to the component configs too, so grab it before it is
        # consumed while normalizing the bundle itself.
        base_topic = data.get("~")
        bundle = normalize_discovery_payload(dict(data))
        components = bundle.pop("components", None)
        if not isinstance(components, dict):
            _LOGGER.warning("Device discovery %s has no components map", discovery_id)
            return

        shared = {key: value for key, value in bundle.items() if key != "platform"}
        seen: list[str] = []
        for key, raw in components.items():
            if not isinstance(raw, dict):
                continue
            child_id = f"{discovery_id}_{key}"
            if not raw:  # empty component config removes just that entity
                await self.async_remove(child_id)
                continue
            child = dict(raw)
            if base_topic and "~" not in child:
                child["~"] = base_topic
            config = normalize_discovery_payload(child)
            component = str(config.pop("platform", "") or "").strip()
            if not component:
                _LOGGER.warning("Component %s in %s has no platform", key, discovery_id)
                continue
            merged: dict[str, Any] = {**shared, **config}
            merged.setdefault("object_id", key)
            if await self._async_apply(child_id, component, merged):
                seen.append(child_id)

        for stale in set(self._children.get(discovery_id, [])) - set(seen):
            await self.async_remove(stale)
        self._children[discovery_id] = seen

    async def _async_apply(
        self, discovery_id: str, component: str, config: dict[str, Any]
    ) -> bool:
        if component in IGNORED_COMPONENTS:
            _LOGGER.debug("Ignoring discovery component %s (%s)", component, discovery_id)
            return False
        if component not in ENTITY_CLASSES:
            _LOGGER.info(
                "Unsupported MQTT discovery component %r (%s); skipping",
                component, discovery_id,
            )
            return False

        existing = self.entities.get(discovery_id)
        # The component is part of the identity, not of `config`: a device
        # bundle carries it in a `platform` key that is popped before the
        # config is stored, so comparing configs alone would read a
        # switch -> light change as an unchanged retained re-publish.
        if (
            existing is not None
            and existing.mqtt_domain == component
            and existing.config == config
        ):
            return True  # retained duplicate: nothing changed

        unique_id = str(config.get("unique_id") or f"mqtt_{discovery_id}")

        # SECURITY: unique_id is what binds a config to an entity_id, and the
        # entity registry looks it up per-platform, ignoring the domain. A
        # config that borrows a unique_id already in use would therefore land
        # on the *other* entity's entity_id and quietly take over its command
        # topics -- publish `lock.front_door`'s unique_id and you own the lock.
        # Only the discovery_id that first claimed a unique_id may keep it.
        owner = self._owners.get(unique_id)
        if owner is not None and owner != discovery_id:
            _LOGGER.error(
                "Ignoring MQTT discovery %s: unique_id %r is already owned by %s",
                discovery_id, unique_id, owner,
            )
            return False
        entry = self.jarvis.entities.get_by_unique_id("mqtt", unique_id)
        live = (
            self.jarvis.data.get("entity_objects", {}).get(entry.entity_id)
            if entry
            else None
        )
        if live is not None and live is not existing:
            _LOGGER.error(
                "Ignoring MQTT discovery %s: unique_id %r already belongs to %s",
                discovery_id, unique_id, entry.entity_id,
            )
            return False

        if existing is not None:
            # A component change (switch -> light) has to surrender the registry
            # entry too, otherwise get_by_unique_id hands back the old
            # `switch.foo` id and the light ends up living in the switch domain.
            await self._async_drop(
                discovery_id, purge_registry=existing.mqtt_domain != component
            )

        entity = create_entity(
            component, self.client, config, unique_id=unique_id, discovery_id=discovery_id
        )
        if entity is None:  # pragma: no cover - guarded above
            return False
        await self.platforms.async_add(component, entity)
        self.entities[discovery_id] = entity
        self._owners[unique_id] = discovery_id
        _LOGGER.info(
            "Discovered %s -> %s (%s)", discovery_id, entity.entity_id, component
        )
        return True

    async def async_remove(self, discovery_id: str) -> bool:
        """Empty config payload: tear the entity (or whole device bundle) down."""
        removed = False
        for child in self._children.pop(discovery_id, []):
            removed |= await self._async_drop(child, purge_registry=True)
        removed |= await self._async_drop(discovery_id, purge_registry=True)
        return removed

    async def _async_drop(self, discovery_id: str, purge_registry: bool) -> bool:
        entity = self.entities.pop(discovery_id, None)
        if entity is None:
            return False
        entity_id = entity.entity_id
        await self.platforms.async_remove(entity.mqtt_domain, entity_id)
        if purge_registry:
            # A genuinely removed entity releases its unique_id; a replacement
            # (purge_registry=False) keeps the claim so the id survives.
            if self._owners.get(str(entity.unique_id)) == discovery_id:
                self._owners.pop(str(entity.unique_id), None)
        if purge_registry and entity_id:
            await self.jarvis.entities.remove(entity_id)
            _LOGGER.info("Removed discovered entity %s (%s)", entity_id, discovery_id)
        return True
