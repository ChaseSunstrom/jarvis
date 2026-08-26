"""Demo integration — a full house of fake devices, no hardware required.

    demo:

That single line gives you lights, switches, sensors, a thermostat, a
cover, a media player, a fan, a lock, a number, a select, a button and a
vacuum, spread across three areas. Every entity implements the real method
contract, so `light.turn_on`, `cover.set_cover_position`,
`media_player.volume_set` and friends all behave exactly as they would
against real hardware.

Options::

    demo:
      create_areas: true    # default: place demo devices in demo areas
      prefix: ""            # optional name prefix if you run several copies
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ...const import (
    STATE_CLOSED,
    STATE_IDLE,
    STATE_LOCKED,
    STATE_OFF,
    STATE_ON,
    STATE_OPEN,
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_UNLOCKED,
)
from ...entity import Entity, EntityPlatform

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "demo"

LIVING_ROOM = "Living Room"
KITCHEN = "Kitchen"
BEDROOM = "Bedroom"


class DemoEntity(Entity):
    """Base: a name, a stable unique id and an area suggestion."""

    def __init__(self, name: str, unique_id: str, area: str | None = None) -> None:
        self._attr_name = name
        self._attr_unique_id = f"demo_{unique_id}"
        self._attr_should_poll = False
        self.area = area

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": [self._attr_unique_id],
            "name": self._attr_name,
            "manufacturer": "Jarvis",
            "model": "Demo device",
            "sw_version": "1.0",
        }


# ---------------------------------------------------------------------------
# light / switch / fan / siren style
# ---------------------------------------------------------------------------
class DemoLight(DemoEntity):
    def __init__(
        self,
        name: str,
        unique_id: str,
        area: str | None = None,
        state: str = STATE_OFF,
        brightness: int = 180,
        supports_color: bool = True,
    ) -> None:
        super().__init__(name, unique_id, area)
        self._attr_state = state
        self._brightness = brightness
        self._color_temp_kelvin = 3000
        self._rgb_color: tuple[int, int, int] | None = (255, 180, 107)
        self._supports_color = supports_color
        self._refresh()

    def _refresh(self) -> None:
        attributes: dict[str, Any] = {"supported_color_modes": ["brightness"]}
        if self._supports_color:
            attributes["supported_color_modes"] = ["color_temp", "rgb"]
        if self._attr_state == STATE_ON:
            attributes["brightness"] = self._brightness
            if self._supports_color:
                attributes["color_temp_kelvin"] = self._color_temp_kelvin
                if self._rgb_color is not None:
                    attributes["rgb_color"] = list(self._rgb_color)
        self._attr_extra_attributes = attributes

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_state = STATE_ON
        if kwargs.get("brightness") is not None:
            self._brightness = int(kwargs["brightness"])
        if kwargs.get("color_temp_kelvin") is not None and self._supports_color:
            self._color_temp_kelvin = int(kwargs["color_temp_kelvin"])
            self._rgb_color = None
        if kwargs.get("rgb_color") is not None and self._supports_color:
            self._rgb_color = tuple(int(c) for c in kwargs["rgb_color"])[:3]  # type: ignore[assignment]
        self._refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_state = STATE_OFF
        self._refresh()

    async def async_toggle(self, **kwargs: Any) -> None:
        if self._attr_state == STATE_ON:
            await self.async_turn_off()
        else:
            await self.async_turn_on()


class DemoSwitch(DemoEntity):
    def __init__(
        self, name: str, unique_id: str, area: str | None = None, state: str = STATE_OFF
    ) -> None:
        super().__init__(name, unique_id, area)
        self._attr_state = state

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_state = STATE_ON

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_state = STATE_OFF

    async def async_toggle(self, **kwargs: Any) -> None:
        self._attr_state = STATE_OFF if self._attr_state == STATE_ON else STATE_ON


class DemoFan(DemoEntity):
    def __init__(self, name: str, unique_id: str, area: str | None = None) -> None:
        super().__init__(name, unique_id, area)
        self._attr_state = STATE_OFF
        self._percentage = 0
        self._preset_mode: str | None = None
        self._refresh()

    def _refresh(self) -> None:
        self._attr_extra_attributes = {
            "percentage": self._percentage,
            "preset_mode": self._preset_mode,
            "preset_modes": ["auto", "smart", "sleep"],
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_state = STATE_ON
        percentage = kwargs.get("percentage")
        self._percentage = int(percentage) if percentage is not None else max(self._percentage, 50)
        if kwargs.get("preset_mode") is not None:
            self._preset_mode = str(kwargs["preset_mode"])
        self._refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_state = STATE_OFF
        self._percentage = 0
        self._refresh()

    async def async_toggle(self, **kwargs: Any) -> None:
        if self._attr_state == STATE_ON:
            await self.async_turn_off()
        else:
            await self.async_turn_on()


# ---------------------------------------------------------------------------
# sensors
# ---------------------------------------------------------------------------
class DemoSensor(DemoEntity):
    def __init__(
        self,
        name: str,
        unique_id: str,
        state: Any,
        unit: str | None = None,
        device_class: str | None = None,
        area: str | None = None,
    ) -> None:
        super().__init__(name, unique_id, area)
        self._attr_state = state
        self._attr_unit_of_measurement = unit
        self._attr_device_class = device_class

    def set_value(self, value: Any) -> None:
        """Nudge the demo reading (handy in other integrations' tests)."""
        self._attr_state = value
        self.async_write_state()


class DemoBinarySensor(DemoEntity):
    def __init__(
        self,
        name: str,
        unique_id: str,
        state: str = STATE_OFF,
        device_class: str | None = None,
        area: str | None = None,
    ) -> None:
        super().__init__(name, unique_id, area)
        self._attr_state = state
        self._attr_device_class = device_class

    def set_value(self, is_on: bool) -> None:
        self._attr_state = STATE_ON if is_on else STATE_OFF
        self.async_write_state()


# ---------------------------------------------------------------------------
# climate / cover / lock
# ---------------------------------------------------------------------------
class DemoClimate(DemoEntity):
    def __init__(self, name: str, unique_id: str, area: str | None = None) -> None:
        super().__init__(name, unique_id, area)
        self._attr_state = "heat"
        self._target = 21.0
        self._current = 19.5
        self._fan_mode = "auto"
        self._refresh()

    def _refresh(self) -> None:
        self._attr_extra_attributes = {
            "temperature": self._target,
            "current_temperature": self._current,
            "hvac_mode": self._attr_state,
            "hvac_modes": ["off", "heat", "cool", "auto"],
            "fan_mode": self._fan_mode,
            "fan_modes": ["auto", "low", "medium", "high"],
            "min_temp": 7,
            "max_temp": 35,
        }

    async def async_set_temperature(self, temperature: float, **kwargs: Any) -> None:
        self._target = float(temperature)
        self._refresh()

    async def async_set_hvac_mode(self, hvac_mode: str, **kwargs: Any) -> None:
        self._attr_state = str(hvac_mode)
        self._refresh()

    async def async_set_fan_mode(self, fan_mode: str, **kwargs: Any) -> None:
        self._fan_mode = str(fan_mode)
        self._refresh()


class DemoCover(DemoEntity):
    def __init__(
        self, name: str, unique_id: str, area: str | None = None, position: int = 100
    ) -> None:
        super().__init__(name, unique_id, area)
        self._position = int(position)
        self._sync()

    def _sync(self) -> None:
        self._attr_state = STATE_OPEN if self._position > 0 else STATE_CLOSED
        self._attr_extra_attributes = {"current_position": self._position}

    async def async_open_cover(self, **kwargs: Any) -> None:
        self._position = 100
        self._sync()

    async def async_close_cover(self, **kwargs: Any) -> None:
        self._position = 0
        self._sync()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        self._sync()

    async def async_set_cover_position(self, position: int, **kwargs: Any) -> None:
        self._position = max(0, min(100, int(position)))
        self._sync()


class DemoLock(DemoEntity):
    def __init__(self, name: str, unique_id: str, area: str | None = None) -> None:
        super().__init__(name, unique_id, area)
        self._attr_state = STATE_LOCKED

    async def async_lock(self, **kwargs: Any) -> None:
        self._attr_state = STATE_LOCKED

    async def async_unlock(self, **kwargs: Any) -> None:
        self._attr_state = STATE_UNLOCKED


# ---------------------------------------------------------------------------
# media player
# ---------------------------------------------------------------------------
class DemoMediaPlayer(DemoEntity):
    TRACKS = [
        ("Blue Monday", "New Order"),
        ("Teardrop", "Massive Attack"),
        ("Nightcall", "Kavinsky"),
    ]

    def __init__(self, name: str, unique_id: str, area: str | None = None) -> None:
        super().__init__(name, unique_id, area)
        self._attr_state = STATE_IDLE
        self._track = 0
        self._volume = 0.4
        self._media: tuple[str, str] | None = None
        self._refresh()

    def _refresh(self) -> None:
        title, artist = self.TRACKS[self._track % len(self.TRACKS)]
        attributes: dict[str, Any] = {
            "volume_level": round(self._volume, 3),
            "media_title": title,
            "media_artist": artist,
            "source_list": ["Spotify", "Radio", "Bluetooth"],
        }
        if self._media is not None:
            attributes["media_content_type"], attributes["media_content_id"] = self._media
        self._attr_extra_attributes = attributes

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_state = STATE_IDLE
        self._refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_state = STATE_OFF
        self._refresh()

    async def async_media_play(self, **kwargs: Any) -> None:
        self._attr_state = STATE_PLAYING
        self._refresh()

    async def async_media_pause(self, **kwargs: Any) -> None:
        self._attr_state = STATE_PAUSED
        self._refresh()

    async def async_media_stop(self, **kwargs: Any) -> None:
        self._attr_state = STATE_IDLE
        self._refresh()

    async def async_media_next_track(self, **kwargs: Any) -> None:
        self._track += 1
        self._refresh()

    async def async_media_previous_track(self, **kwargs: Any) -> None:
        self._track -= 1
        self._refresh()

    async def async_volume_set(self, volume_level: float, **kwargs: Any) -> None:
        self._volume = max(0.0, min(1.0, float(volume_level)))
        self._refresh()

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        self._media = (str(media_type), str(media_id))
        self._attr_state = STATE_PLAYING
        self._refresh()


# ---------------------------------------------------------------------------
# number / select / text / button / vacuum
# ---------------------------------------------------------------------------
class DemoNumber(DemoEntity):
    def __init__(
        self,
        name: str,
        unique_id: str,
        value: float = 50.0,
        minimum: float = 0.0,
        maximum: float = 100.0,
        step: float = 1.0,
        unit: str | None = None,
        area: str | None = None,
    ) -> None:
        super().__init__(name, unique_id, area)
        self._attr_state = value
        self._attr_unit_of_measurement = unit
        self._attr_extra_attributes = {"min": minimum, "max": maximum, "step": step}
        self._min, self._max = minimum, maximum

    async def async_set_value(self, value: float, **kwargs: Any) -> None:
        self._attr_state = max(self._min, min(self._max, float(value)))


class DemoSelect(DemoEntity):
    def __init__(
        self,
        name: str,
        unique_id: str,
        options: list[str],
        current: str | None = None,
        area: str | None = None,
    ) -> None:
        super().__init__(name, unique_id, area)
        self._options = list(options)
        self._attr_state = current or self._options[0]
        self._attr_extra_attributes = {"options": self._options}

    async def async_select_option(self, option: str, **kwargs: Any) -> None:
        if option not in self._options:
            raise ValueError(f"{option!r} is not one of {self._options}")
        self._attr_state = option


class DemoText(DemoEntity):
    def __init__(
        self, name: str, unique_id: str, value: str = "", area: str | None = None
    ) -> None:
        super().__init__(name, unique_id, area)
        self._attr_state = value

    async def async_set_value(self, value: str, **kwargs: Any) -> None:
        self._attr_state = str(value)


class DemoButton(DemoEntity):
    def __init__(self, name: str, unique_id: str, area: str | None = None) -> None:
        super().__init__(name, unique_id, area)
        self._attr_state = "unknown"
        self.presses = 0

    async def async_press(self, **kwargs: Any) -> None:
        self.presses += 1
        self._attr_state = f"pressed_{self.presses}"


class DemoVacuum(DemoEntity):
    def __init__(self, name: str, unique_id: str, area: str | None = None) -> None:
        super().__init__(name, unique_id, area)
        self._attr_state = "docked"
        self._attr_extra_attributes = {"battery_level": 92, "fan_speed": "medium"}

    async def async_start(self, **kwargs: Any) -> None:
        self._attr_state = "cleaning"

    async def async_return_to_base(self, **kwargs: Any) -> None:
        self._attr_state = "returning"

    async def async_stop(self, **kwargs: Any) -> None:
        self._attr_state = "idle"


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
def build_entities(prefix: str = "") -> dict[str, list[DemoEntity]]:
    """The demo house, grouped by domain. Pure — no Jarvis needed."""

    def name(text: str) -> str:
        return f"{prefix}{text}" if prefix else text

    return {
        "light": [
            DemoLight(name("Ceiling Lights"), "ceiling_lights", LIVING_ROOM, STATE_ON, 180),
            DemoLight(name("Bed Light"), "bed_light", BEDROOM, STATE_OFF, 128),
            DemoLight(
                name("Kitchen Lights"), "kitchen_lights", KITCHEN, STATE_ON, 255,
                supports_color=False,
            ),
        ],
        "switch": [
            DemoSwitch(name("Decorative Lights"), "decorative_lights", LIVING_ROOM, STATE_ON),
            DemoSwitch(name("Coffee Machine"), "coffee_machine", KITCHEN, STATE_OFF),
        ],
        "sensor": [
            DemoSensor(
                name("Outside Temperature"), "outside_temperature", 15.6, "°C", "temperature"
            ),
            DemoSensor(name("Outside Humidity"), "outside_humidity", 54, "%", "humidity"),
            DemoSensor(
                name("Power Consumption"), "power_consumption", 412, "W", "power", KITCHEN
            ),
        ],
        "binary_sensor": [
            DemoBinarySensor(name("Basement Motion"), "basement_motion", STATE_OFF, "motion"),
            DemoBinarySensor(name("Front Door"), "front_door_sensor", STATE_OFF, "door"),
        ],
        "climate": [DemoClimate(name("Thermostat"), "thermostat", LIVING_ROOM)],
        "cover": [
            DemoCover(name("Living Room Window"), "living_room_window", LIVING_ROOM, 70),
            DemoCover(name("Garage Door"), "garage_door", None, 0),
        ],
        "lock": [DemoLock(name("Front Door Lock"), "front_door_lock")],
        "fan": [DemoFan(name("Living Room Fan"), "living_room_fan", LIVING_ROOM)],
        "media_player": [
            DemoMediaPlayer(name("Living Room Speaker"), "living_room_speaker", LIVING_ROOM)
        ],
        "number": [
            DemoNumber(name("Target Humidity"), "target_humidity", 45, 20, 80, 1, "%", BEDROOM)
        ],
        "select": [
            DemoSelect(
                name("Light Scene"), "light_scene", ["Bright", "Relax", "Movie", "Off"], "Relax",
                LIVING_ROOM,
            )
        ],
        "text": [DemoText(name("Doorbell Message"), "doorbell_message", "Back in 5", None)],
        "button": [DemoButton(name("Push Button"), "push_button", LIVING_ROOM)],
        "vacuum": [DemoVacuum(name("Robot Vacuum"), "robot_vacuum", LIVING_ROOM)],
    }


async def async_remove_all(jarvis: "Jarvis") -> int:
    """Take the demo house down (M80): every demo entity through the one
    delete path, so the registries, the state and the live objects agree,
    and the dashboards, the exposure list and the model's house all lose
    them at once. Returns how many went."""
    store = jarvis.data.setdefault(DOMAIN, {})
    created: dict[str, DemoEntity] = store.get("entities") or {}
    # Registry entries from an earlier boot too — the platform is "demo".
    ids = set(created)
    for entry in list(jarvis.entities.entities.values()):
        if getattr(entry, "platform", "") == DOMAIN:
            ids.add(entry.entity_id)
    removed = 0
    for entity_id in sorted(ids):
        try:
            result = await jarvis.async_remove_entity(entity_id)
        except Exception:  # noqa: BLE001 - one stubborn entity must not keep the rest
            _LOGGER.exception("Demo: could not remove %s", entity_id)
            continue
        if isinstance(result, dict) and result.get("removed"):
            removed += 1
    created.clear()
    store["platforms"] = {}
    _LOGGER.info("Demo: removed %d entities", removed)
    return removed


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    options = config if isinstance(config, dict) else {}
    # Demo mode is a setting (M80): `demo: enabled: false` — set from Settings
    # › House or by "turn off demo mode" under approval — leaves a real house
    # with no fixture in it. Off at boot also clears what an earlier boot
    # registered, so the Devices screen does not keep a lamp that never was.
    if not bool(options.get("enabled", True)):
        removed = await async_remove_all(jarvis)
        _LOGGER.info("Demo: off (%d stale entities cleared)", removed)
        return True
    create_areas = bool(options.get("create_areas", True))
    prefix = str(options.get("prefix", "") or "")

    store = jarvis.data.setdefault(DOMAIN, {})
    entities_by_domain = build_entities(prefix)
    platforms: dict[str, EntityPlatform] = store.setdefault("platforms", {})
    created: dict[str, DemoEntity] = store.setdefault("entities", {})

    area_ids: dict[str, str] = {}
    if create_areas:
        for area_name in (LIVING_ROOM, KITCHEN, BEDROOM):
            area = await jarvis.areas.create(area_name)
            area_ids[area_name] = area.id

    for domain, entities in entities_by_domain.items():
        platform = platforms.get(domain)
        if platform is None:
            platform = EntityPlatform(jarvis, domain, DOMAIN)
            platforms[domain] = platform
        await platform.async_add_entities(list(entities))

        for entity in entities:
            created[entity.entity_id] = entity
            area_id = area_ids.get(entity.area or "")
            if area_id:
                await jarvis.entities.update(entity.entity_id, area_id=area_id)
                entry = jarvis.entities.get(entity.entity_id)
                if entry is not None and entry.device_id:
                    await jarvis.devices.update(entry.device_id, area_id=area_id)

    _LOGGER.info(
        "Demo: %d entities across %d domains",
        sum(len(items) for items in entities_by_domain.values()),
        len(entities_by_domain),
    )
    return True
