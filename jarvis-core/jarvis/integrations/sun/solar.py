"""Solar position maths — NOAA algorithm, stdlib only.

Everything here is a pure function of (latitude, longitude, instant), so it
is trivially testable and has no dependency on the rest of Jarvis. Accuracy
is roughly ±1 minute for sunrise/sunset at temperate latitudes, which is
far better than any automation needs.

Reference: NOAA Solar Calculator (Astronomical Algorithms, Jean Meeus).
"""

from __future__ import annotations

import math
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone

# Zenith angles for the events we care about (degrees from vertical).
ZENITH_OFFICIAL = 90.833  # sun's upper limb at the horizon + refraction
ZENITH_CIVIL = 96.0
ZENITH_NAUTICAL = 102.0
ZENITH_ASTRONOMICAL = 108.0

_UNIX_EPOCH_JD = 2440587.5

#: The only event names :func:`next_event` understands.
SOLAR_EVENTS = ("sunrise", "sunset", "dawn", "dusk", "noon", "midnight")


def as_utc(when: datetime | None = None) -> datetime:
    """Normalise to an aware UTC datetime (naive input is treated as UTC)."""
    if when is None:
        return datetime.now(timezone.utc)
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def julian_day(when: datetime) -> float:
    return as_utc(when).timestamp() / 86400.0 + _UNIX_EPOCH_JD


def julian_century(jd: float) -> float:
    return (jd - 2451545.0) / 36525.0


def _solar_params(t: float) -> tuple[float, float]:
    """(declination in degrees, equation of time in minutes) at century ``t``."""
    mean_long = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    mean_anom = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    eccentricity = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)

    m_rad = math.radians(mean_anom)
    center = (
        math.sin(m_rad) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * m_rad) * (0.019993 - 0.000101 * t)
        + math.sin(3 * m_rad) * 0.000289
    )
    true_long = mean_long + center
    omega = 125.04 - 1934.136 * t
    apparent_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))

    mean_obliq = 23.0 + (
        26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0
    ) / 60.0
    obliq_corr = mean_obliq + 0.00256 * math.cos(math.radians(omega))

    declination = math.degrees(
        math.asin(
            math.sin(math.radians(obliq_corr)) * math.sin(math.radians(apparent_long))
        )
    )

    var_y = math.tan(math.radians(obliq_corr / 2.0)) ** 2
    l_rad = math.radians(mean_long)
    eq_time = 4.0 * math.degrees(
        var_y * math.sin(2 * l_rad)
        - 2 * eccentricity * math.sin(m_rad)
        + 4 * eccentricity * var_y * math.sin(m_rad) * math.cos(2 * l_rad)
        - 0.5 * var_y * var_y * math.sin(4 * l_rad)
        - 1.25 * eccentricity * eccentricity * math.sin(2 * m_rad)
    )
    return declination, eq_time


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _refraction_degrees(elevation: float) -> float:
    """Atmospheric refraction correction (NOAA), in degrees."""
    if elevation > 85.0:
        return 0.0
    tan_e = math.tan(math.radians(elevation))
    if elevation > 5.0:
        arcsec = 58.1 / tan_e - 0.07 / tan_e**3 + 0.000086 / tan_e**5
    elif elevation > -0.575:
        arcsec = 1735.0 + elevation * (
            -518.2 + elevation * (103.4 + elevation * (-12.79 + elevation * 0.711))
        )
    else:
        arcsec = -20.772 / tan_e
    return arcsec / 3600.0


def solar_position(
    latitude: float, longitude: float, when: datetime | None = None
) -> tuple[float, float]:
    """Apparent (refraction-corrected) elevation and azimuth, in degrees.

    Azimuth is measured clockwise from true north.
    """
    when = as_utc(when)
    t = julian_century(julian_day(when))
    declination, eq_time = _solar_params(t)

    minutes = (
        when.hour * 60.0
        + when.minute
        + when.second / 60.0
        + when.microsecond / 60_000_000.0
    )
    true_solar_time = (minutes + eq_time + 4.0 * longitude) % 1440.0
    hour_angle = true_solar_time / 4.0 - 180.0

    lat_rad = math.radians(latitude)
    decl_rad = math.radians(declination)
    ha_rad = math.radians(hour_angle)

    cos_zenith = _clamp(
        math.sin(lat_rad) * math.sin(decl_rad)
        + math.cos(lat_rad) * math.cos(decl_rad) * math.cos(ha_rad)
    )
    zenith = math.degrees(math.acos(cos_zenith))
    raw_elevation = 90.0 - zenith
    elevation = raw_elevation + _refraction_degrees(raw_elevation)

    sin_zenith = math.sin(math.radians(zenith))
    if abs(sin_zenith) < 1e-9 or abs(math.cos(lat_rad)) < 1e-9:
        azimuth = 180.0 if latitude >= 0 else 0.0
    else:
        cos_az = _clamp(
            (math.sin(lat_rad) * cos_zenith - math.sin(decl_rad))
            / (math.cos(lat_rad) * sin_zenith)
        )
        angle = math.degrees(math.acos(cos_az))
        azimuth = (angle + 180.0) % 360.0 if hour_angle > 0 else (540.0 - angle) % 360.0

    return round(elevation, 4), round(azimuth, 4)


def _hour_angle_for_zenith(
    latitude: float, declination: float, zenith: float
) -> float | None:
    """Half-day length in degrees, or None when the event never happens."""
    lat_rad = math.radians(latitude)
    decl_rad = math.radians(declination)
    denominator = math.cos(lat_rad) * math.cos(decl_rad)
    if abs(denominator) < 1e-12:
        return None
    value = math.cos(math.radians(zenith)) / denominator - math.tan(lat_rad) * math.tan(
        decl_rad
    )
    if value < -1.0 or value > 1.0:
        return None  # polar day or polar night
    return math.degrees(math.acos(value))


def solar_noon(longitude: float, day: date_cls) -> datetime:
    """UTC instant of solar noon for ``day`` at ``longitude``."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    minutes = 720.0 - 4.0 * longitude
    for _ in range(2):
        t = julian_century(julian_day(midnight + timedelta(minutes=minutes)))
        _, eq_time = _solar_params(t)
        minutes = 720.0 - 4.0 * longitude - eq_time
    return midnight + timedelta(minutes=minutes)


def sun_times(
    latitude: float,
    longitude: float,
    day: date_cls,
    zenith: float = ZENITH_OFFICIAL,
) -> tuple[datetime | None, datetime | None]:
    """(sunrise, sunset) in UTC for the given UTC calendar day.

    Returns ``(None, None)`` during polar day/night, when the sun never
    crosses the requested zenith.
    """
    midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    noon = solar_noon(longitude, day)

    results: list[datetime | None] = []
    # +1 lands before solar noon (sunrise), -1 after it (sunset).
    for sign in (1.0, -1.0):
        minutes = (noon - midnight).total_seconds() / 60.0
        event: datetime | None = None
        for _ in range(3):
            t = julian_century(julian_day(midnight + timedelta(minutes=minutes)))
            declination, eq_time = _solar_params(t)
            hour_angle = _hour_angle_for_zenith(latitude, declination, zenith)
            if hour_angle is None:
                event = None
                break
            minutes = 720.0 - 4.0 * (longitude + sign * hour_angle) - eq_time
            event = midnight + timedelta(minutes=minutes)
        results.append(event)
    return results[0], results[1]


def next_event(
    latitude: float,
    longitude: float,
    event: str,
    after: datetime | None = None,
    max_days: int = 367,
) -> datetime | None:
    """Next occurrence of an event strictly after ``after`` (UTC).

    ``event`` is one of: sunrise, sunset, dawn, dusk, noon, midnight.
    Returns None inside a polar day/night that lasts longer than
    ``max_days``. An unrecognised name raises :class:`ValueError` — it used
    to fall through to sunset, so a typo in an automation trigger fired
    silently at the wrong time of day.
    """
    after = as_utc(after)
    event = str(event).strip().lower()
    if event not in SOLAR_EVENTS:
        raise ValueError(
            f"unknown solar event {event!r}; expected one of {', '.join(SOLAR_EVENTS)}"
        )
    zenith = {
        "sunrise": ZENITH_OFFICIAL,
        "sunset": ZENITH_OFFICIAL,
        "dawn": ZENITH_CIVIL,
        "dusk": ZENITH_CIVIL,
    }.get(event, ZENITH_OFFICIAL)

    day = (after - timedelta(days=1)).date()
    for offset in range(max_days + 1):
        current = day + timedelta(days=offset)
        if event == "noon":
            candidate: datetime | None = solar_noon(longitude, current)
        elif event == "midnight":
            candidate = solar_noon(longitude, current) - timedelta(hours=12)
        else:
            rise, set_ = sun_times(latitude, longitude, current, zenith)
            candidate = rise if event in ("sunrise", "dawn") else set_
        if candidate is not None and candidate > after:
            return candidate
    return None


def is_up(
    latitude: float, longitude: float, when: datetime | None = None
) -> bool:
    """True when the sun is above the horizon at ``when``.

    Normally decided by comparing the next rising and setting, which stays
    correct right at the horizon where a raw elevation comparison gets
    fuzzy. Inside a polar day/night the sun does not cross the horizon at
    all that day, so elevation is the only meaningful answer.
    """
    when = as_utc(when)
    today_rise, today_set = sun_times(latitude, longitude, when.date())
    if today_rise is None or today_set is None:
        return solar_position(latitude, longitude, when)[0] > 0.0

    rising = next_event(latitude, longitude, "sunrise", when)
    setting = next_event(latitude, longitude, "sunset", when)
    if rising is None or setting is None:
        return solar_position(latitude, longitude, when)[0] > 0.0
    return setting < rising
