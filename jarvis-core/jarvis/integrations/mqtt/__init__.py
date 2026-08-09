"""MQTT integration: broker connection, YAML entities, HA-style discovery.

    mqtt:
      broker: 127.0.0.1
      port: 1883
      username: jarvis
      password: !secret mqtt_password
      discovery: true
      discovery_prefix: homeassistant
      birth_topic: jarvis/status
      will_topic: jarvis/status
      ready_timeout: 2.0   # seconds setup waits for the first connection

      switch:
        - name: Desk lamp
          state_topic: stat/desk/POWER
          command_topic: cmnd/desk/POWER
      sensor:
        - name: Outside temperature
          state_topic: sensors/garden
          value_template: "{{ value_json.temperature }}"
          unit_of_measurement: "C"
          device_class: temperature

The live client is stored at ``jarvis.data["mqtt"]``; integration bookkeeping
(platforms, discovery) lives at ``jarvis.data["mqtt_data"]``.

Tests inject a fake by presetting ``jarvis.data["mqtt"]`` to an
:class:`~.client.MqttClientBase` instance (or ``jarvis.data["mqtt_client_factory"]``
to a callable) before calling :func:`async_setup`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...entity import EntityPlatform
from ...state import slugify
from .client import (
    FakeMqttClient,
    MqttClientBase,
    MqttMessage,
    NullClient,
    create_client,
    topic_matches,
)
from .discovery import DEFAULT_DISCOVERY_PREFIX, MqttDiscovery, normalize_discovery_payload
from .entity import ENTITY_CLASSES, MqttEntity, create_entity

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "mqtt"
DEPENDENCIES: list[str] = []

DATA_CLIENT = "mqtt"
DATA_MQTT = "mqtt_data"
DATA_CLIENT_FACTORY = "mqtt_client_factory"

SERVICE_PUBLISH = "publish"
SERVICE_DUMP = "dump"

DEFAULT_BIRTH_PAYLOAD = "online"
DEFAULT_WILL_PAYLOAD = "offline"
DUMP_FILENAME = "mqtt_dump.txt"
MAX_DUMP_SECONDS = 300.0

__all__ = [
    "DOMAIN",
    "FakeMqttClient",
    "MqttClientBase",
    "MqttData",
    "MqttMessage",
    "MqttPlatforms",
    "async_publish",
    "async_setup",
    "async_subscribe",
    "topic_matches",
]


class MqttPlatforms:
    """One EntityPlatform per Jarvis domain, created on demand."""

    def __init__(self, jarvis: "Jarvis") -> None:
        self.jarvis = jarvis
        self._platforms: dict[str, EntityPlatform] = {}

    def platform(self, domain: str) -> EntityPlatform:
        platform = self._platforms.get(domain)
        if platform is None:
            platform = EntityPlatform(self.jarvis, domain, DOMAIN)
            self._platforms[domain] = platform
        return platform

    async def async_add(self, domain: str, entity: MqttEntity) -> None:
        await self.platform(domain).async_add_entities([entity])

    async def async_remove(self, domain: str, entity_id: str) -> None:
        await self.platform(domain).async_remove_entity(entity_id)

    async def async_shutdown(self) -> None:
        for platform in self._platforms.values():
            await platform.async_shutdown()


@dataclass
class MqttData:
    """Everything the integration hangs on to."""

    client: MqttClientBase
    platforms: MqttPlatforms
    config: dict[str, Any] = field(default_factory=dict)
    discovery: MqttDiscovery | None = None
    yaml_entities: list[MqttEntity] = field(default_factory=list)
    last_dump: list[dict[str, Any]] = field(default_factory=list)


# --- helpers other integrations can use -------------------------------------
def get_client(jarvis: "Jarvis") -> MqttClientBase | None:
    client = jarvis.data.get(DATA_CLIENT)
    return client if isinstance(client, MqttClientBase) else None


async def async_publish(
    jarvis: "Jarvis",
    topic: str,
    payload: Any = "",
    retain: bool = False,
    qos: int = 0,
) -> bool:
    client = get_client(jarvis)
    if client is None:
        _LOGGER.warning("mqtt not set up; dropping publish to %s", topic)
        return False
    await client.async_publish(topic, payload, retain, qos)
    return True


async def async_subscribe(jarvis: "Jarvis", topic: str, callback: Any, qos: int = 0) -> Any:
    """Subscribe to a topic filter. Returns an unsubscribe callable (or None)."""
    client = get_client(jarvis)
    if client is None:
        _LOGGER.warning("mqtt not set up; cannot subscribe to %s", topic)
        return None
    return await client.async_subscribe(topic, callback, qos)


# --- setup ------------------------------------------------------------------
def _resolve_client(jarvis: "Jarvis", config: dict[str, Any]) -> MqttClientBase:
    """Use a pre-injected client/factory if present, else build a real one."""
    existing = jarvis.data.get(DATA_CLIENT)
    if isinstance(existing, MqttClientBase):
        _LOGGER.info("Using pre-injected MQTT client (%s)", existing.backend_name)
        return existing
    factory = jarvis.data.get(DATA_CLIENT_FACTORY)
    if callable(factory):
        client = factory(config)
        if isinstance(client, MqttClientBase):
            return client
        _LOGGER.warning("mqtt_client_factory returned %r; ignoring", type(client))
    return create_client(config)


def _yaml_entity_configs(config: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Pull `<component>: [ {...} ]` blocks out of the mqtt YAML."""
    out: list[tuple[str, dict[str, Any]]] = []
    for component in ENTITY_CLASSES:
        block = config.get(component)
        if block is None:
            continue
        items = block if isinstance(block, list) else [block]
        for item in items:
            if not isinstance(item, dict):
                _LOGGER.warning("mqtt %s entry is not a mapping: %r", component, item)
                continue
            out.append((component, dict(item)))
    return out


async def _async_setup_yaml_entities(
    config: dict[str, Any], client: MqttClientBase, platforms: MqttPlatforms
) -> list[MqttEntity]:
    created: list[MqttEntity] = []
    for component, raw in _yaml_entity_configs(config):
        entity_config = normalize_discovery_payload(raw)
        name = entity_config.get("name") or entity_config.get("object_id") or component
        unique_id = (
            entity_config.get("unique_id") or f"mqtt_yaml_{component}_{slugify(str(name))}"
        )
        entity = create_entity(component, client, entity_config, unique_id=unique_id)
        if entity is None:  # pragma: no cover - keys come from ENTITY_CLASSES
            continue
        await platforms.async_add(component, entity)
        created.append(entity)
    if created:
        _LOGGER.info("Set up %d MQTT entities from YAML", len(created))
    return created


def _birth_message(config: dict[str, Any]) -> dict[str, Any] | None:
    message = config.get("birth_message")
    if isinstance(message, dict) and message.get("topic"):
        return {
            "topic": message["topic"],
            "payload": message.get("payload", DEFAULT_BIRTH_PAYLOAD),
            "retain": bool(message.get("retain", True)),
            "qos": int(message.get("qos", 0)),
        }
    if config.get("birth_topic"):
        return {
            "topic": config["birth_topic"],
            "payload": config.get("birth_payload", DEFAULT_BIRTH_PAYLOAD),
            "retain": bool(config.get("birth_retain", True)),
            "qos": int(config.get("birth_qos", 0)),
        }
    return None


def _register_services(jarvis: "Jarvis", data: MqttData) -> None:
    async def _publish(call: Any) -> None:
        topic = call.get("topic")
        if not topic:
            raise ValueError("mqtt.publish requires a 'topic'")
        payload = call.get("payload", "")
        template = call.get("payload_template")
        if template:
            from .entity import render_value_template

            payload = render_value_template(template, str(payload), default="")
        sent = await data.client.async_publish(
            str(topic),
            payload,
            bool(call.get("retain", False)),
            int(call.get("qos", 0) or 0),
        )
        if sent is False:
            # The client logs the cause; the caller has to learn that the
            # message never left, or automations silently no-op.
            raise RuntimeError(f"mqtt.publish to {topic!r} failed (see log)")

    async def _dump(call: Any) -> dict[str, Any]:
        topic = str(call.get("topic", "#"))
        # Clamped: this is a debugging aid, and an unbounded value pins a
        # service call (and its subscription) open for the life of the process.
        seconds = min(max(float(call.get("seconds", 5) or 5), 0.0), MAX_DUMP_SECONDS)
        collected: list[dict[str, Any]] = []

        def _collect(message: MqttMessage) -> None:
            collected.append(
                {
                    "topic": message.topic,
                    "payload": message.payload,
                    "retain": message.retain,
                }
            )

        unsub = await data.client.async_subscribe(topic, _collect)
        try:
            await asyncio.sleep(seconds)
        finally:
            unsub()

        data.last_dump = collected
        path = jarvis.config_dir / DUMP_FILENAME
        lines = [f"{item['topic']} {item['payload']}" for item in collected]
        try:
            await asyncio.to_thread(
                path.write_text, "\n".join(lines) + ("\n" if lines else ""), "utf-8"
            )
        except OSError:
            _LOGGER.warning("Could not write %s", path, exc_info=True)
        return {"topic": topic, "count": len(collected), "messages": collected}

    jarvis.services.register(
        DOMAIN,
        SERVICE_PUBLISH,
        _publish,
        description="Publish a message to an MQTT topic.",
        fields={
            "topic": {"required": True, "example": "cmnd/desk/POWER"},
            "payload": {"example": "ON"},
            "payload_template": {"example": "{{ value }}"},
            "retain": {"default": False},
            "qos": {"default": 0},
        },
    )
    jarvis.services.register(
        DOMAIN,
        SERVICE_DUMP,
        _dump,
        description="Record messages on a topic filter for N seconds (debugging).",
        fields={
            "topic": {"default": "#", "example": "zigbee2mqtt/#"},
            "seconds": {"default": 5, "description": f"Capped at {MAX_DUMP_SECONDS:.0f}s."},
        },
        supports_response=True,
    )


async def async_setup(jarvis: "Jarvis", config: Any) -> bool:
    if config is None:
        config = {}
    if not isinstance(config, dict):
        _LOGGER.error("mqtt: config must be a mapping, got %r", type(config).__name__)
        return False

    client = _resolve_client(jarvis, config)
    platforms = MqttPlatforms(jarvis)
    data = MqttData(client=client, platforms=platforms, config=config)
    jarvis.data[DATA_CLIENT] = client
    jarvis.data[DATA_MQTT] = data

    _register_services(jarvis, data)

    connected = await client.async_connect()
    if not connected and not isinstance(client, NullClient):
        _LOGGER.warning("MQTT broker %s:%s unreachable; will keep retrying",
                        client.broker, client.port)

    data.yaml_entities = await _async_setup_yaml_entities(config, client, platforms)

    if config.get("discovery", True):
        prefix = str(config.get("discovery_prefix") or DEFAULT_DISCOVERY_PREFIX)
        discovery = MqttDiscovery(jarvis, client, platforms, prefix)
        data.discovery = discovery
        await discovery.async_start()

    birth = _birth_message(config)
    if birth:
        await client.async_publish(
            birth["topic"], birth["payload"], birth["retain"], birth["qos"]
        )

    async def _shutdown() -> None:
        will = config.get("will_message") or (
            {
                "topic": config["will_topic"],
                "payload": config.get("will_payload", DEFAULT_WILL_PAYLOAD),
                "retain": config.get("will_retain", True),
            }
            if config.get("will_topic")
            else None
        )
        if will and will.get("topic"):
            await client.async_publish(
                will["topic"],
                will.get("payload", DEFAULT_WILL_PAYLOAD),
                bool(will.get("retain", True)),
            )
        if data.discovery is not None:
            await data.discovery.async_stop()
        await platforms.async_shutdown()
        await client.async_disconnect()

    jarvis.register_shutdown(_shutdown)
    return True
