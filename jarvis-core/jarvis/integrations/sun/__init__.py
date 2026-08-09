"""Sun — where the sun is, and when it next moves.

Creates a single ``sun.sun`` entity (``above_horizon`` / ``below_horizon``)
carrying the attributes automations and dashboards expect::

    next_rising, next_setting, next_dawn, next_dusk, next_noon,
    next_midnight, elevation, azimuth, rising

Location comes from the top-level ``jarvis:`` block, and can be overridden
per-integration::

    jarvis:
      latitude: 40.71
      longitude: -74.01
      elevation: 10
      time_zone: America/New_York

    sun:
      update_interval: 60     # seconds between recomputes

All the maths lives in :mod:`.solar` (pure functions, no dependencies), and
this module re-exports the pieces automations need:
:func:`is_up`, :func:`next_event_at`, :func:`solar_position_now`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from ...entity import Entity, EntityPlatform
from .solar import (  # noqa: F401  (re-exported for automations/tests)
    SOLAR_EVENTS,
    ZENITH_ASTRONOMICAL,
    ZENITH_CIVIL,
    ZENITH_NAUTICAL,
    ZENITH_OFFICIAL,
    as_utc,
    next_event,
    solar_noon,
    solar_position,
    sun_times,
)
from .solar import is_up as solar_is_up

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "sun"
ENTITY_ID = "sun.sun"

STATE_ABOVE_HORIZON = "above_horizon"
STATE_BELOW_HORIZON = "below_horizon"

DEFAULT_LATITUDE = 0.0
DEFAULT_LONGITUDE = 0.0
DEFAULT_UPDATE_INTERVAL = 60.0

# Event names accepted by sun triggers / helpers. Bound to the solar module
# so the advertised list and the list actually enforced cannot drift apart.
EVENTS = SOLAR_EVENTS


class SunData:
    """Location-bound wrapper around the solar functions.

    Stored at ``jarvis.data["sun"]`` so automations can ask questions like
    ``sun.is_up()`` or ``sun.next("sunset", offset=timedelta(minutes=-30))``
    without knowing the configured coordinates.
    """

    def __init__(self, latitude: float, longitude: float, elevation: float = 0.0) -> None:
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.elevation = float(elevation)

    def position(self, when: datetime | None = None) -> tuple[float, float]:
        """(elevation, azimuth) in degrees."""
        return solar_position(self.latitude, self.longitude, when)

    def solar_elevation(self, when: datetime | None = None) -> float:
        return self.position(when)[0]

    def solar_azimuth(self, when: datetime | None = None) -> float:
        return self.position(when)[1]

    def sunrise(self, day: Any = None) -> datetime | None:
        day = day or as_utc().date()
        return sun_times(self.latitude, self.longitude, day)[0]

    def sunset(self, day: Any = None) -> datetime | None:
        day = day or as_utc().date()
        return sun_times(self.latitude, self.longitude, day)[1]

    def next(
        self,
        event: str,
        after: datetime | None = None,
        offset: timedelta | None = None,
    ) -> datetime | None:
        """Next ``event`` (optionally shifted by ``offset``) after ``after``.

        The offset is applied to the astronomical instant, then the search
        continues if that lands in the past — so "30 minutes before sunset"
        always returns a future moment.

        Raises :class:`ValueError` for a name outside :data:`EVENTS`.
        """
        after = as_utc(after)
        offset = offset or timedelta()
        search_from = after
        for _ in range(4):
            instant = next_event(self.latitude, self.longitude, event, search_from)
            if instant is None:
                return None
            shifted = instant + offset
            if shifted > after:
                return shifted
            search_from = instant
        return None

    def is_up(self, when: datetime | None = None) -> bool:
        return solar_is_up(self.latitude, self.longitude, when)

    def as_dict(self, when: datetime | None = None) -> dict[str, Any]:
        when = as_utc(when)
        elevation, azimuth = self.position(when)
        rising = self.next("sunrise", when)
        setting = self.next("sunset", when)
        return {
            "state": STATE_ABOVE_HORIZON if self.is_up(when) else STATE_BELOW_HORIZON,
            "next_rising": _iso(rising),
            "next_setting": _iso(setting),
            "next_dawn": _iso(self.next("dawn", when)),
            "next_dusk": _iso(self.next("dusk", when)),
            "next_noon": _iso(self.next("noon", when)),
            "next_midnight": _iso(self.next("midnight", when)),
            "elevation": elevation,
            "azimuth": azimuth,
            # Climbing or falling: cheapest reliable test is "where will it
            # be shortly?", which stays correct at every latitude.
            "rising": self.solar_elevation(when + timedelta(minutes=10)) > elevation,
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class SunEntity(Entity):
    """The ``sun.sun`` entity. Polled by its platform, never by hardware."""

    def __init__(self, data: SunData) -> None:
        self._data = data
        self._attr_name = "Sun"
        self._attr_unique_id = "sun_sun"
        self._attr_icon = "mdi:white-balance-sunny"
        self._attr_should_poll = True
        self._attr_state = STATE_BELOW_HORIZON
        self._attr_extra_attributes = {}
        self.recompute()

    def recompute(self, when: datetime | None = None) -> dict[str, Any]:
        snapshot = self._data.as_dict(when)
        self._attr_state = snapshot.pop("state")
        self._attr_extra_attributes = snapshot
        return snapshot

    async def async_update(self) -> None:
        self.recompute()


def get_sun(jarvis: "Jarvis") -> SunData | None:
    """The configured SunData, or None when `sun:` is not set up."""
    return jarvis.data.get(DOMAIN)


def is_up(jarvis: "Jarvis", when: datetime | None = None) -> bool:
    """Automation helper: is the sun above the horizon?"""
    data = get_sun(jarvis)
    if data is None:
        state = jarvis.states.get(ENTITY_ID)
        return state is not None and state.state == STATE_ABOVE_HORIZON
    return data.is_up(when)


def next_event_at(
    jarvis: "Jarvis",
    event: str,
    after: datetime | None = None,
    offset: timedelta | None = None,
) -> datetime | None:
    """Automation helper: when does `event` next happen (with offset)?"""
    data = get_sun(jarvis)
    if data is None:
        return None
    return data.next(event, after, offset)


def solar_position_now(jarvis: "Jarvis") -> tuple[float, float]:
    data = get_sun(jarvis)
    if data is None:
        return (0.0, 0.0)
    return data.position()


def _coordinates(jarvis: "Jarvis", config: dict[str, Any]) -> tuple[float, float, float]:
    core = (jarvis.config or {}).get("jarvis") or {}
    latitude = config.get("latitude", core.get("latitude", DEFAULT_LATITUDE))
    longitude = config.get("longitude", core.get("longitude", DEFAULT_LONGITUDE))
    elevation = config.get("elevation", core.get("elevation", 0))
    try:
        return float(latitude), float(longitude), float(elevation)
    except (TypeError, ValueError):
        _LOGGER.warning(
            "sun: invalid latitude/longitude (%r, %r); falling back to 0,0",
            latitude, longitude,
        )
        return DEFAULT_LATITUDE, DEFAULT_LONGITUDE, 0.0


async def async_setup(jarvis: "Jarvis", config: Any) -> bool:
    if not isinstance(config, dict):
        config = {}

    latitude, longitude, elevation = _coordinates(jarvis, config)
    if latitude == 0.0 and longitude == 0.0:
        _LOGGER.warning(
            "sun: no latitude/longitude configured under `jarvis:` — "
            "sunrise/sunset will be computed for 0°N 0°E"
        )

    data = SunData(latitude, longitude, elevation)
    jarvis.data[DOMAIN] = data

    interval = float(config.get("update_interval", DEFAULT_UPDATE_INTERVAL))
    platform = EntityPlatform(jarvis, DOMAIN, DOMAIN, scan_interval=max(interval, 5.0))
    jarvis.data[f"{DOMAIN}_platform"] = platform
    await platform.async_add_entities([SunEntity(data)])
    return True
