"""Two dialects turned into discovery configs: Tasmota's own, and Shelly Gen2.

Home Assistant's discovery protocol covers Zigbee2MQTT, Z-Wave JS UI, ESPHome,
rtl_433 and Theengs. Two common devices do not speak it:

* **Tasmota** removed HA-format discovery in 2023 and publishes its own on
  ``tasmota/discovery/<mac>/config`` (the device: name, topic, relays,
  friendly names) and ``tasmota/discovery/<mac>/sensors`` (a ``sn`` map of
  what its sensors look like). Its state lives on ``stat/<topic>/POWER<n>`` and
  ``tele/<topic>/SENSOR``; commands go to ``cmnd/<topic>/POWER<n>``.
* **Shelly Gen2** (Plus/Pro) publishes ``<id>/status/switch:<n>`` JSON
  (``output``, ``apower``, ``voltage``, ``current``, ``temperature``) and takes
  ``on``/``off``/``toggle`` on ``<id>/command/switch:<n>``. No discovery at all.

Each translator is a pure function from a message to the ``(component,
discovery_id, config)`` triples the discovery layer already understands, so
the entities they make are ordinary discovered entities — with the
allowlist, the units and the device grouping everything else gets. Nothing
here subscribes; ``__init__`` wires the topics.
"""
from __future__ import annotations

import json
import re
from typing import Any

#: Tasmota sensor keys → (device_class, unit, state_class). The keys are the
#: ones Tasmota's own docs use; anything not here still becomes a plain sensor.
_TASMOTA_UNITS: dict[str, tuple[str | None, str | None, str | None]] = {
    "Temperature": ("temperature", "°C", "measurement"),
    "Humidity": ("humidity", "%", "measurement"),
    "Pressure": ("pressure", "hPa", "measurement"),
    "Illuminance": ("illuminance", "lx", "measurement"),
    "Power": ("power", "W", "measurement"),
    "ApparentPower": ("apparent_power", "VA", "measurement"),
    "ReactivePower": ("reactive_power", "var", "measurement"),
    "Voltage": ("voltage", "V", "measurement"),
    "Current": ("current", "A", "measurement"),
    "Factor": ("power_factor", None, "measurement"),
    "Total": ("energy", "kWh", "total_increasing"),
    "Today": ("energy", "kWh", "total_increasing"),
    "Yesterday": ("energy", "kWh", "total"),
    "CO2": ("carbon_dioxide", "ppm", "measurement"),
    "Battery": ("battery", "%", "measurement"),
    "Distance": ("distance", "cm", "measurement"),
}

_SHELLY_SENSORS: dict[str, tuple[str, str, str]] = {
    # key in the status payload → (device_class, unit, name suffix)
    "apower": ("power", "W", "Power"),
    "voltage": ("voltage", "V", "Voltage"),
    "current": ("current", "A", "Current"),
    "aenergy.total": ("energy", "Wh", "Energy"),
    "temperature.tC": ("temperature", "°C", "Temperature"),
}

Triple = tuple[str, str, dict[str, Any]]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")


def _device(identifier: str, name: str, manufacturer: str, model: str = "", sw: str = "") -> dict[str, Any]:
    device: dict[str, Any] = {"identifiers": [identifier], "name": name, "manufacturer": manufacturer}
    if model:
        device["model"] = model
    if sw:
        device["sw_version"] = sw
    return device


# --- Tasmota ------------------------------------------------------------------


def tasmota_configs(mac: str, payload: Any) -> list[Triple]:
    """Relays from a ``tasmota/discovery/<mac>/config`` message.

    The fields are Tasmota's: ``dn`` device name, ``fn`` friendly names per
    relay (``null`` for an unused slot), ``t`` the device topic, ``rl`` relay
    kinds (1 = a switch, 2 = a light, 0 = none), ``sw`` firmware, ``md`` module.
    A single-relay device reports on ``stat/<t>/POWER``; more than one on
    ``POWER1``, ``POWER2``, … — which Tasmota calls the ``so`` "SetOption26"
    quirk and this reproduces.
    """
    data = payload if isinstance(payload, dict) else _json(payload)
    if not isinstance(data, dict):
        return []
    mac = _slug(mac or data.get("mac") or "")
    topic = str(data.get("t") or "").strip()
    if not mac or not topic:
        return []
    name = str(data.get("dn") or topic)
    relays = [int(r) for r in (data.get("rl") or []) if str(r).isdigit()]
    friendly = list(data.get("fn") or [])
    device = _device(f"tasmota_{mac}", name, "Tasmota", str(data.get("md") or ""), str(data.get("sw") or ""))
    used = [i for i, kind in enumerate(relays) if kind]
    out: list[Triple] = []
    for index in used:
        suffix = "" if len(used) == 1 else str(index + 1)
        relay_name = friendly[index] if index < len(friendly) and friendly[index] else f"{name} {index + 1}"
        component = "light" if relays[index] == 2 else "switch"
        config = {
            "name": str(relay_name),
            "unique_id": f"tasmota_{mac}_{component}_{index + 1}",
            "state_topic": f"stat/{topic}/POWER{suffix}",
            "command_topic": f"cmnd/{topic}/POWER{suffix}",
            "payload_on": "ON",
            "payload_off": "OFF",
            "availability_topic": f"tele/{topic}/LWT",
            "payload_available": "Online",
            "payload_not_available": "Offline",
            "device": device,
        }
        out.append((component, f"tasmota_{mac}_{component}_{index + 1}", config))
    return out


def tasmota_sensor_configs(mac: str, config_payload: Any, sensors_payload: Any) -> list[Triple]:
    """Sensors from ``tasmota/discovery/<mac>/sensors`` — the ``sn`` map.

    ``sn`` is the device's last ``tele/<t>/SENSOR`` message: ``{"Time": …,
    "ENERGY": {"Power": 12.3, "Total": 4.5}, "BME280": {"Temperature": 21.4}}``.
    Every numeric leaf under a named block becomes a sensor reading that
    block's key off ``tele/<t>/SENSOR`` with a ``value_json`` template.
    """
    data = config_payload if isinstance(config_payload, dict) else _json(config_payload)
    sensors = sensors_payload if isinstance(sensors_payload, dict) else _json(sensors_payload)
    if not isinstance(data, dict) or not isinstance(sensors, dict):
        return []
    mac = _slug(mac or data.get("mac") or "")
    topic = str(data.get("t") or "").strip()
    if not mac or not topic:
        return []
    name = str(data.get("dn") or topic)
    device = _device(f"tasmota_{mac}", name, "Tasmota", str(data.get("md") or ""), str(data.get("sw") or ""))
    out: list[Triple] = []
    for block, readings in (sensors.get("sn") or {}).items():
        if block == "Time" or not isinstance(readings, dict):
            continue
        for key, value in readings.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            device_class, unit, state_class = _TASMOTA_UNITS.get(key, (None, None, None))
            config: dict[str, Any] = {
                "name": f"{name} {block} {key}",
                "unique_id": f"tasmota_{mac}_{_slug(block)}_{_slug(key)}",
                "state_topic": f"tele/{topic}/SENSOR",
                "value_template": f"{{{{ value_json.{block}.{key} }}}}",
                "availability_topic": f"tele/{topic}/LWT",
                "payload_available": "Online",
                "payload_not_available": "Offline",
                "device": device,
            }
            if device_class:
                config["device_class"] = device_class
            if unit:
                config["unit_of_measurement"] = unit
            if state_class:
                config["state_class"] = state_class
            out.append(("sensor", config["unique_id"], config))
    return out


# --- Shelly Gen2 --------------------------------------------------------------


def shelly_configs(device_id: str, component: str, payload: Any) -> list[Triple]:
    """A Shelly Gen2 ``<id>/status/switch:<n>`` message as entities.

    ``device_id`` is the MQTT prefix (``shellyplus1pm-441793d69718``);
    ``component`` is ``switch:0`` etc. The switch itself plus a sensor for each
    power reading the payload carries; each keeps reading the same status
    topic, so a later message updates them all.
    """
    data = payload if isinstance(payload, dict) else _json(payload)
    if not isinstance(data, dict):
        return []
    device_id = str(device_id or "").strip().strip("/")
    match = re.fullmatch(r"(switch|light|cover):(\d+)", str(component or ""))
    if not device_id or not match:
        return []
    kind, index = match.group(1), int(match.group(2))
    slug = _slug(device_id)
    model = device_id.split("-", 1)[0]
    device = _device(f"shelly_{slug}", device_id, "Shelly", model)
    state_topic = f"{device_id}/status/{kind}:{index}"
    out: list[Triple] = []
    if kind in ("switch", "light"):
        out.append((
            kind,
            f"shelly_{slug}_{kind}_{index}",
            {
                "name": f"{device_id} {kind} {index}",
                "unique_id": f"shelly_{slug}_{kind}_{index}",
                "state_topic": state_topic,
                "value_template": "{{ value_json.output }}",
                # The status says True/False; the command wants on/off — which
                # is exactly what HA's state_on/state_off exist to separate.
                "state_on": "True",
                "state_off": "False",
                "command_topic": f"{device_id}/command/{kind}:{index}",
                "payload_on": "on",
                "payload_off": "off",
                "availability_topic": f"{device_id}/online",
                "payload_available": "true",
                "payload_not_available": "false",
                "device": device,
            },
        ))
    for key, (device_class, unit, suffix) in _SHELLY_SENSORS.items():
        head, _, tail = key.partition(".")
        present = head in data and (not tail or isinstance(data.get(head), dict) and tail in data[head])
        if not present:
            continue
        template = f"{{{{ value_json.{head}.{tail} }}}}" if tail else f"{{{{ value_json.{head} }}}}"
        uid = f"shelly_{slug}_{kind}_{index}_{_slug(key)}"
        out.append((
            "sensor",
            uid,
            {
                "name": f"{device_id} {suffix}",
                "unique_id": uid,
                "state_topic": state_topic,
                "value_template": template,
                "device_class": device_class,
                "unit_of_measurement": unit,
                "state_class": "total_increasing" if device_class == "energy" else "measurement",
                "availability_topic": f"{device_id}/online",
                "payload_available": "true",
                "payload_not_available": "false",
                "device": device,
            },
        ))
    return out


def _json(payload: Any) -> Any:
    try:
        return json.loads(payload if isinstance(payload, str) else bytes(payload).decode("utf-8", "replace"))
    except (TypeError, ValueError):
        return None
