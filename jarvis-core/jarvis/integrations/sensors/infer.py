"""What *is* this thing? — pure inference from an id and a payload.

Nothing in here touches Jarvis, the network, the clock or a file. Give it a
sensor id, whatever the device posted, and (optionally) the areas that exist,
and it answers the five questions the rest of the system needs:

    domain  device_class  unit  friendly name  area

That is the whole trick behind "stick an ESP32 on the front door and Jarvis
just knows". ``front_door_motion`` posting ``{"state": true}`` is a
``binary_sensor`` with device class ``motion``, called "Front Door Motion",
in the "Front Door" area if you have one. ``garage_temp`` posting ``21.5``
is a ``sensor``, ``temperature``, ``°C``, "Garage Temperature", area "Garage".

Two rules keep the guessing honest:

* **The payload picks the domain, the name picks the class.** A boolean is a
  ``binary_sensor`` whatever it is called; a number is a ``sensor``. Only
  ``0``/``1`` is ambiguous, and there the name breaks the tie.
* **The domain is never anything else.** A payload may *hint* a domain, but
  only ``sensor`` and ``binary_sensor`` are honoured. A device that posts
  ``{"domain": "lock"}`` gets a ``sensor``, not an actuator — inference must
  never be a way to mint something the assistant can then go and *operate*.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ...const import STATE_OFF, STATE_ON, STATE_UNKNOWN

SENSOR = "sensor"
BINARY_SENSOR = "binary_sensor"

#: The only two domains inference may ever produce. See the module docstring.
ALLOWED_DOMAINS = frozenset({SENSOR, BINARY_SENSOR})

#: Keys that describe the sensor rather than being part of its reading.
HINT_KEYS = frozenset(
    {
        "area",
        "area_id",
        "device_class",
        "domain",
        "friendly_name",
        "icon",
        "name",
        "unit",
        "unit_of_measurement",
    }
)

#: Keys carrying the reading itself.
VALUE_KEYS = ("state", "value", "val")

MAX_NAME_CHARS = 64


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------
def slug(text: Any) -> str:
    """``"Front Door!"`` -> ``front_door`` (local copy; keeps this module pure)."""
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower()).strip("_")


#: Spellings that are worth normalising before anything else looks at the id.
_ID_REWRITES = (
    ("pm_2_5", "pm25"),
    ("pm2_5", "pm25"),
    ("pm_25", "pm25"),
    ("pm_10", "pm10"),
    ("pm_1", "pm1"),
    ("co_2", "co2"),
)


def normalize_id(raw: Any) -> tuple[str | None, str]:
    """``(domain hint, sensor id)``.

    Accepts a bare id (``front_door_motion``) or a full entity id
    (``binary_sensor.front_door_motion``); the latter's domain is only used as
    a hint, and only if it is one we allow.
    """
    text = str(raw or "").strip().lower()
    domain_hint: str | None = None
    if "." in text:
        head, _, tail = text.partition(".")
        if slug(head) in ALLOWED_DOMAINS:
            domain_hint = slug(head)
            text = tail
        else:
            text = text.replace(".", "_")
    text = slug(text)
    for old, new in _ID_REWRITES:
        text = text.replace(old, new)
    return domain_hint, text


def tokens_of(sensor_id: str) -> list[str]:
    return [t for t in normalize_id(sensor_id)[1].split("_") if t]


#: Words that read badly when merely title-cased.
WORD_FORMS: dict[str, str] = {
    "ac": "AC",
    "aqi": "AQI",
    "batt": "Battery",
    "co": "CO",
    "co2": "CO2",
    "cpu": "CPU",
    "db": "dB",
    "gpu": "GPU",
    "hum": "Humidity",
    "humid": "Humidity",
    "hvac": "HVAC",
    "id": "ID",
    "illum": "Illuminance",
    "ip": "IP",
    "led": "LED",
    "lux": "Lux",
    "no2": "NO2",
    "ph": "pH",
    "pir": "Motion",
    "pm1": "PM1",
    "pm10": "PM10",
    "pm25": "PM2.5",
    "press": "Pressure",
    "psi": "PSI",
    "ram": "RAM",
    "rh": "Humidity",
    "rssi": "RSSI",
    "temp": "Temperature",
    "tv": "TV",
    "tvoc": "TVOC",
    "usb": "USB",
    "uv": "UV",
    "voc": "VOC",
    "wifi": "WiFi",
}


def humanize(sensor_id: Any) -> str:
    """``front_door_motion`` -> ``Front Door Motion``."""
    words = [WORD_FORMS.get(token, token.title()) for token in tokens_of(sensor_id)]
    name = " ".join(words).strip()
    return (name or "Sensor")[:MAX_NAME_CHARS]


# ---------------------------------------------------------------------------
# device classes
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ClassEntry:
    """What a name fragment means, on each side of the binary/numeric split."""

    binary: str | None = None
    numeric: str | None = None
    unit: str | None = None

    @property
    def binary_preferred(self) -> bool:
        return self.binary is not None


def _b(device_class: str) -> ClassEntry:
    return ClassEntry(binary=device_class)


def _n(device_class: str | None, unit: str | None = None) -> ClassEntry:
    return ClassEntry(numeric=device_class, unit=unit)


PERCENT = "%"
CELSIUS = "°C"
MICROGRAMS = "µg/m³"

#: Name fragment (one or more ``_``-joined tokens) -> what it means.
#: Looked up longest-suffix-first, so ``garage_door`` beats ``door``.
CLASS_TABLE: dict[str, ClassEntry] = {
    # --- binary only ---
    "motion": _b("motion"),
    "pir": _b("motion"),
    "movement": _b("motion"),
    "occupancy": _b("occupancy"),
    "occupied": _b("occupancy"),
    "presence": _b("presence"),
    "door": _b("door"),
    "garage_door": _b("garage_door"),
    "window": _b("window"),
    "opening": _b("opening"),
    "contact": _b("opening"),
    "smoke": _b("smoke"),
    "co": _b("carbon_monoxide"),
    "carbon_monoxide": _b("carbon_monoxide"),
    "leak": _b("moisture"),
    "water_leak": _b("moisture"),
    "flood": _b("moisture"),
    "damp": _b("moisture"),
    "vibration": _b("vibration"),
    "tamper": _b("tamper"),
    "lock": _b("lock"),
    "plug": _b("plug"),
    "running": _b("running"),
    "safety": _b("safety"),
    "cold": _b("cold"),
    "heat": _b("heat"),
    "connectivity": _b("connectivity"),
    "online": _b("connectivity"),
    "link": _b("connectivity"),
    "problem": _b("problem"),
    "fault": _b("problem"),
    "error": _b("problem"),
    "charging": _b("battery_charging"),
    "battery_charging": _b("battery_charging"),
    # --- both, decided by the payload ---
    "battery": ClassEntry(binary="battery", numeric="battery", unit=PERCENT),
    "battery_level": ClassEntry(binary="battery", numeric="battery", unit=PERCENT),
    "gas": ClassEntry(binary="gas", numeric="gas", unit="m³"),
    "moisture": ClassEntry(binary="moisture", numeric="moisture", unit=PERCENT),
    "water": ClassEntry(binary="moisture", numeric="water", unit="L"),
    "wet": ClassEntry(binary="moisture", numeric="moisture", unit=PERCENT),
    "rain": ClassEntry(binary="moisture", numeric="precipitation", unit="mm"),
    "sound": ClassEntry(binary="sound", numeric="sound_pressure", unit="dB"),
    "noise": ClassEntry(binary="sound", numeric="sound_pressure", unit="dB"),
    "power": ClassEntry(binary="power", numeric="power", unit="W"),
    "light": ClassEntry(binary="light", numeric="illuminance", unit="lx"),
    # --- numeric only ---
    "temp": _n("temperature", CELSIUS),
    "temperature": _n("temperature", CELSIUS),
    "dew_point": _n("temperature", CELSIUS),
    "humidity": _n("humidity", PERCENT),
    "humid": _n("humidity", PERCENT),
    "hum": _n("humidity", PERCENT),
    "rh": _n("humidity", PERCENT),
    "soil": _n("moisture", PERCENT),
    "soil_moisture": _n("moisture", PERCENT),
    "illuminance": _n("illuminance", "lx"),
    "lux": _n("illuminance", "lx"),
    "light_level": _n("illuminance", "lx"),
    "brightness": _n("illuminance", "lx"),
    "pressure": _n("pressure", "hPa"),
    "baro": _n("pressure", "hPa"),
    "barometer": _n("pressure", "hPa"),
    "co2": _n("carbon_dioxide", "ppm"),
    "carbon_dioxide": _n("carbon_dioxide", "ppm"),
    "pm1": _n("pm1", MICROGRAMS),
    "pm10": _n("pm10", MICROGRAMS),
    "pm25": _n("pm25", MICROGRAMS),
    "voc": _n("volatile_organic_compounds", "ppb"),
    "tvoc": _n("volatile_organic_compounds", "ppb"),
    "ozone": _n("ozone", MICROGRAMS),
    "no2": _n("nitrogen_dioxide", MICROGRAMS),
    "aqi": _n("aqi", None),
    "air_quality": _n("aqi", None),
    "energy": _n("energy", "kWh"),
    "kwh": _n("energy", "kWh"),
    "consumption": _n("energy", "kWh"),
    "current": _n("current", "A"),
    "voltage": _n("voltage", "V"),
    "volt": _n("voltage", "V"),
    "frequency": _n("frequency", "Hz"),
    "signal": _n("signal_strength", "dBm"),
    "rssi": _n("signal_strength", "dBm"),
    "signal_strength": _n("signal_strength", "dBm"),
    "distance": _n("distance", "cm"),
    "speed": _n("speed", "km/h"),
    "wind_speed": _n("wind_speed", "km/h"),
    "weight": _n("weight", "kg"),
    "mass": _n("weight", "kg"),
    "uptime": _n("duration", "s"),
    "duration": _n("duration", "s"),
    "ph": _n("ph", None),
    "cpu": _n(None, PERCENT),
    "ram": _n(None, PERCENT),
    "memory": _n(None, PERCENT),
    "disk": _n(None, PERCENT),
    "level": _n(None, PERCENT),
    "percent": _n(None, PERCENT),
    "percentage": _n(None, PERCENT),
}

#: Longest fragment in the table, so the suffix walk knows where to start.
_MAX_FRAGMENT = max(len(key.split("_")) for key in CLASS_TABLE)


def lookup_class(sensor_id: Any) -> tuple[str | None, ClassEntry | None]:
    """Longest matching trailing fragment of the id, and what it means."""
    tokens = tokens_of(sensor_id)
    if not tokens:
        return None, None
    for size in range(min(_MAX_FRAGMENT, len(tokens)), 0, -1):
        fragment = "_".join(tokens[-size:])
        entry = CLASS_TABLE.get(fragment)
        if entry is not None:
            return fragment, entry
    return None, None


# ---------------------------------------------------------------------------
# values
# ---------------------------------------------------------------------------
TRUE_WORDS = frozenset(
    {
        "1", "on", "true", "yes", "open", "opened", "detected", "detect",
        "active", "present", "occupied", "wet", "motion", "high", "unlocked",
        "online", "up", "alarm", "triggered",
    }
)
FALSE_WORDS = frozenset(
    {
        "0", "off", "false", "no", "closed", "close", "clear", "none",
        "inactive", "absent", "unoccupied", "dry", "low", "locked",
        "offline", "down", "idle", "normal",
    }
)
UNKNOWN_WORDS = frozenset({"", "unknown", "unavailable", "null", "nan", "n/a"})

KIND_NONE = "none"
KIND_BOOL = "bool"
KIND_ZERO_ONE = "zero_one"
KIND_NUMBER = "number"
KIND_BINARY_TEXT = "binary_text"
KIND_TEXT = "text"


def as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def value_kind(value: Any) -> str:
    """How to read the thing that was posted."""
    if value is None:
        return KIND_NONE
    if isinstance(value, bool):
        return KIND_BOOL
    number = as_number(value)
    if number is not None:
        return KIND_ZERO_ONE if number in (0.0, 1.0) else KIND_NUMBER
    text = str(value).strip().lower()
    if text in UNKNOWN_WORDS:
        return KIND_NONE
    if text in TRUE_WORDS or text in FALSE_WORDS:
        return KIND_BINARY_TEXT
    return KIND_TEXT


def format_number(number: float) -> str:
    """``24.0`` -> ``"24"``, ``21.5`` -> ``"21.5"``."""
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return f"{number:g}"


def normalize_state(value: Any, domain: str) -> str:
    """The string that goes on the state machine."""
    if value is None:
        return STATE_UNKNOWN
    if domain == BINARY_SENSOR:
        if isinstance(value, bool):
            return STATE_ON if value else STATE_OFF
        number = as_number(value)
        if number is not None:
            return STATE_OFF if number == 0 else STATE_ON
        text = str(value).strip().lower()
        if text in TRUE_WORDS:
            return STATE_ON
        if text in FALSE_WORDS:
            return STATE_OFF
        return STATE_UNKNOWN
    if isinstance(value, bool):
        return STATE_ON if value else STATE_OFF
    number = as_number(value)
    if number is not None and not isinstance(value, str):
        return format_number(number)
    text = str(value).strip()
    if text.lower() in UNKNOWN_WORDS:
        return STATE_UNKNOWN
    return text


# ---------------------------------------------------------------------------
# payloads
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Payload:
    """A posted body, split into the reading, its attributes and its hints."""

    value: Any = None
    attributes: dict[str, Any] = field(default_factory=dict)
    hints: dict[str, Any] = field(default_factory=dict)


def _clean_hints(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: source[key]
        for key in HINT_KEYS
        if key in source and source[key] not in (None, "")
    }


def parse_payload(payload: Any, sensor_id: Any = "") -> Payload:
    """Understand ``{"state": ...}``, ``{"value": ...}`` or a bare value.

    A body with neither — ``{"temperature": 21.5}``, which is what a lot of
    homemade firmware posts — is read by matching a key against the sensor's
    own name, then by falling back to its single scalar key.
    """
    if payload is None:
        return Payload()
    if not isinstance(payload, Mapping):
        return Payload(value=payload)

    body = dict(payload)
    attributes = body.get("attributes")
    attributes = dict(attributes) if isinstance(attributes, Mapping) else {}

    hints = _clean_hints(attributes)
    hints.update(_clean_hints(body))

    for key in VALUE_KEYS:
        if key in body:
            return Payload(value=body[key], attributes=attributes, hints=hints)

    # No explicit state: look for a key the sensor's own name mentions.
    named = set(tokens_of(sensor_id))
    leftovers = {
        key: value
        for key, value in body.items()
        if key not in HINT_KEYS and key != "attributes" and not isinstance(value, (dict, list))
    }
    for key, value in leftovers.items():
        if slug(key) in named:
            hints.setdefault("device_class_from_key", key)
            return Payload(value=value, attributes=attributes, hints=hints)
    if len(leftovers) == 1:
        key, value = next(iter(leftovers.items()))
        hints.setdefault("device_class_from_key", key)
        return Payload(value=value, attributes=attributes, hints=hints)

    # Nothing readable: keep the body as attributes so it is not simply lost.
    merged = {**{k: v for k, v in body.items() if k not in HINT_KEYS}, **attributes}
    merged.pop("attributes", None)
    return Payload(value=None, attributes=merged, hints=hints)


# ---------------------------------------------------------------------------
# areas
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Area:
    id: str
    name: str
    aliases: tuple[str, ...] = ()


class AreaIndex:
    """Areas that exist, indexed for longest-prefix matching against an id."""

    def __init__(self, areas: Iterable[Area] | Mapping[str, str] | None = None) -> None:
        self._areas: dict[str, Area] = {}
        self._by_slug: dict[str, str] = {}
        if isinstance(areas, Mapping):
            areas = [Area(str(k), str(v)) for k, v in areas.items()]
        for area in areas or ():
            self.add(area)

    @classmethod
    def from_registry(cls, registry: Any) -> "AreaIndex":
        """Build from a live :class:`jarvis.registry.AreaRegistry`."""
        entries = getattr(registry, "areas", None) or {}
        return cls(
            Area(str(entry.id), str(entry.name), tuple(str(a) for a in (entry.aliases or [])))
            for entry in entries.values()
        )

    def add(self, area: Area) -> None:
        self._areas[area.id] = area
        for candidate in (area.id, area.name, *area.aliases):
            key = slug(candidate)
            if key:
                self._by_slug.setdefault(key, area.id)

    def __len__(self) -> int:
        return len(self._areas)

    def name_of(self, area_id: str | None) -> str | None:
        area = self._areas.get(area_id or "")
        return area.name if area else None

    def resolve(self, text: Any) -> str | None:
        """Area id for an id, a name or an alias. No fuzzy matching."""
        if text in (None, ""):
            return None
        raw = str(text).strip()
        if raw in self._areas:
            return raw
        return self._by_slug.get(slug(raw))

    def match(self, sensor_id: Any) -> str | None:
        """Longest leading run of the id that names an area; then trailing.

        ``front_door_motion`` finds "Front Door" if it exists, and finds
        nothing at all if only "Front Porch" does — a wrong room is worse
        than no room.
        """
        tokens = tokens_of(sensor_id)
        for size in range(len(tokens), 0, -1):
            found = self._by_slug.get("_".join(tokens[:size]))
            if found:
                return found
        for size in range(len(tokens) - 1, 0, -1):
            found = self._by_slug.get("_".join(tokens[-size:]))
            if found:
                return found
        return None


# ---------------------------------------------------------------------------
# the inference itself
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SensorSpec:
    """Everything needed to create the entity, and how the answer was reached."""

    sensor_id: str
    domain: str
    device_class: str | None = None
    unit: str | None = None
    name: str = ""
    area_id: str | None = None
    state: str = STATE_UNKNOWN
    attributes: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    @property
    def entity_id_hint(self) -> str:
        return f"{self.domain}.{slug(self.name) or self.sensor_id}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "sensor_id": self.sensor_id,
            "domain": self.domain,
            "device_class": self.device_class,
            "unit_of_measurement": self.unit,
            "name": self.name,
            "area_id": self.area_id,
            "state": self.state,
            "reason": self.reason,
        }


def _clean_name(raw: Any, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(raw or "").replace("\n", " ")).strip()
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:MAX_NAME_CHARS] if text else fallback


def infer(
    sensor_id: Any,
    payload: Any = None,
    hints: Mapping[str, Any] | None = None,
    areas: AreaIndex | Mapping[str, str] | None = None,
) -> SensorSpec:
    """Work out what a posted sensor is. Pure; safe to call on junk."""
    domain_from_id, clean_id = normalize_id(sensor_id)
    if not clean_id:
        clean_id = "sensor"

    parsed = payload if isinstance(payload, Payload) else parse_payload(payload, clean_id)
    merged: dict[str, Any] = {**parsed.hints, **_clean_hints(hints or {})}

    index = areas if isinstance(areas, AreaIndex) else AreaIndex(areas)

    # --- domain -----------------------------------------------------------
    kind = value_kind(parsed.value)
    fragment, entry = lookup_class(clean_id)
    key_fragment, key_entry = (None, None)
    if merged.get("device_class_from_key"):
        key_fragment, key_entry = lookup_class(str(merged["device_class_from_key"]))
    if entry is None and key_entry is not None:
        fragment, entry = key_fragment, key_entry

    hinted_domain = slug(merged.get("domain"))
    if hinted_domain in ALLOWED_DOMAINS:
        domain, reason = hinted_domain, "domain given by the payload"
    elif domain_from_id in ALLOWED_DOMAINS:
        domain, reason = str(domain_from_id), "domain given in the sensor id"
    elif kind == KIND_BOOL:
        domain, reason = BINARY_SENSOR, "payload is a boolean"
    elif kind == KIND_BINARY_TEXT:
        domain, reason = BINARY_SENSOR, "payload reads as on/off"
    elif kind == KIND_NUMBER:
        domain, reason = SENSOR, "payload is a number"
    elif kind == KIND_ZERO_ONE:
        if entry is not None and entry.binary_preferred:
            domain, reason = BINARY_SENSOR, f"0/1 and {fragment!r} is a binary sensor"
        else:
            domain, reason = SENSOR, "payload is 0 or 1 with no binary name"
    elif kind == KIND_TEXT:
        domain, reason = SENSOR, "payload is free text"
    else:  # nothing posted at all
        if entry is not None and entry.numeric is None and entry.binary_preferred:
            domain, reason = BINARY_SENSOR, f"{fragment!r} is only ever a binary sensor"
        else:
            domain, reason = SENSOR, "nothing posted yet"

    # --- device class + unit ---------------------------------------------
    device_class: str | None = None
    unit: str | None = None
    if entry is not None:
        if domain == BINARY_SENSOR:
            device_class = entry.binary
        else:
            device_class = entry.numeric
            unit = entry.unit
        if device_class:
            reason = f"{reason}; {fragment!r} -> {device_class}"

    hinted_class = merged.get("device_class")
    if hinted_class:
        device_class = slug(hinted_class) or None
        reason = f"{reason}; device_class given by the payload"
    hinted_unit = merged.get("unit") or merged.get("unit_of_measurement")
    if hinted_unit:
        unit = str(hinted_unit)[:16]
    if domain == BINARY_SENSOR:
        unit = None  # an on/off value has no unit, whatever was posted

    # --- name + area ------------------------------------------------------
    name = _clean_name(merged.get("name") or merged.get("friendly_name"), humanize(clean_id))
    area_id = index.resolve(merged.get("area_id") or merged.get("area"))
    if area_id is None:
        area_id = index.match(clean_id)

    attributes = {k: v for k, v in parsed.attributes.items() if k not in HINT_KEYS}
    return SensorSpec(
        sensor_id=clean_id,
        domain=domain,
        device_class=device_class,
        unit=unit,
        name=name,
        area_id=area_id,
        state=normalize_state(parsed.value, domain),
        attributes=attributes,
        reason=reason,
    )


__all__ = [
    "ALLOWED_DOMAINS",
    "Area",
    "AreaIndex",
    "BINARY_SENSOR",
    "CLASS_TABLE",
    "ClassEntry",
    "Payload",
    "SENSOR",
    "SensorSpec",
    "format_number",
    "humanize",
    "infer",
    "lookup_class",
    "normalize_id",
    "normalize_state",
    "parse_payload",
    "slug",
    "tokens_of",
    "value_kind",
]
