"""Philips Hue integration — bridge lights and rooms over HTTP.

    hue:
      host: 192.168.1.20
      api_key: !secret hue_key
      scan_interval: 15
      version: 2      # optional; auto-detected (v2 first, then v1)
      groups: true    # expose rooms/groups as light entities too

Both bridge APIs are supported and normalised to the same entity contract:
`async_turn_on(brightness=, color_temp_kelvin=, rgb_color=, transition=)`,
`async_turn_off()`, `async_toggle()`.

Tests inject transport with::

    jarvis.data["hue"] = {"transport": httpx.MockTransport(handler)}
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from ...const import STATE_OFF, STATE_ON
from ...entity import Entity, EntityPlatform

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "hue"

DEFAULT_SCAN_INTERVAL = 15.0
DEFAULT_TIMEOUT = 10.0

# Hue v1 brightness is 1-254; Jarvis (like HA) uses 0-255.
V1_MAX_BRI = 254
MIN_MIREK = 153
MAX_MIREK = 500


# ---------------------------------------------------------------------------
# colour maths (sRGB <-> CIE xy, Philips' Wide RGB D65 matrices)
# ---------------------------------------------------------------------------
def rgb_to_xy(red: float, green: float, blue: float) -> tuple[float, float]:
    def gamma(channel: float) -> float:
        channel = max(0.0, min(1.0, channel / 255.0))
        return ((channel + 0.055) / 1.055) ** 2.4 if channel > 0.04045 else channel / 12.92

    r, g, b = gamma(red), gamma(green), gamma(blue)
    x = r * 0.664511 + g * 0.154324 + b * 0.162028
    y = r * 0.283881 + g * 0.668433 + b * 0.047685
    z = r * 0.000088 + g * 0.072310 + b * 0.986039
    total = x + y + z
    if total <= 0:
        return (0.0, 0.0)
    return (round(x / total, 4), round(y / total, 4))


def xy_to_rgb(x: float, y: float, brightness: float = 1.0) -> tuple[int, int, int]:
    if y == 0:
        return (0, 0, 0)
    z = 1.0 - x - y
    big_y = brightness
    big_x = (big_y / y) * x
    big_z = (big_y / y) * z

    r = big_x * 1.656492 - big_y * 0.354851 - big_z * 0.255038
    g = -big_x * 0.707196 + big_y * 1.655397 + big_z * 0.036152
    b = big_x * 0.051713 - big_y * 0.121364 + big_z * 1.011530

    def reverse_gamma(channel: float) -> int:
        channel = max(0.0, channel)
        channel = (
            1.055 * (channel ** (1 / 2.4)) - 0.055 if channel > 0.0031308 else 12.92 * channel
        )
        return int(max(0, min(255, round(channel * 255))))

    largest = max(r, g, b, 1.0)
    return (reverse_gamma(r / largest), reverse_gamma(g / largest), reverse_gamma(b / largest))


def kelvin_to_mirek(kelvin: float) -> int:
    kelvin = max(1.0, float(kelvin))
    return int(max(MIN_MIREK, min(MAX_MIREK, round(1_000_000 / kelvin))))


def mirek_to_kelvin(mirek: float) -> int:
    mirek = max(1.0, float(mirek))
    return int(round(1_000_000 / mirek))


def _clamp255(value: Any) -> int:
    return int(max(0, min(255, round(float(value)))))


# ---------------------------------------------------------------------------
# bridge
# ---------------------------------------------------------------------------
class HueError(Exception):
    """The bridge said no."""


class HueBridge:
    """Talks to one bridge and normalises v1/v2 payloads to one shape."""

    def __init__(
        self,
        jarvis: "Jarvis",
        client: httpx.AsyncClient,
        host: str,
        api_key: str,
        version: int | None = None,
        include_groups: bool = True,
    ) -> None:
        self.jarvis = jarvis
        self.client = client
        self.host = str(host).rstrip("/")
        self.api_key = api_key
        self.version = version
        self.include_groups = include_groups
        # resource_key -> normalised device dict
        self.devices: dict[str, dict[str, Any]] = {}
        self.subscribers: list["HueLight"] = []

    # -- urls --------------------------------------------------------------
    @property
    def _v2_base(self) -> str:
        if self.host.startswith(("http://", "https://")):
            return self.host
        return f"https://{self.host}"

    @property
    def _v1_base(self) -> str:
        if self.host.startswith(("http://", "https://")):
            return self.host
        return f"http://{self.host}"

    def _v2_headers(self) -> dict[str, str]:
        return {"hue-application-key": self.api_key}

    # -- http --------------------------------------------------------------
    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        response = await self.client.request(method, url, **kwargs)
        if response.status_code >= 400:
            raise HueError(f"{method} {url} -> HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise HueError(f"{method} {url} -> invalid JSON") from exc

    # -- discovery / polling ----------------------------------------------
    async def async_detect_version(self) -> int:
        if self.version in (1, 2):
            return self.version
        try:
            await self._request(
                "GET", f"{self._v2_base}/clip/v2/resource/light", headers=self._v2_headers()
            )
            self.version = 2
        except (HueError, httpx.HTTPError):
            _LOGGER.debug("Hue v2 unavailable on %s; falling back to v1", self.host)
            self.version = 1
        return self.version

    async def async_fetch(self) -> dict[str, dict[str, Any]]:
        """Refresh every light/group. Returns the normalised device map."""
        version = self.version or await self.async_detect_version()
        devices = (
            await self._fetch_v2() if version == 2 else await self._fetch_v1()
        )
        self.devices = devices
        return devices

    async def _fetch_v2(self) -> dict[str, dict[str, Any]]:
        devices: dict[str, dict[str, Any]] = {}
        payload = await self._request(
            "GET", f"{self._v2_base}/clip/v2/resource/light", headers=self._v2_headers()
        )
        for item in payload.get("data", []) or []:
            resource_id = item.get("id")
            if not resource_id:
                continue
            devices[f"light:{resource_id}"] = _normalise_v2_light(item)

        if not self.include_groups:
            return devices

        try:
            rooms = await self._request(
                "GET", f"{self._v2_base}/clip/v2/resource/room", headers=self._v2_headers()
            )
            grouped = await self._request(
                "GET",
                f"{self._v2_base}/clip/v2/resource/grouped_light",
                headers=self._v2_headers(),
            )
        except (HueError, httpx.HTTPError) as exc:
            _LOGGER.debug("Hue v2 groups unavailable: %s", exc)
            return devices

        by_id = {item.get("id"): item for item in grouped.get("data", []) or []}
        for room in rooms.get("data", []) or []:
            name = (room.get("metadata") or {}).get("name")
            for service in room.get("services", []) or []:
                if service.get("rtype") != "grouped_light":
                    continue
                group = by_id.get(service.get("rid"))
                if group is None:
                    continue
                key = f"group:{group['id']}"
                devices[key] = _normalise_v2_group(group, name or f"Group {group['id']}")
        return devices

    async def _fetch_v1(self) -> dict[str, dict[str, Any]]:
        devices: dict[str, dict[str, Any]] = {}
        lights = await self._request("GET", f"{self._v1_base}/api/{self.api_key}/lights")
        if isinstance(lights, list):  # v1 error payloads are lists
            raise HueError(f"bridge error: {lights}")
        for resource_id, item in (lights or {}).items():
            devices[f"light:{resource_id}"] = _normalise_v1_light(resource_id, item)

        if not self.include_groups:
            return devices
        try:
            groups = await self._request("GET", f"{self._v1_base}/api/{self.api_key}/groups")
        except (HueError, httpx.HTTPError) as exc:
            _LOGGER.debug("Hue v1 groups unavailable: %s", exc)
            return devices
        if isinstance(groups, dict):
            for resource_id, item in groups.items():
                devices[f"group:{resource_id}"] = _normalise_v1_group(resource_id, item)
        return devices

    # -- commands ----------------------------------------------------------
    async def async_set_state(
        self,
        key: str,
        on: bool,
        brightness: int | None = None,
        color_temp_kelvin: int | None = None,
        rgb_color: tuple[int, int, int] | None = None,
        transition: float | None = None,
    ) -> dict[str, Any]:
        """Send a command; returns the JSON body that was sent (handy in tests)."""
        kind, _, resource_id = key.partition(":")
        version = self.version or await self.async_detect_version()
        if version == 2:
            body = _build_v2_body(on, brightness, color_temp_kelvin, rgb_color, transition)
            resource = "light" if kind == "light" else "grouped_light"
            url = f"{self._v2_base}/clip/v2/resource/{resource}/{resource_id}"
            await self._request("PUT", url, json=body, headers=self._v2_headers())
            return body

        body = _build_v1_body(on, brightness, color_temp_kelvin, rgb_color, transition)
        if kind == "light":
            url = f"{self._v1_base}/api/{self.api_key}/lights/{resource_id}/state"
        else:
            url = f"{self._v1_base}/api/{self.api_key}/groups/{resource_id}/action"
        await self._request("PUT", url, json=body)
        return body


def _normalise_v2_light(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") or {}
    dimming = item.get("dimming") or {}
    color_temp = item.get("color_temperature") or {}
    color = item.get("color") or {}
    xy = color.get("xy") or {}
    brightness = dimming.get("brightness")
    device: dict[str, Any] = {
        "kind": "light",
        "name": metadata.get("name") or f"Hue light {item.get('id')}",
        "on": bool((item.get("on") or {}).get("on")),
        "brightness": _clamp255(float(brightness) * 255 / 100) if brightness is not None else None,
        "color_temp_kelvin": (
            mirek_to_kelvin(color_temp["mirek"]) if color_temp.get("mirek") else None
        ),
        "rgb_color": (
            xy_to_rgb(xy["x"], xy["y"]) if xy.get("x") is not None and xy.get("y") is not None
            else None
        ),
        "reachable": True,
        "model": item.get("product_data", {}).get("product_name") or metadata.get("archetype"),
    }
    return device


def _normalise_v2_group(item: dict[str, Any], name: str) -> dict[str, Any]:
    dimming = item.get("dimming") or {}
    brightness = dimming.get("brightness")
    return {
        "kind": "group",
        "name": name,
        "on": bool((item.get("on") or {}).get("on")),
        "brightness": _clamp255(float(brightness) * 255 / 100) if brightness is not None else None,
        "color_temp_kelvin": None,
        "rgb_color": None,
        "reachable": True,
        "model": "Hue room",
    }


def _normalise_v1_light(resource_id: str, item: dict[str, Any]) -> dict[str, Any]:
    state = item.get("state") or {}
    xy = state.get("xy")
    bri = state.get("bri")
    return {
        "kind": "light",
        "name": item.get("name") or f"Hue light {resource_id}",
        "on": bool(state.get("on")),
        "brightness": _clamp255(float(bri) * 255 / V1_MAX_BRI) if bri is not None else None,
        "color_temp_kelvin": mirek_to_kelvin(state["ct"]) if state.get("ct") else None,
        "rgb_color": xy_to_rgb(xy[0], xy[1]) if isinstance(xy, (list, tuple)) and len(xy) == 2
        else None,
        "reachable": bool(state.get("reachable", True)),
        "model": item.get("modelid"),
    }


def _normalise_v1_group(resource_id: str, item: dict[str, Any]) -> dict[str, Any]:
    action = item.get("action") or {}
    bri = action.get("bri")
    return {
        "kind": "group",
        "name": item.get("name") or f"Hue group {resource_id}",
        "on": bool((item.get("state") or {}).get("any_on", action.get("on"))),
        "brightness": _clamp255(float(bri) * 255 / V1_MAX_BRI) if bri is not None else None,
        "color_temp_kelvin": mirek_to_kelvin(action["ct"]) if action.get("ct") else None,
        "rgb_color": None,
        "reachable": True,
        "model": "Hue group",
    }


def _build_v2_body(
    on: bool,
    brightness: int | None,
    color_temp_kelvin: int | None,
    rgb_color: tuple[int, int, int] | None,
    transition: float | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"on": {"on": bool(on)}}
    if brightness is not None:
        body["dimming"] = {"brightness": round(_clamp255(brightness) * 100 / 255, 2)}
    if color_temp_kelvin is not None:
        body["color_temperature"] = {"mirek": kelvin_to_mirek(color_temp_kelvin)}
    if rgb_color is not None:
        x, y = rgb_to_xy(*rgb_color)
        body["color"] = {"xy": {"x": x, "y": y}}
    if transition is not None:
        body["dynamics"] = {"duration": int(float(transition) * 1000)}
    return body


def _build_v1_body(
    on: bool,
    brightness: int | None,
    color_temp_kelvin: int | None,
    rgb_color: tuple[int, int, int] | None,
    transition: float | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"on": bool(on)}
    if brightness is not None:
        body["bri"] = int(max(1, min(V1_MAX_BRI, round(_clamp255(brightness) * V1_MAX_BRI / 255))))
    if color_temp_kelvin is not None:
        body["ct"] = kelvin_to_mirek(color_temp_kelvin)
    if rgb_color is not None:
        body["xy"] = list(rgb_to_xy(*rgb_color))
    if transition is not None:
        body["transitiontime"] = int(float(transition) * 10)
    return body


# ---------------------------------------------------------------------------
# entity
# ---------------------------------------------------------------------------
class HueLight(Entity):
    """One Hue light or room, exposed as a `light` entity."""

    def __init__(self, bridge: HueBridge, key: str, device: dict[str, Any]) -> None:
        self._bridge = bridge
        self._key = key
        self._device = dict(device)
        self._attr_name = device.get("name") or key
        self._attr_unique_id = f"hue_{bridge.host}_{key}".replace(":", "_")
        self._attr_should_poll = False
        self._apply(device)

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": [self._attr_unique_id],
            "name": self._attr_name,
            "manufacturer": "Signify (Philips Hue)",
            "model": self._device.get("model") or "Hue light",
        }

    # -- state -------------------------------------------------------------
    def _apply(self, device: dict[str, Any]) -> None:
        self._device = dict(device)
        self._attr_state = STATE_ON if device.get("on") else STATE_OFF
        self._attr_available = bool(device.get("reachable", True))
        attributes: dict[str, Any] = {}
        if device.get("on"):
            for key in ("brightness", "color_temp_kelvin", "rgb_color"):
                if device.get(key) is not None:
                    attributes[key] = device[key]
        attributes["hue_type"] = device.get("kind", "light")
        self._attr_extra_attributes = attributes

    def apply_device(self, device: dict[str, Any]) -> None:
        self._apply(device)

    async def async_update(self) -> None:
        """Only the first entity of a bridge polls; it refreshes the rest."""
        devices = await self._bridge.async_fetch()
        for entity in self._bridge.subscribers:
            device = devices.get(entity._key)
            if device is None:
                entity._attr_available = False
            else:
                entity.apply_device(device)
            if entity is not self:
                entity.async_write_state()

    # -- commands ----------------------------------------------------------
    async def async_turn_on(self, **kwargs: Any) -> None:
        rgb = kwargs.get("rgb_color")
        await self._bridge.async_set_state(
            self._key,
            on=True,
            brightness=kwargs.get("brightness"),
            color_temp_kelvin=kwargs.get("color_temp_kelvin"),
            rgb_color=tuple(rgb) if rgb is not None else None,
            transition=kwargs.get("transition"),
        )
        device = dict(self._device)
        device["on"] = True
        for key in ("brightness", "color_temp_kelvin", "rgb_color"):
            if kwargs.get(key) is not None:
                device[key] = tuple(kwargs[key]) if key == "rgb_color" else kwargs[key]
        if kwargs.get("rgb_color") is not None:
            device["color_temp_kelvin"] = None
        self._apply(device)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._bridge.async_set_state(
            self._key, on=False, transition=kwargs.get("transition")
        )
        device = dict(self._device)
        device["on"] = False
        self._apply(device)

    async def async_toggle(self, **kwargs: Any) -> None:
        if self._attr_state == STATE_ON:
            await self.async_turn_off()
        else:
            await self.async_turn_on()


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
def create_client(jarvis: "Jarvis", timeout: float = DEFAULT_TIMEOUT) -> httpx.AsyncClient:
    """Shared client; honours an injected `client`/`transport` for tests."""
    store = jarvis.data.setdefault(DOMAIN, {})
    client = store.get("client")
    if client is not None:
        store.setdefault("owns_client", False)
        return client
    transport = store.get("transport")
    client = httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(timeout),
        # Hue v2 serves a self-signed bridge certificate.
        verify=False if transport is None else True,
    )
    store["client"] = client
    store["owns_client"] = True
    return client


def _as_bridges(config: Any) -> list[dict[str, Any]]:
    if config is None:
        return []
    if isinstance(config, dict):
        if "bridges" in config:
            return [b for b in config["bridges"] if isinstance(b, dict)]
        return [config]
    return [block for block in config if isinstance(block, dict)]


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    blocks = _as_bridges(config)
    if not blocks:
        return True

    store = jarvis.data.setdefault(DOMAIN, {})
    client = create_client(jarvis)
    if store.get("owns_client", True) and not store.get("shutdown_registered"):
        store["shutdown_registered"] = True
        jarvis.register_shutdown(client.aclose)

    bridges: list[HueBridge] = store.setdefault("bridges", [])
    platforms: dict[str, EntityPlatform] = store.setdefault("platforms", {})

    for block in blocks:
        host = block.get("host")
        if not host:
            _LOGGER.error("hue: a bridge needs a 'host'")
            continue
        bridge = HueBridge(
            jarvis,
            client,
            host,
            str(block.get("api_key") or block.get("username") or ""),
            version=block.get("version"),
            include_groups=bool(block.get("groups", True)),
        )
        bridges.append(bridge)

        try:
            devices = await bridge.async_fetch()
        except (HueError, httpx.HTTPError) as exc:
            _LOGGER.error("hue: cannot reach bridge %s: %s", host, exc)
            continue

        scan_interval = float(block.get("scan_interval", DEFAULT_SCAN_INTERVAL))
        platform = platforms.get(host)
        if platform is None:
            platform = EntityPlatform(jarvis, "light", DOMAIN, scan_interval)
            platforms[host] = platform

        entities: list[Entity] = []
        for key, device in devices.items():
            light = HueLight(bridge, key, device)
            bridge.subscribers.append(light)
            entities.append(light)
        if entities:
            entities[0]._attr_should_poll = True
            await platform.async_add_entities(entities)
        _LOGGER.info("hue: %d entities from bridge %s (v%s)", len(entities), host, bridge.version)

    return True
