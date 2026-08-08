"""MQTT entity classes.

One set of classes serves both YAML-configured and discovery-created entities:
the discovery layer just hands over a config dict with canonical (un-abbreviated)
keys, exactly like the YAML block would.

Each class implements the Jarvis entity method contract by publishing to its
command topics, and updates its state from its state topics.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from ...const import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_MODE,
    ATTR_POSITION,
    ATTR_RGB_COLOR,
    ATTR_TEMPERATURE,
    STATE_CLOSED,
    STATE_LOCKED,
    STATE_OFF,
    STATE_ON,
    STATE_OPEN,
    STATE_UNKNOWN,
    STATE_UNLOCKED,
)
from ...entity import Entity
from ...state import slugify
from .client import MqttClientBase, MqttMessage

_LOGGER = logging.getLogger(__name__)

STATE_OPENING = "opening"
STATE_CLOSING = "closing"

DEFAULT_PAYLOAD_ON = "ON"
DEFAULT_PAYLOAD_OFF = "OFF"
DEFAULT_PAYLOAD_AVAILABLE = "online"
DEFAULT_PAYLOAD_NOT_AVAILABLE = "offline"
DEFAULT_BRIGHTNESS_SCALE = 255


# --- templating -------------------------------------------------------------
try:  # jinja2 is the normal path; the fallback keeps us honest without it
    import jinja2

    _ENV: Any = jinja2.Environment(
        undefined=getattr(jinja2, "ChainableUndefined", jinja2.Undefined),
        autoescape=False,
    )
except ImportError:  # pragma: no cover - jinja2 is a listed dependency
    jinja2 = None  # type: ignore[assignment]
    _ENV = None

_TEMPLATE_CACHE: dict[str, Any] = {}


def _fallback_render(template: str, context: dict[str, Any]) -> str:
    """Tiny `{{ value }}` / `{{ value_json.a.b }}` renderer for a jinja2-less install."""
    import re

    def _resolve(expr: str) -> str:
        expr = expr.strip()
        current: Any = context
        for part in re.split(r"\.|\[", expr):
            part = part.strip().strip("]").strip("'\"")
            if not part:
                continue
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list) and part.isdigit():
                current = current[int(part)] if int(part) < len(current) else None
            else:
                current = getattr(current, part, None)
            if current is None:
                return ""
        return "" if current is None else str(current)

    return re.sub(r"\{\{(.+?)\}\}", lambda m: _resolve(m.group(1)), template)


def render_value_template(
    template: str | None,
    payload: str,
    default: Any = None,
    extra: dict[str, Any] | None = None,
) -> Any:
    """Render an MQTT value template against a payload.

    `value` is the raw payload, `value_json` the parsed JSON (when it parses).
    Returns `default` when the template errors or renders to nothing.
    """
    if not template:
        return payload
    context: dict[str, Any] = {"value": payload}
    try:
        context["value_json"] = json.loads(payload)
    except (ValueError, TypeError):
        pass
    if extra:
        context.update(extra)

    if _ENV is not None:
        compiled = _TEMPLATE_CACHE.get(template)
        if compiled is None:
            try:
                compiled = _ENV.from_string(template)
            except Exception:
                _LOGGER.warning("Invalid MQTT template %r", template)
                return default
            _TEMPLATE_CACHE[template] = compiled
        try:
            rendered = compiled.render(**context)
        except Exception:
            _LOGGER.warning("Error rendering MQTT template %r", template, exc_info=True)
            return default
    else:  # pragma: no cover - exercised only without jinja2
        rendered = _fallback_render(template, context)

    rendered = rendered.strip()
    if rendered in ("", "None", "none", "Undefined"):
        return default
    return rendered


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int | None = None) -> int | None:
    number = _as_float(value)
    if number is None:
        return default
    return int(round(number))


def _kelvin_to_mireds(kelvin: float) -> int:
    return int(round(1_000_000 / max(float(kelvin), 1.0)))


def _mireds_to_kelvin(mireds: float) -> int:
    return int(round(1_000_000 / max(float(mireds), 1.0)))


def _format_number(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return str(value)
    if float(number).is_integer():
        return str(int(number))
    return str(number)


def device_info_from_config(config: dict[str, Any]) -> dict[str, Any] | None:
    """Build Entity.device_info from a discovery/YAML `device` block."""
    device = config.get("device")
    if not isinstance(device, dict):
        return None

    identifiers: list[str] = []
    raw_ids = device.get("identifiers") or []
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    for item in raw_ids:
        if isinstance(item, (list, tuple)):
            identifiers.append("_".join(str(part) for part in item))
        else:
            identifiers.append(str(item))
    if not identifiers:
        for item in device.get("connections") or []:
            if isinstance(item, (list, tuple)):
                identifiers.append("_".join(str(part) for part in item))
            else:
                identifiers.append(str(item))
    if not identifiers:
        name = device.get("name")
        if not name:
            return None
        identifiers = [f"mqtt_{slugify(str(name))}"]

    return {
        "identifiers": identifiers,
        "name": str(device.get("name") or identifiers[0]),
        "manufacturer": device.get("manufacturer"),
        "model": device.get("model") or device.get("model_id"),
        "sw_version": device.get("sw_version"),
        "suggested_area": device.get("suggested_area"),
    }


def entity_name_from_config(config: dict[str, Any], fallback: str = "MQTT entity") -> str:
    """The display name: explicit name, else device name, else object id."""
    name = config.get("name")
    if name:
        device = config.get("device")
        if isinstance(device, dict) and config.get("has_entity_name"):
            device_name = device.get("name")
            if device_name and not str(name).lower().startswith(str(device_name).lower()):
                return f"{device_name} {name}"
        return str(name)
    device = config.get("device")
    if isinstance(device, dict) and device.get("name"):
        return str(device["name"])
    for key in ("object_id", "unique_id"):
        if config.get(key):
            return str(config[key]).replace("_", " ").strip() or fallback
    return fallback


# --- base -------------------------------------------------------------------
class MqttEntity(Entity):
    """Common MQTT plumbing: availability, JSON attributes, templates, publish."""

    mqtt_domain = "sensor"

    def __init__(
        self,
        client: MqttClientBase,
        config: dict[str, Any],
        unique_id: str | None = None,
        discovery_id: str | None = None,
    ) -> None:
        self._client = client
        self._config = dict(config)
        self.discovery_id = discovery_id
        self._unsubs: list[Callable[[], None]] = []
        self._json_attributes: dict[str, Any] = {}
        self._expire_task: asyncio.Task | None = None

        self._attr_unique_id = (
            unique_id
            or config.get("unique_id")
            or config.get("object_id")
            or f"mqtt_{slugify(entity_name_from_config(config))}"
        )
        self._attr_name = entity_name_from_config(config)
        self._attr_icon = config.get("icon")
        self._attr_device_class = config.get("device_class")
        self._attr_unit_of_measurement = config.get("unit_of_measurement")
        self._attr_device_info = device_info_from_config(config)
        self._attr_extra_attributes = {}

        self._qos = _as_int(config.get("qos"), 0) or 0
        self._retain = bool(config.get("retain", False))
        self._expire_after = _as_float(config.get("expire_after")) or 0.0

        self._availability = self._parse_availability()
        self._availability_state: dict[str, bool] = {}
        # HA semantics: an entity that declares availability topics stays
        # unavailable until the device says otherwise.
        self._attr_available = not self._availability

        state_class = config.get("state_class")
        if state_class:
            self._attr_extra_attributes["state_class"] = state_class
        if config.get("entity_category"):
            self._attr_extra_attributes["entity_category"] = config["entity_category"]

    # --- config helpers ---------------------------------------------------
    def _conf(self, key: str, default: Any = None) -> Any:
        value = self._config.get(key)
        return default if value is None else value

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = dict(self._json_attributes)
        attrs.update(self._attr_extra_attributes or {})
        return attrs

    # --- lifecycle --------------------------------------------------------
    async def async_added_to_jarvis(self) -> None:
        await self._async_subscribe_availability()
        await self._async_subscribe_json_attributes()
        await self._async_subscribe_topics()
        self.async_write_state()

    async def async_will_remove(self) -> None:
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:  # pragma: no cover - defensive
                _LOGGER.debug("Error unsubscribing %s", self.entity_id, exc_info=True)
        self._unsubs.clear()
        if self._expire_task is not None:
            self._expire_task.cancel()
            self._expire_task = None

    async def _async_subscribe_topics(self) -> None:
        """Subscribe to this entity's state topics (per-domain)."""

    async def _subscribe(self, topic: str | None, handler: Any) -> None:
        if not topic:
            return
        unsub = await self._client.async_subscribe(str(topic), handler, self._qos)
        self._unsubs.append(unsub)

    async def _publish(
        self,
        topic: str | None,
        payload: Any,
        retain: bool | None = None,
        qos: int | None = None,
    ) -> bool:
        if not topic:
            _LOGGER.warning("%s has no command topic for this action", self.entity_id)
            return False
        await self._client.async_publish(
            str(topic),
            payload,
            self._retain if retain is None else retain,
            self._qos if qos is None else qos,
        )
        return True

    # --- templating -------------------------------------------------------
    def _render_command(self, template_key: str, value: Any) -> Any:
        template = self._config.get(template_key)
        if not template:
            return value
        return render_value_template(
            template, str(value), default=value, extra={"value": value}
        )

    # --- availability -----------------------------------------------------
    def _parse_availability(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        raw = self._config.get("availability")
        if isinstance(raw, dict):
            raw = [raw]
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and item.get("topic"):
                    entries.append(
                        {
                            "topic": str(item["topic"]),
                            "payload_available": str(
                                item.get("payload_available", DEFAULT_PAYLOAD_AVAILABLE)
                            ),
                            "payload_not_available": str(
                                item.get(
                                    "payload_not_available",
                                    DEFAULT_PAYLOAD_NOT_AVAILABLE,
                                )
                            ),
                            "value_template": item.get("value_template"),
                        }
                    )
        topic = self._config.get("availability_topic")
        if topic:
            entries.append(
                {
                    "topic": str(topic),
                    "payload_available": str(
                        self._config.get("payload_available", DEFAULT_PAYLOAD_AVAILABLE)
                    ),
                    "payload_not_available": str(
                        self._config.get(
                            "payload_not_available", DEFAULT_PAYLOAD_NOT_AVAILABLE
                        )
                    ),
                    "value_template": self._config.get("availability_template"),
                }
            )
        return entries

    async def _async_subscribe_availability(self) -> None:
        for entry in self._availability:
            await self._subscribe(entry["topic"], self._make_availability_handler(entry))

    def _make_availability_handler(self, entry: dict[str, Any]) -> Any:
        def _handle(message: MqttMessage) -> None:
            payload = message.payload
            if entry.get("value_template"):
                rendered = render_value_template(entry["value_template"], payload)
                if rendered is None:
                    return
                payload = str(rendered)
            if payload == entry["payload_available"]:
                self._availability_state[entry["topic"]] = True
            elif payload == entry["payload_not_available"]:
                self._availability_state[entry["topic"]] = False
            else:
                return
            self._recompute_availability(entry["topic"])
            self.async_write_state()

        return _handle

    def _recompute_availability(self, latest_topic: str) -> None:
        mode = str(self._config.get("availability_mode", "latest")).lower()
        states = self._availability_state
        if mode == "all":
            self._attr_available = len(states) == len(self._availability) and all(
                states.values()
            )
        elif mode == "any":
            self._attr_available = any(states.values())
        else:  # latest
            self._attr_available = states.get(latest_topic, True)

    # --- JSON attributes --------------------------------------------------
    async def _async_subscribe_json_attributes(self) -> None:
        topic = self._config.get("json_attributes_topic")
        if not topic:
            return

        def _handle(message: MqttMessage) -> None:
            payload: Any = message.payload
            template = self._config.get("json_attributes_template")
            if template:
                payload = render_value_template(template, message.payload)
            try:
                data = json.loads(payload) if isinstance(payload, str) else payload
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "%s: json_attributes payload is not JSON: %r",
                    self.entity_id, message.payload,
                )
                return
            if not isinstance(data, dict):
                return
            allow = self._config.get("json_attributes")
            if isinstance(allow, list):
                data = {k: v for k, v in data.items() if k in allow}
            self._json_attributes = data
            self.async_write_state()

        await self._subscribe(topic, _handle)

    # --- expire_after -----------------------------------------------------
    def _bump_expiry(self) -> None:
        if not self._expire_after:
            return
        if self._expire_task is not None:
            self._expire_task.cancel()

        async def _expire() -> None:
            try:
                await asyncio.sleep(self._expire_after)
            except asyncio.CancelledError:
                return
            self._attr_available = False
            self.async_write_state()

        try:
            self._expire_task = asyncio.get_running_loop().create_task(_expire())
        except RuntimeError:  # pragma: no cover - no loop (sync context)
            self._expire_task = None

    def _mark_received(self) -> None:
        """A state message arrived: refresh expiry (and un-expire)."""
        if self._expire_after and not self._availability:
            self._attr_available = True
        self._bump_expiry()


# --- sensor -----------------------------------------------------------------
class MqttSensor(MqttEntity):
    mqtt_domain = "sensor"

    async def _async_subscribe_topics(self) -> None:
        await self._subscribe(self._config.get("state_topic"), self._handle_state)

    def _handle_state(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("value_template"), message.payload, default=None
        )
        if value is None:
            return
        self._attr_state = value
        self._mark_received()
        self.async_write_state()


class MqttBinarySensor(MqttEntity):
    mqtt_domain = "binary_sensor"

    async def _async_subscribe_topics(self) -> None:
        await self._subscribe(self._config.get("state_topic"), self._handle_state)

    def _handle_state(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("value_template"), message.payload, default=None
        )
        if value is None:
            return
        value = str(value)
        payload_on = str(self._conf("payload_on", DEFAULT_PAYLOAD_ON))
        payload_off = str(self._conf("payload_off", DEFAULT_PAYLOAD_OFF))
        if value == payload_on:
            self._attr_state = STATE_ON
        elif value == payload_off:
            self._attr_state = STATE_OFF
        else:
            _LOGGER.debug("%s: unmatched binary payload %r", self.entity_id, value)
            return
        self._mark_received()
        self.async_write_state()


# --- switch-like ------------------------------------------------------------
class MqttToggleEntity(MqttEntity):
    """Shared on/off machinery for switch, fan and light (default schema)."""

    mqtt_domain = "switch"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._optimistic = bool(
            self._config.get("optimistic", not self._config.get("state_topic"))
        )

    async def _async_subscribe_topics(self) -> None:
        await self._subscribe(self._config.get("state_topic"), self._handle_state)

    def _handle_state(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("state_value_template")
            or self._config.get("value_template"),
            message.payload,
            default=None,
        )
        if value is None:
            return
        value = str(value)
        state_on = str(
            self._conf("state_on", self._conf("payload_on", DEFAULT_PAYLOAD_ON))
        )
        state_off = str(
            self._conf("state_off", self._conf("payload_off", DEFAULT_PAYLOAD_OFF))
        )
        if value == state_on:
            self._attr_state = STATE_ON
        elif value == state_off:
            self._attr_state = STATE_OFF
        else:
            _LOGGER.debug("%s: unmatched state payload %r", self.entity_id, value)
            return
        self._mark_received()
        self.async_write_state()

    @property
    def is_on(self) -> bool:
        return self._attr_state == STATE_ON

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._publish(
            self._config.get("command_topic"),
            self._conf("payload_on", DEFAULT_PAYLOAD_ON),
        )
        if self._optimistic:
            self._attr_state = STATE_ON
            self.async_write_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._publish(
            self._config.get("command_topic"),
            self._conf("payload_off", DEFAULT_PAYLOAD_OFF),
        )
        if self._optimistic:
            self._attr_state = STATE_OFF
            self.async_write_state()

    async def async_toggle(self, **kwargs: Any) -> None:
        if self.is_on:
            await self.async_turn_off()
        else:
            await self.async_turn_on()


class MqttSwitch(MqttToggleEntity):
    mqtt_domain = "switch"


class MqttFan(MqttToggleEntity):
    mqtt_domain = "fan"

    async def _async_subscribe_topics(self) -> None:
        await super()._async_subscribe_topics()
        await self._subscribe(
            self._config.get("percentage_state_topic"), self._handle_percentage
        )

    def _handle_percentage(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("percentage_value_template"), message.payload, default=None
        )
        percentage = _as_int(value)
        if percentage is None:
            return
        self._attr_extra_attributes["percentage"] = percentage
        self.async_write_state()

    async def async_set_percentage(self, percentage: int, **kwargs: Any) -> None:
        payload = self._render_command("percentage_command_template", int(percentage))
        if await self._publish(self._config.get("percentage_command_topic"), payload):
            self._attr_extra_attributes["percentage"] = int(percentage)
            if self._optimistic:
                self._attr_state = STATE_ON if int(percentage) else STATE_OFF
            self.async_write_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        percentage = kwargs.get("percentage")
        await super().async_turn_on()
        if percentage is not None:
            await self.async_set_percentage(int(percentage))


class MqttSiren(MqttToggleEntity):
    mqtt_domain = "siren"


# --- light ------------------------------------------------------------------
class MqttLight(MqttEntity):
    """Supports the `default` (topic-per-attribute) and `json` schemas."""

    mqtt_domain = "light"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._schema = str(self._config.get("schema", "default")).lower()
        self._brightness_scale = (
            _as_int(self._config.get("brightness_scale"), DEFAULT_BRIGHTNESS_SCALE)
            or DEFAULT_BRIGHTNESS_SCALE
        )
        self._optimistic = bool(
            self._config.get("optimistic", not self._config.get("state_topic"))
        )
        self._attr_state = STATE_UNKNOWN

    # --- state ------------------------------------------------------------
    async def _async_subscribe_topics(self) -> None:
        if self._schema == "json":
            await self._subscribe(self._config.get("state_topic"), self._handle_json_state)
            return
        await self._subscribe(self._config.get("state_topic"), self._handle_state)
        await self._subscribe(
            self._config.get("brightness_state_topic"), self._handle_brightness
        )
        await self._subscribe(self._config.get("rgb_state_topic"), self._handle_rgb)
        await self._subscribe(
            self._config.get("color_temp_state_topic"), self._handle_color_temp
        )

    def _handle_state(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("state_value_template")
            or self._config.get("value_template"),
            message.payload,
            default=None,
        )
        if value is None:
            return
        value = str(value)
        if value == str(self._conf("payload_on", DEFAULT_PAYLOAD_ON)):
            self._attr_state = STATE_ON
        elif value == str(self._conf("payload_off", DEFAULT_PAYLOAD_OFF)):
            self._attr_state = STATE_OFF
        else:
            return
        self._mark_received()
        self.async_write_state()

    def _handle_brightness(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("brightness_value_template"), message.payload, default=None
        )
        raw = _as_float(value)
        if raw is None:
            return
        self._set_brightness_from_device(raw)
        self.async_write_state()

    def _handle_rgb(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("rgb_value_template"), message.payload, default=None
        )
        if value is None:
            return
        parts = [p for p in str(value).replace(";", ",").split(",") if p.strip()]
        if len(parts) < 3:
            return
        rgb = tuple(_as_int(p, 0) or 0 for p in parts[:3])
        self._attr_extra_attributes[ATTR_RGB_COLOR] = list(rgb)
        self.async_write_state()

    def _handle_color_temp(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("color_temp_value_template"), message.payload, default=None
        )
        mireds = _as_float(value)
        if mireds is None:
            return
        self._attr_extra_attributes[ATTR_COLOR_TEMP_KELVIN] = _mireds_to_kelvin(mireds)
        self.async_write_state()

    def _handle_json_state(self, message: MqttMessage) -> None:
        data = message.json()
        if not isinstance(data, dict):
            return
        state = str(data.get("state", "")).upper()
        if state == str(self._conf("payload_on", DEFAULT_PAYLOAD_ON)).upper():
            self._attr_state = STATE_ON
        elif state == str(self._conf("payload_off", DEFAULT_PAYLOAD_OFF)).upper():
            self._attr_state = STATE_OFF
        if "brightness" in data:
            raw = _as_float(data["brightness"])
            if raw is not None:
                self._set_brightness_from_device(raw)
        color = data.get("color")
        if isinstance(color, dict) and {"r", "g", "b"} <= set(color):
            self._attr_extra_attributes[ATTR_RGB_COLOR] = [
                _as_int(color["r"], 0) or 0,
                _as_int(color["g"], 0) or 0,
                _as_int(color["b"], 0) or 0,
            ]
        if data.get("color_temp") is not None:
            mireds = _as_float(data["color_temp"])
            if mireds is not None:
                self._attr_extra_attributes[ATTR_COLOR_TEMP_KELVIN] = _mireds_to_kelvin(
                    mireds
                )
        self._mark_received()
        self.async_write_state()

    def _set_brightness_from_device(self, raw: float) -> None:
        brightness = int(round(raw * 255 / self._brightness_scale))
        self._attr_extra_attributes[ATTR_BRIGHTNESS] = max(0, min(255, brightness))

    def _scale_brightness(self, brightness: int) -> int:
        scaled = int(round(int(brightness) * self._brightness_scale / 255))
        return max(0, min(self._brightness_scale, scaled))

    # --- commands ---------------------------------------------------------
    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get("brightness")
        rgb = kwargs.get("rgb_color")
        kelvin = kwargs.get("color_temp_kelvin")
        transition = kwargs.get("transition")

        if self._schema == "json":
            payload: dict[str, Any] = {"state": str(self._conf("payload_on", "ON"))}
            if brightness is not None:
                payload["brightness"] = self._scale_brightness(int(brightness))
            if rgb is not None and len(tuple(rgb)) >= 3:
                payload["color"] = {
                    "r": int(rgb[0]),
                    "g": int(rgb[1]),
                    "b": int(rgb[2]),
                }
            if kelvin is not None:
                payload["color_temp"] = _kelvin_to_mireds(float(kelvin))
            if transition is not None:
                payload["transition"] = _as_float(transition)
            await self._publish(self._config.get("command_topic"), payload)
        else:
            if brightness is not None and self._config.get("brightness_command_topic"):
                await self._publish(
                    self._config["brightness_command_topic"],
                    self._render_command(
                        "brightness_command_template", self._scale_brightness(int(brightness))
                    ),
                )
            if rgb is not None and self._config.get("rgb_command_topic"):
                rgb_payload = ",".join(str(int(component)) for component in tuple(rgb)[:3])
                await self._publish(
                    self._config["rgb_command_topic"],
                    self._render_command("rgb_command_template", rgb_payload),
                )
            if kelvin is not None and self._config.get("color_temp_command_topic"):
                await self._publish(
                    self._config["color_temp_command_topic"],
                    self._render_command(
                        "color_temp_command_template", _kelvin_to_mireds(float(kelvin))
                    ),
                )
            on_command_type = str(self._config.get("on_command_type", "last")).lower()
            skip_on = (
                on_command_type == "brightness"
                and brightness is not None
                and self._config.get("brightness_command_topic")
            )
            if not skip_on:
                await self._publish(
                    self._config.get("command_topic"),
                    self._conf("payload_on", DEFAULT_PAYLOAD_ON),
                )

        if brightness is not None:
            self._attr_extra_attributes[ATTR_BRIGHTNESS] = max(0, min(255, int(brightness)))
        if rgb is not None:
            self._attr_extra_attributes[ATTR_RGB_COLOR] = [int(c) for c in tuple(rgb)[:3]]
        if kelvin is not None:
            self._attr_extra_attributes[ATTR_COLOR_TEMP_KELVIN] = int(kelvin)
        if self._optimistic or brightness is not None or rgb is not None:
            if self._optimistic:
                self._attr_state = STATE_ON
            self.async_write_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self._schema == "json":
            payload: dict[str, Any] = {"state": str(self._conf("payload_off", "OFF"))}
            if kwargs.get("transition") is not None:
                payload["transition"] = _as_float(kwargs["transition"])
            await self._publish(self._config.get("command_topic"), payload)
        else:
            await self._publish(
                self._config.get("command_topic"),
                self._conf("payload_off", DEFAULT_PAYLOAD_OFF),
            )
        if self._optimistic:
            self._attr_state = STATE_OFF
            self.async_write_state()

    async def async_toggle(self, **kwargs: Any) -> None:
        if self._attr_state == STATE_ON:
            await self.async_turn_off()
        else:
            await self.async_turn_on()


# --- cover ------------------------------------------------------------------
class MqttCover(MqttEntity):
    mqtt_domain = "cover"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._position_open = _as_float(self._config.get("position_open"), 100.0) or 100.0
        self._position_closed = _as_float(self._config.get("position_closed"), 0.0) or 0.0
        self._optimistic = bool(
            self._config.get(
                "optimistic",
                not (
                    self._config.get("state_topic") or self._config.get("position_topic")
                ),
            )
        )

    async def _async_subscribe_topics(self) -> None:
        await self._subscribe(self._config.get("state_topic"), self._handle_state)
        await self._subscribe(self._config.get("position_topic"), self._handle_position)

    def _handle_state(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("value_template"), message.payload, default=None
        )
        if value is None:
            return
        value = str(value)
        mapping = {
            str(self._conf("state_open", STATE_OPEN)): STATE_OPEN,
            str(self._conf("state_closed", STATE_CLOSED)): STATE_CLOSED,
            str(self._conf("state_opening", STATE_OPENING)): STATE_OPENING,
            str(self._conf("state_closing", STATE_CLOSING)): STATE_CLOSING,
        }
        state = mapping.get(value) or mapping.get(value.lower())
        if state is None:
            return
        self._attr_state = state
        self._mark_received()
        self.async_write_state()

    def _handle_position(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("position_template") or self._config.get("value_template"),
            message.payload,
            default=None,
        )
        raw = _as_float(value)
        if raw is None:
            return
        span = self._position_open - self._position_closed or 1.0
        percent = int(round((raw - self._position_closed) * 100 / span))
        percent = max(0, min(100, percent))
        self._attr_extra_attributes[ATTR_POSITION] = percent
        if not self._config.get("state_topic"):
            self._attr_state = STATE_CLOSED if percent == 0 else STATE_OPEN
        self._mark_received()
        self.async_write_state()

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._publish(
            self._config.get("command_topic"), self._conf("payload_open", "OPEN")
        )
        if self._optimistic:
            self._attr_state = STATE_OPEN
            self._attr_extra_attributes[ATTR_POSITION] = 100
            self.async_write_state()

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._publish(
            self._config.get("command_topic"), self._conf("payload_close", "CLOSE")
        )
        if self._optimistic:
            self._attr_state = STATE_CLOSED
            self._attr_extra_attributes[ATTR_POSITION] = 0
            self.async_write_state()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._publish(
            self._config.get("command_topic"), self._conf("payload_stop", "STOP")
        )

    async def async_set_cover_position(self, position: int, **kwargs: Any) -> None:
        percent = max(0, min(100, int(position)))
        span = self._position_open - self._position_closed
        raw = self._position_closed + span * percent / 100
        payload = self._render_command("set_position_template", _format_number(raw))
        topic = self._config.get("set_position_topic") or self._config.get("command_topic")
        if await self._publish(topic, payload):
            self._attr_extra_attributes[ATTR_POSITION] = percent
            if self._optimistic or not self._config.get("state_topic"):
                self._attr_state = STATE_CLOSED if percent == 0 else STATE_OPEN
            self.async_write_state()


# --- climate ----------------------------------------------------------------
class MqttClimate(MqttEntity):
    mqtt_domain = "climate"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        modes = self._config.get("modes") or ["off", "heat", "cool", "auto"]
        self._attr_extra_attributes["hvac_modes"] = list(modes)
        if self._config.get("fan_modes"):
            self._attr_extra_attributes["fan_modes"] = list(self._config["fan_modes"])
        for key, attr in (
            ("min_temp", "min_temp"),
            ("max_temp", "max_temp"),
            ("temp_step", "target_temp_step"),
            ("temperature_unit", "temperature_unit"),
        ):
            if self._config.get(key) is not None:
                self._attr_extra_attributes[attr] = self._config[key]
        self._attr_state = STATE_UNKNOWN

    async def _async_subscribe_topics(self) -> None:
        await self._subscribe(self._config.get("mode_state_topic"), self._handle_mode)
        await self._subscribe(
            self._config.get("temperature_state_topic"), self._handle_target_temp
        )
        await self._subscribe(
            self._config.get("current_temperature_topic"), self._handle_current_temp
        )
        await self._subscribe(self._config.get("fan_mode_state_topic"), self._handle_fan_mode)

    def _handle_mode(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("mode_state_template"), message.payload, default=None
        )
        if value is None:
            return
        self._attr_state = str(value)
        self._attr_extra_attributes[ATTR_HVAC_MODE] = str(value)
        self._mark_received()
        self.async_write_state()

    def _handle_target_temp(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("temperature_state_template"), message.payload, default=None
        )
        temperature = _as_float(value)
        if temperature is None:
            return
        self._attr_extra_attributes[ATTR_TEMPERATURE] = temperature
        self.async_write_state()

    def _handle_current_temp(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("current_temperature_template"), message.payload, default=None
        )
        temperature = _as_float(value)
        if temperature is None:
            return
        self._attr_extra_attributes[ATTR_CURRENT_TEMPERATURE] = temperature
        self.async_write_state()

    def _handle_fan_mode(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("fan_mode_state_template"), message.payload, default=None
        )
        if value is None:
            return
        self._attr_extra_attributes["fan_mode"] = str(value)
        self.async_write_state()

    async def async_set_temperature(self, temperature: float, **kwargs: Any) -> None:
        payload = self._render_command(
            "temperature_command_template", _format_number(temperature)
        )
        if await self._publish(self._config.get("temperature_command_topic"), payload):
            self._attr_extra_attributes[ATTR_TEMPERATURE] = float(temperature)
            self.async_write_state()

    async def async_set_hvac_mode(self, hvac_mode: str, **kwargs: Any) -> None:
        payload = self._render_command("mode_command_template", hvac_mode)
        if await self._publish(self._config.get("mode_command_topic"), payload):
            self._attr_state = str(hvac_mode)
            self._attr_extra_attributes[ATTR_HVAC_MODE] = str(hvac_mode)
            self.async_write_state()

    async def async_set_fan_mode(self, fan_mode: str, **kwargs: Any) -> None:
        payload = self._render_command("fan_mode_command_template", fan_mode)
        if await self._publish(self._config.get("fan_mode_command_topic"), payload):
            self._attr_extra_attributes["fan_mode"] = str(fan_mode)
            self.async_write_state()


# --- lock -------------------------------------------------------------------
class MqttLock(MqttEntity):
    mqtt_domain = "lock"

    async def _async_subscribe_topics(self) -> None:
        await self._subscribe(self._config.get("state_topic"), self._handle_state)

    def _handle_state(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("value_template"), message.payload, default=None
        )
        if value is None:
            return
        value = str(value)
        if value == str(self._conf("state_locked", "LOCKED")):
            self._attr_state = STATE_LOCKED
        elif value == str(self._conf("state_unlocked", "UNLOCKED")):
            self._attr_state = STATE_UNLOCKED
        else:
            return
        self._mark_received()
        self.async_write_state()

    async def async_lock(self, **kwargs: Any) -> None:
        await self._publish(
            self._config.get("command_topic"), self._conf("payload_lock", "LOCK")
        )

    async def async_unlock(self, **kwargs: Any) -> None:
        await self._publish(
            self._config.get("command_topic"), self._conf("payload_unlock", "UNLOCK")
        )


# --- button / number / select / text ----------------------------------------
class MqttButton(MqttEntity):
    mqtt_domain = "button"

    async def async_press(self, **kwargs: Any) -> None:
        payload = self._render_command(
            "command_template", self._conf("payload_press", "PRESS")
        )
        if await self._publish(self._config.get("command_topic"), payload):
            import time

            self._attr_state = time.strftime("%Y-%m-%dT%H:%M:%S")
            self.async_write_state()


class MqttNumber(MqttEntity):
    mqtt_domain = "number"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for key, default in (("min", 0), ("max", 100), ("step", 1)):
            value = self._config.get(key, default)
            self._attr_extra_attributes[key] = _as_float(value, default)
        self._optimistic = bool(
            self._config.get("optimistic", not self._config.get("state_topic"))
        )

    async def _async_subscribe_topics(self) -> None:
        await self._subscribe(self._config.get("state_topic"), self._handle_state)

    def _handle_state(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("value_template"), message.payload, default=None
        )
        number = _as_float(value)
        if number is None:
            return
        self._attr_state = _format_number(number)
        self._mark_received()
        self.async_write_state()

    async def async_set_value(self, value: float, **kwargs: Any) -> None:
        payload = self._render_command("command_template", _format_number(value))
        if await self._publish(self._config.get("command_topic"), payload):
            if self._optimistic:
                self._attr_state = _format_number(value)
                self.async_write_state()


class MqttSelect(MqttEntity):
    mqtt_domain = "select"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._options = [str(o) for o in (self._config.get("options") or [])]
        self._attr_extra_attributes["options"] = list(self._options)
        self._optimistic = bool(
            self._config.get("optimistic", not self._config.get("state_topic"))
        )

    async def _async_subscribe_topics(self) -> None:
        await self._subscribe(self._config.get("state_topic"), self._handle_state)

    def _handle_state(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("value_template"), message.payload, default=None
        )
        if value is None:
            return
        self._attr_state = str(value)
        self._mark_received()
        self.async_write_state()

    async def async_select_option(self, option: str, **kwargs: Any) -> None:
        if self._options and str(option) not in self._options:
            raise ValueError(
                f"{self.entity_id}: {option!r} is not one of {self._options}"
            )
        payload = self._render_command("command_template", option)
        if await self._publish(self._config.get("command_topic"), payload):
            if self._optimistic:
                self._attr_state = str(option)
                self.async_write_state()


class MqttText(MqttEntity):
    mqtt_domain = "text"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._optimistic = bool(
            self._config.get("optimistic", not self._config.get("state_topic"))
        )

    async def _async_subscribe_topics(self) -> None:
        await self._subscribe(self._config.get("state_topic"), self._handle_state)

    def _handle_state(self, message: MqttMessage) -> None:
        value = render_value_template(
            self._config.get("value_template"), message.payload, default=None
        )
        if value is None:
            return
        self._attr_state = str(value)
        self._mark_received()
        self.async_write_state()

    async def async_set_value(self, value: str, **kwargs: Any) -> None:
        payload = self._render_command("command_template", value)
        if await self._publish(self._config.get("command_topic"), payload):
            if self._optimistic:
                self._attr_state = str(value)
                self.async_write_state()


# component name (both YAML key and discovery topic segment) -> class
ENTITY_CLASSES: dict[str, type[MqttEntity]] = {
    "binary_sensor": MqttBinarySensor,
    "button": MqttButton,
    "climate": MqttClimate,
    "cover": MqttCover,
    "fan": MqttFan,
    "light": MqttLight,
    "lock": MqttLock,
    "number": MqttNumber,
    "select": MqttSelect,
    "sensor": MqttSensor,
    "siren": MqttSiren,
    "switch": MqttSwitch,
    "text": MqttText,
}

SUPPORTED_COMPONENTS = tuple(sorted(ENTITY_CLASSES))


def create_entity(
    component: str,
    client: MqttClientBase,
    config: dict[str, Any],
    unique_id: str | None = None,
    discovery_id: str | None = None,
) -> MqttEntity | None:
    """Instantiate the class for a component, or None if unsupported."""
    cls = ENTITY_CLASSES.get(component)
    if cls is None:
        return None
    return cls(client, config, unique_id=unique_id, discovery_id=discovery_id)
