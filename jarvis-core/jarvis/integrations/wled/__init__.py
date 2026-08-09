"""WLED integration — an LED controller's JSON API as a light + effect select.

    wled:
      - host: 192.168.1.30
        name: Desk Strip     # optional; defaults to the device's own name
        scan_interval: 10

Creates a `light` entity (on/off, brightness, rgb, colour temperature) and a
`select` entity for the effect list. Tests inject transport with::

    jarvis.data["wled"] = {"transport": httpx.MockTransport(handler)}
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

import httpx

from ...const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from ...entity import Entity, EntityPlatform

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "wled"

DEFAULT_SCAN_INTERVAL = 10.0
DEFAULT_TIMEOUT = 10.0


class WledError(Exception):
    """The controller did not answer usefully."""


def kelvin_to_rgb(kelvin: float) -> tuple[int, int, int]:
    """Approximate an RGB triple for a colour temperature (Tanner Helland)."""
    temp = max(1000.0, min(40000.0, float(kelvin))) / 100.0
    if temp <= 66:
        red = 255.0
        green = 99.4708025861 * math.log(temp) - 161.1195681661
        blue = 0.0 if temp <= 19 else 138.5177312231 * math.log(temp - 10) - 305.0447927307
    else:
        red = 329.698727446 * ((temp - 60) ** -0.1332047592)
        green = 288.1221695283 * ((temp - 60) ** -0.0755148492)
        blue = 255.0

    def clamp(value: float) -> int:
        return int(max(0, min(255, round(value))))

    return (clamp(red), clamp(green), clamp(blue))


def _clamp255(value: Any) -> int:
    return int(max(0, min(255, round(float(value)))))


class WledDevice:
    """One WLED controller: fetches `/json`, posts to `/json/state`."""

    def __init__(
        self, client: httpx.AsyncClient, host: str, name_override: str | None = None
    ) -> None:
        self.client = client
        self.host = str(host).rstrip("/")
        self.name_override = name_override
        self.state: dict[str, Any] = {}
        self.info: dict[str, Any] = {}
        self.effects: list[str] = []
        self.palettes: list[str] = []
        self.subscribers: list[Entity] = []

    @property
    def base_url(self) -> str:
        if self.host.startswith(("http://", "https://")):
            return self.host
        return f"http://{self.host}"

    @property
    def name(self) -> str:
        return self.name_override or self.info.get("name") or f"WLED {self.host}"

    @property
    def unique_id(self) -> str:
        return f"wled_{self.info.get('mac') or self.host}".replace(":", "").replace(".", "_")

    # -- data --------------------------------------------------------------
    @property
    def segment(self) -> dict[str, Any]:
        segments = self.state.get("seg") or []
        return segments[0] if segments else {}

    @property
    def is_on(self) -> bool:
        return bool(self.state.get("on"))

    @property
    def brightness(self) -> int | None:
        value = self.state.get("bri")
        return _clamp255(value) if value is not None else None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        colors = self.segment.get("col") or []
        if not colors or not isinstance(colors[0], (list, tuple)) or len(colors[0]) < 3:
            return None
        return tuple(_clamp255(c) for c in colors[0][:3])  # type: ignore[return-value]

    @property
    def effect(self) -> str | None:
        index = self.segment.get("fx")
        if index is None or not self.effects:
            return None
        return self.effects[index] if 0 <= int(index) < len(self.effects) else None

    # -- http --------------------------------------------------------------
    async def async_fetch(self) -> dict[str, Any]:
        response = await self.client.get(f"{self.base_url}/json")
        if response.status_code >= 400:
            raise WledError(f"GET /json -> HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise WledError("GET /json -> invalid JSON") from exc
        if not isinstance(payload, dict):
            raise WledError("GET /json -> unexpected payload")
        self.state = payload.get("state") or {}
        self.info = payload.get("info") or {}
        self.effects = list(payload.get("effects") or [])
        self.palettes = list(payload.get("palettes") or [])
        return payload

    async def async_send(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST a state fragment; merges it into the local state on success."""
        response = await self.client.post(f"{self.base_url}/json/state", json=payload)
        if response.status_code >= 400:
            raise WledError(f"POST /json/state -> HTTP {response.status_code}")
        self._merge(payload)
        return payload

    def _merge(self, payload: dict[str, Any]) -> None:
        for key, value in payload.items():
            if key == "seg":
                segments = self.state.setdefault("seg", [{}])
                if not segments:
                    segments.append({})
                for index, fragment in enumerate(value):
                    while len(segments) <= index:
                        segments.append({})
                    segments[index].update(
                        {k: v for k, v in fragment.items() if k != "id"}
                    )
            else:
                self.state[key] = value

    def notify(self, exclude: Entity | None = None) -> None:
        for entity in self.subscribers:
            refresh = getattr(entity, "apply_device", None)
            if callable(refresh):
                refresh()
            if entity is not exclude:
                entity.async_write_state()

    def notify_failure(self, exclude: Entity | None = None) -> None:
        """The controller stopped answering — nothing riding it is live.

        Only the light polls; without this the companion select would keep
        advertising the last effect it saw as though the strip were online.
        """
        for entity in self.subscribers:
            if entity is exclude:
                continue
            entity._attr_available = False
            entity.async_write_state()


class WledLight(Entity):
    """The controller's master segment as a light entity."""

    def __init__(self, device: WledDevice) -> None:
        self._device = device
        self._attr_name = device.name
        self._attr_unique_id = f"{device.unique_id}_light"
        self._attr_should_poll = True
        self.apply_device()

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": [self._device.unique_id],
            "name": self._device.name,
            "manufacturer": "WLED",
            "model": self._device.info.get("arch") or "WLED",
            "sw_version": self._device.info.get("ver"),
        }

    def apply_device(self) -> None:
        device = self._device
        self._attr_state = STATE_ON if device.is_on else STATE_OFF
        attributes: dict[str, Any] = {"effect_list": device.effects}
        if device.is_on:
            if device.brightness is not None:
                attributes["brightness"] = device.brightness
            if device.rgb_color is not None:
                attributes["rgb_color"] = list(device.rgb_color)
            if device.effect is not None:
                attributes["effect"] = device.effect
        self._attr_extra_attributes = attributes

    async def async_update(self) -> None:
        try:
            await self._device.async_fetch()
        except Exception:
            self._device.notify_failure(exclude=self)
            raise
        self._attr_available = True
        # notify() refreshes every subscriber (this entity included) and
        # writes the state of the others; the platform writes ours.
        self._device.notify(exclude=self)

    # -- commands ----------------------------------------------------------
    def _segment_payload(self, **fields: Any) -> dict[str, Any]:
        return {"seg": [{"id": self._device.segment.get("id", 0), **fields}]}

    async def async_turn_on(self, **kwargs: Any) -> None:
        payload: dict[str, Any] = {"on": True}
        if kwargs.get("brightness") is not None:
            payload["bri"] = _clamp255(kwargs["brightness"])
        if kwargs.get("transition") is not None:
            payload["tt"] = int(float(kwargs["transition"]) * 10)

        segment: dict[str, Any] = {}
        if kwargs.get("rgb_color") is not None:
            segment["col"] = [[_clamp255(c) for c in tuple(kwargs["rgb_color"])[:3]]]
        elif kwargs.get("color_temp_kelvin") is not None:
            segment["col"] = [list(kelvin_to_rgb(kwargs["color_temp_kelvin"]))]
        if kwargs.get("effect") is not None:
            index = self._effect_index(kwargs["effect"])
            if index is not None:
                segment["fx"] = index
        if segment:
            payload.update(self._segment_payload(**segment))

        await self._device.async_send(payload)
        self.apply_device()
        self._device.notify(exclude=self)

    async def async_turn_off(self, **kwargs: Any) -> None:
        payload: dict[str, Any] = {"on": False}
        if kwargs.get("transition") is not None:
            payload["tt"] = int(float(kwargs["transition"]) * 10)
        await self._device.async_send(payload)
        self.apply_device()
        self._device.notify(exclude=self)

    async def async_toggle(self, **kwargs: Any) -> None:
        if self._attr_state == STATE_ON:
            await self.async_turn_off()
        else:
            await self.async_turn_on()

    def _effect_index(self, effect: str) -> int | None:
        target = str(effect).strip().lower()
        for index, name in enumerate(self._device.effects):
            if str(name).strip().lower() == target:
                return index
        if target.isdigit():
            index = int(target)
            # A raw index the controller does not have is silently ignored by
            # WLED; refuse it here so the caller sees the mistake instead.
            if 0 <= index < len(self._device.effects) or not self._device.effects:
                return index
        _LOGGER.warning("wled: unknown effect %r", effect)
        return None


class WledEffectSelect(Entity):
    """The effect list as a `select` entity."""

    def __init__(self, device: WledDevice) -> None:
        self._device = device
        self._attr_name = f"{device.name} Effect"
        self._attr_unique_id = f"{device.unique_id}_effect"
        self._attr_should_poll = False
        self.apply_device()

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": [self._device.unique_id],
            "name": self._device.name,
            "manufacturer": "WLED",
            "model": self._device.info.get("arch") or "WLED",
        }

    def apply_device(self) -> None:
        self._attr_state = self._device.effect or STATE_UNKNOWN
        self._attr_extra_attributes = {"options": self._device.effects}

    async def async_select_option(self, option: str) -> None:
        target = str(option).strip().lower()
        index = next(
            (i for i, name in enumerate(self._device.effects) if str(name).lower() == target),
            None,
        )
        if index is None:
            raise ValueError(f"unknown WLED effect {option!r}")
        await self._device.async_send(
            {"seg": [{"id": self._device.segment.get("id", 0), "fx": index}]}
        )
        self.apply_device()
        self._device.notify(exclude=self)


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
def create_client(jarvis: "Jarvis", timeout: float = DEFAULT_TIMEOUT) -> httpx.AsyncClient:
    store = jarvis.data.setdefault(DOMAIN, {})
    client = store.get("client")
    if client is not None:
        store.setdefault("owns_client", False)
        return client
    client = httpx.AsyncClient(
        transport=store.get("transport"), timeout=httpx.Timeout(timeout)
    )
    store["client"] = client
    store["owns_client"] = True
    return client


def _as_devices(config: Any) -> list[dict[str, Any]]:
    if config is None:
        return []
    if isinstance(config, dict):
        if "devices" in config:
            return [d for d in config["devices"] if isinstance(d, dict)]
        return [config]
    return [block for block in config if isinstance(block, dict)]


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    blocks = _as_devices(config)
    if not blocks:
        return True

    store = jarvis.data.setdefault(DOMAIN, {})
    client = create_client(jarvis)
    if store.get("owns_client", True) and not store.get("shutdown_registered"):
        store["shutdown_registered"] = True
        jarvis.register_shutdown(client.aclose)

    devices: list[WledDevice] = store.setdefault("devices", [])
    platforms: dict[str, EntityPlatform] = store.setdefault("platforms", {})

    for block in blocks:
        host = block.get("host")
        if not host:
            _LOGGER.error("wled: a device needs a 'host'")
            continue
        device = WledDevice(client, host, block.get("name"))
        try:
            await device.async_fetch()
        except (WledError, httpx.HTTPError) as exc:
            _LOGGER.error("wled: cannot reach %s: %s", host, exc)
            continue
        devices.append(device)

        scan_interval = float(block.get("scan_interval", DEFAULT_SCAN_INTERVAL))
        light_platform = platforms.get("light")
        if light_platform is None:
            light_platform = EntityPlatform(jarvis, "light", DOMAIN, scan_interval)
            platforms["light"] = light_platform

        light = WledLight(device)
        device.subscribers.append(light)
        await light_platform.async_add_entities([light])

        if device.effects:
            select_platform = platforms.get("select")
            if select_platform is None:
                select_platform = EntityPlatform(jarvis, "select", DOMAIN, scan_interval)
                platforms["select"] = select_platform
            effect = WledEffectSelect(device)
            device.subscribers.append(effect)
            await select_platform.async_add_entities([effect])

        _LOGGER.info("wled: %s ready (%d effects)", device.name, len(device.effects))

    return True
