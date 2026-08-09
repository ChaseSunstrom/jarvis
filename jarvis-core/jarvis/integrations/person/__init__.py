"""Person — who is home, aggregated from their device trackers.

    person:
      - name: Chris
        device_trackers:
          - device_tracker.chris_phone
          - device_tracker.chris_watch
      - name: Sam
        id: sam
        device_trackers: [device_tracker.sam_phone]

Each entry becomes a ``person.<name>`` entity whose state follows its
trackers: ``home`` if any tracker is home, otherwise the most recently
updated known tracker state (``not_home`` or a named place).

Also registers ``device_tracker.see`` so a phone, router or presence
script can report a location without needing its own integration::

    service: device_tracker.see
    data:
      dev_id: chris_phone
      gps: [40.71, -74.01]
      gps_accuracy: 12
      battery: 84

With no ``location_name``, GPS coordinates are compared against the home
coordinates from the ``jarvis:`` block (``radius``, default 100 m) to
decide ``home`` vs ``not_home``.
"""

from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING, Any

from ...const import (
    EVENT_STATE_CHANGED,
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from ...entity import Entity, EntityPlatform
from ...services import ServiceCall
from ...state import slugify

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "person"
DEVICE_TRACKER_DOMAIN = "device_tracker"
SERVICE_SEE = "see"

DEFAULT_HOME_RADIUS = 100.0  # metres
EARTH_RADIUS = 6_371_000.0

UNSET_STATES = frozenset({STATE_UNKNOWN, STATE_UNAVAILABLE, "", "none", "None"})

ATTR_SOURCE = "source"
ATTR_DEVICE_TRACKERS = "device_trackers"
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_GPS_ACCURACY = "gps_accuracy"
ATTR_BATTERY = "battery_level"


def distance_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in metres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS * math.asin(min(1.0, math.sqrt(a)))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _normalise_config(config: Any) -> list[dict[str, Any]]:
    """Accept a list of dicts, or a mapping of name → config."""
    if not config:
        return []
    if isinstance(config, dict):
        entries = []
        for key, value in config.items():
            value = dict(value or {})
            value.setdefault("name", key)
            entries.append(value)
        return entries
    if isinstance(config, list):
        return [dict(item) for item in config if isinstance(item, dict)]
    return []


class PersonEntity(Entity):
    """A person, whose state is the consensus of their trackers."""

    def __init__(
        self,
        name: str,
        device_trackers: list[str],
        person_id: str | None = None,
        user_id: str | None = None,
        picture: str | None = None,
    ) -> None:
        self._attr_name = name
        self.person_id = person_id or slugify(name)
        self._attr_unique_id = f"person_{self.person_id}"
        self._attr_icon = "mdi:account"
        self._attr_should_poll = False
        self._attr_state = STATE_UNKNOWN
        self.device_trackers = [t.lower() for t in device_trackers]
        self.user_id = user_id
        self.picture = picture
        self._source: str | None = None
        self._unsub: Any = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "id": self.person_id,
            ATTR_DEVICE_TRACKERS: list(self.device_trackers),
            ATTR_SOURCE: self._source,
            "user_id": self.user_id,
            "entity_picture": self.picture,
        }
        if self._source and self.jarvis is not None:
            tracker = self.jarvis.states.get(self._source)
            if tracker is not None:
                for key in (
                    ATTR_LATITUDE,
                    ATTR_LONGITUDE,
                    ATTR_GPS_ACCURACY,
                    ATTR_BATTERY,
                ):
                    if key in tracker.attributes:
                        attrs[key] = tracker.attributes[key]
        return {k: v for k, v in attrs.items() if v is not None}

    # --- lifecycle ----------------------------------------------------
    async def async_added_to_jarvis(self) -> None:
        self._unsub = self.jarvis.bus.listen(EVENT_STATE_CHANGED, self._handle_change)
        self.update_from_trackers()

    async def async_will_remove(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def _handle_change(self, event: Any) -> None:
        entity_id = event.data.get("entity_id")
        if entity_id and entity_id.lower() in self.device_trackers:
            self.update_from_trackers()

    # --- state resolution ---------------------------------------------
    def update_from_trackers(self) -> None:
        """Recompute from the current tracker states and publish."""
        best_state = STATE_UNKNOWN
        best_source: str | None = None
        best_time = -1.0
        home_time = -1.0

        for tracker_id in self.device_trackers:
            state = self.jarvis.states.get(tracker_id)
            if state is None or state.state in UNSET_STATES:
                continue
            updated = float(state.last_updated)
            if state.state == STATE_HOME:
                # Any tracker at home wins; newest home wins among those.
                if updated > home_time:
                    home_time = updated
                    best_state = STATE_HOME
                    best_source = tracker_id
                continue
            if home_time < 0 and updated > best_time:
                best_time = updated
                best_state = state.state
                best_source = tracker_id

        self._source = best_source
        self._attr_state = best_state
        self.async_write_state()


def _home_coordinates(jarvis: "Jarvis") -> tuple[float | None, float | None, float]:
    core = (jarvis.config or {}).get("jarvis") or {}
    latitude = core.get("latitude")
    longitude = core.get("longitude")
    radius = float(core.get("radius", DEFAULT_HOME_RADIUS) or DEFAULT_HOME_RADIUS)
    if latitude is None or longitude is None:
        return None, None, radius
    try:
        return float(latitude), float(longitude), radius
    except (TypeError, ValueError):
        return None, None, radius


def _resolve_location(
    jarvis: "Jarvis",
    entity_id: str,
    location_name: str | None,
    gps: Any,
) -> tuple[str, float | None, float | None]:
    """Work out (state, latitude, longitude) for a `see` call."""
    latitude = longitude = None
    if gps is not None:
        try:
            latitude, longitude = float(gps[0]), float(gps[1])
        except (TypeError, ValueError, IndexError):
            _LOGGER.warning("device_tracker.see: bad gps payload %r", gps)
            latitude = longitude = None

    if location_name:
        return str(location_name), latitude, longitude

    if latitude is not None and longitude is not None:
        home_lat, home_lon, radius = _home_coordinates(jarvis)
        if home_lat is not None and home_lon is not None:
            distance = distance_metres(latitude, longitude, home_lat, home_lon)
            state = STATE_HOME if distance <= radius else STATE_NOT_HOME
            return state, latitude, longitude
        return STATE_NOT_HOME, latitude, longitude

    existing = jarvis.states.get(entity_id)
    if existing is not None and existing.state not in UNSET_STATES:
        return existing.state, latitude, longitude
    return STATE_HOME, latitude, longitude


async def async_setup(jarvis: "Jarvis", config: Any) -> bool:
    entries = _normalise_config(config)

    # --- device_tracker.see -------------------------------------------
    async def handle_see(call: ServiceCall) -> dict[str, Any]:
        dev_id = call.get("dev_id") or call.get("mac") or call.get("host_name")
        entity_id = call.get("entity_id")
        if not entity_id:
            if not dev_id:
                return {"seen": False, "error": "dev_id or entity_id is required"}
            entity_id = f"{DEVICE_TRACKER_DOMAIN}.{slugify(str(dev_id))}"
        entity_id = str(entity_id).lower()

        state, latitude, longitude = _resolve_location(
            jarvis, entity_id, call.get("location_name"), call.get("gps")
        )

        existing = jarvis.states.get(entity_id)
        attributes: dict[str, Any] = dict(existing.attributes) if existing else {}
        attributes.setdefault(
            "friendly_name",
            str(call.get("host_name") or dev_id or entity_id.split(".", 1)[1])
            .replace("_", " ")
            .title(),
        )
        attributes["source_type"] = call.get(
            "source_type", "gps" if latitude is not None else "router"
        )
        if latitude is not None:
            attributes[ATTR_LATITUDE] = latitude
            attributes[ATTR_LONGITUDE] = longitude
        if call.get("gps_accuracy") is not None:
            attributes[ATTR_GPS_ACCURACY] = call.get("gps_accuracy")
        if call.get("battery") is not None:
            attributes[ATTR_BATTERY] = call.get("battery")
        if call.get("host_name"):
            attributes["host_name"] = call.get("host_name")
        extra = call.get("attributes")
        if isinstance(extra, dict):
            attributes.update(extra)
        attributes["last_seen"] = time.time()

        jarvis.states.set(entity_id, state, attributes, context=call.context)
        return {"seen": True, "entity_id": entity_id, "state": state}

    jarvis.services.register(
        DEVICE_TRACKER_DOMAIN,
        SERVICE_SEE,
        handle_see,
        description="Report a device's location (creates the tracker if needed).",
        fields={
            "dev_id": {"description": "Device slug, e.g. chris_phone."},
            "entity_id": {"description": "Full tracker entity id (instead of dev_id)."},
            "location_name": {"description": "home, not_home or a place name."},
            "gps": {"description": "[latitude, longitude]", "example": [40.71, -74.01]},
            "gps_accuracy": {"description": "Accuracy in metres."},
            "battery": {"description": "Battery percentage."},
            "host_name": {"description": "Friendly device name."},
            "source_type": {"description": "gps, router, bluetooth …"},
        },
        supports_response=True,
    )

    # --- person entities ----------------------------------------------
    people: list[PersonEntity] = []
    for entry in entries:
        name = entry.get("name")
        if not name:
            _LOGGER.warning("person: entry without a name ignored: %r", entry)
            continue
        trackers = _as_list(
            entry.get("device_trackers")
            or entry.get("device_tracker")
            or entry.get("trackers")
        )
        people.append(
            PersonEntity(
                name=str(name),
                device_trackers=trackers,
                person_id=entry.get("id"),
                user_id=entry.get("user_id"),
                picture=entry.get("picture"),
            )
        )

    platform = EntityPlatform(jarvis, DOMAIN, DOMAIN)
    jarvis.data[f"{DOMAIN}_platform"] = platform
    jarvis.data[DOMAIN] = {p.person_id: p for p in people}
    if people:
        await platform.async_add_entities(people)
    return True
