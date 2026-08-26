"""Any sensor (M57): the edges of MQTT discovery, and the model's sensor tools.

`test_mqtt.py` covers the protocol; this file covers what was missing at the
edges — a button press arriving as `event`, the birth the bridges listen for,
an allowlist that keeps the street's radio traffic out, one unit per device
class at ingest, Tasmota's own discovery and Shelly Gen2's status translated,
and the four tools the model reads a reading with. Every message comes from
`tests/fixtures/mqtt_discovery/*.json`, captured from real devices; the
fixture says which. No broker: the same FakeMqttClient as test_mqtt.py.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations.mqtt import translators  # noqa: E402
from jarvis.integrations.mqtt.entity import canonicalise, render_value_template  # noqa: E402
from jarvis.llm.tools import ToolRegistry  # noqa: E402

from test_mqtt import setup_mqtt  # noqa: E402

pytestmark = pytest.mark.asyncio

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mqtt_discovery"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


async def feed(client, topic: str, payload: Any) -> None:
    await client.feed(topic, payload if isinstance(payload, str) else json.dumps(payload))
    await asyncio.sleep(0)


def sensors_of(jarvis) -> dict[str, Any]:
    return {s.entity_id: s for s in jarvis.states.all("sensor")}


def find(jarvis, domain: str, needle: str):
    for state in jarvis.states.all(domain):
        if needle in state.entity_id:
            return state
    raise AssertionError(f"no {domain} entity containing {needle!r}: {jarvis.states.entity_ids(domain)}")


# --- the fixtures are from five sources -------------------------------------


def test_the_fixtures_name_their_source():
    names = sorted(p.stem for p in FIXTURES.glob("*.json"))
    assert len(names) >= 5, names
    sources = {fixture(n)["source"].split(" ")[0].lower() for n in names}
    for expected in ("zigbee2mqtt", "esphome", "rtl_433", "tasmota", "shelly"):
        assert expected in sources, f"no fixture from {expected}: {sources}"


# --- a device bundle, a button press, a phone --------------------------------


async def test_a_zigbee2mqtt_device_bundle_becomes_three_readings(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    f = fixture("zigbee2mqtt_climate")
    await feed(client, f["topic"], f["payload"])
    # The bundle's availability is the bridge's state, published as JSON.
    await feed(client, "zigbee2mqtt/bridge/state", {"state": "online"})
    await feed(client, f["state_topic"], f["state"])
    temperature = find(jarvis, "sensor", "temperature")
    assert temperature.state == "12.5", temperature
    assert temperature.attributes["unit_of_measurement"] == "°C"
    assert temperature.attributes["device_class"] == "temperature"
    assert find(jarvis, "sensor", "humidity").state == "61"
    assert find(jarvis, "sensor", "battery").state == "87"
    await jarvis.async_stop()


async def test_a_button_press_is_an_event_entity_and_a_bus_event(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    presses: list[dict[str, Any]] = []
    jarvis.bus.listen("jarvis_mqtt_event", lambda e: presses.append(dict(e.data)))
    f = fixture("zigbee2mqtt_button_event")
    await feed(client, f["topic"], f["payload"])
    assert jarvis.states.entity_ids("event"), "the event component was dropped"
    await feed(client, f["state_topic"], f["state"])
    await feed(client, f["state_topic"], f["state"])  # the same button, pressed twice
    action = find(jarvis, "event", "action")
    assert action.state == "on"
    assert action.attributes["event_type"] == "on"
    assert "brightness_move_up" in action.attributes["event_types"]
    assert len(presses) == 2, "a second identical press is still a press"
    assert presses[0]["entity_id"] == action.entity_id and presses[0]["event_type"] == "on"
    await jarvis.async_stop()


async def test_a_device_tracker_is_home_or_not(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    await feed(client, "homeassistant/device_tracker/espresense/phone/config", {
        "name": "Phone", "unique_id": "espresense_phone", "state_topic": "espresense/phone/state",
        "payload_home": "home", "payload_not_home": "not_home",
    })
    await feed(client, "espresense/phone/state", "home")
    assert find(jarvis, "device_tracker", "phone").state == "home"
    await feed(client, "espresense/phone/state", "not_home")
    assert find(jarvis, "device_tracker", "phone").state == "not_home"
    await jarvis.async_stop()


# --- the birth ------------------------------------------------------------------


async def test_the_birth_is_said_where_the_bridges_listen(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    births = [m for m in client.published if m.topic == "homeassistant/status"]
    assert births and births[-1].payload == "online", [m.topic for m in client.published]
    await jarvis.async_stop()


async def test_the_birth_can_be_switched_off(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True, "discovery_birth": False})
    assert not [m for m in client.published if m.topic == "homeassistant/status"]
    await jarvis.async_stop()


# --- the allowlist ----------------------------------------------------------------


async def test_the_allowlist_keeps_the_street_out(tmp_path):
    """rtl_433 hears every tyre sensor on the road; only the named station is ours."""
    jarvis, client = await setup_mqtt(
        tmp_path, {"discovery": True, "discovery_allow_ids": ["Bresser-7in1-1234*"]}
    )
    f = fixture("rtl433_bresser")
    await feed(client, f["topic"], f["payload"])
    await feed(client, f["neighbour"]["topic"], f["neighbour"]["payload"])
    ids = jarvis.states.entity_ids("sensor")
    assert any("bresser" in i for i in ids), ids
    assert not any("schrader" in i or "tpms" in i for i in ids), f"the neighbour's tyre is in the house: {ids}"
    await jarvis.async_stop()


async def test_a_denylist_wins_over_everything(tmp_path):
    jarvis, client = await setup_mqtt(
        tmp_path, {"discovery": True, "discovery_deny_ids": ["Schrader-*"]}
    )
    f = fixture("rtl433_bresser")
    await feed(client, f["topic"], f["payload"])
    await feed(client, f["neighbour"]["topic"], f["neighbour"]["payload"])
    ids = jarvis.states.entity_ids("sensor")
    assert any("bresser" in i for i in ids) and not any("schrader" in i for i in ids), ids
    await jarvis.async_stop()


# --- one unit per device class ------------------------------------------------------


def test_canonicalise_converts_known_pairs_and_leaves_the_rest():
    assert canonicalise("68.9", "°F", "temperature") == (20.5, "°C")
    assert canonicalise(4521.7, "Wh", "energy") == (4.52, "kWh")
    assert canonicalise("12.5", "°C", "temperature") == ("12.5", "°C")
    assert canonicalise("open", "°F", "temperature") == ("open", "°F")
    assert canonicalise("3", "furlongs", "distance") == ("3", "furlongs")
    assert canonicalise("3", None, None) == ("3", None)


async def test_an_esphome_fahrenheit_reading_is_celsius_at_ingest(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    f = fixture("esphome_bme280")
    await feed(client, f["topic"], f["payload"])
    await feed(client, f["payload"]["avty_t"], "online")
    await feed(client, f["state_topic"], f["state"])
    study = find(jarvis, "sensor", "study")
    assert study.state == "20.5", study
    assert study.attributes["unit_of_measurement"] == "°C"
    await jarvis.async_stop()


async def test_canonical_units_can_be_switched_off(tmp_path, monkeypatch):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True, "canonical_units": False})
    f = fixture("esphome_bme280")
    await feed(client, f["topic"], f["payload"])
    await feed(client, f["payload"]["avty_t"], "online")
    await feed(client, f["state_topic"], f["state"])
    study = find(jarvis, "sensor", "study")
    assert study.state == "68.9" and study.attributes["unit_of_measurement"] == "°F", study
    await jarvis.async_stop()


# --- the dialects -----------------------------------------------------------------------


def test_tasmota_own_discovery_translates_to_a_switch_and_energy_sensors():
    f = fixture("tasmota_plug")
    relays = translators.tasmota_configs(f["config"]["mac"], f["config"])
    assert [c for c, _, _ in relays] == ["switch"]
    component, discovery_id, config = relays[0]
    assert config["name"] == "Fridge"
    assert config["state_topic"] == "stat/tasmota_AABBCC/POWER"
    assert config["command_topic"] == "cmnd/tasmota_AABBCC/POWER"
    assert config["device"]["manufacturer"] == "Tasmota"
    sensors = translators.tasmota_sensor_configs(f["config"]["mac"], f["config"], f["sensors"])
    by_key = {cfg["unique_id"].rsplit("_", 1)[-1]: cfg for _, _, cfg in sensors}
    assert by_key["power"]["device_class"] == "power" and by_key["power"]["unit_of_measurement"] == "W"
    assert by_key["total"]["device_class"] == "energy" and by_key["total"]["state_class"] == "total_increasing"
    assert by_key["power"]["value_template"] == "{{ value_json.ENERGY.Power }}"
    assert all(cfg["state_topic"] == "tele/tasmota_AABBCC/SENSOR" for cfg in by_key.values())


async def test_a_tasmota_plug_is_a_switch_and_readings_end_to_end(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    f = fixture("tasmota_plug")
    await feed(client, f["config_topic"], f["config"])
    await feed(client, f["sensors_topic"], f["sensors"])
    await feed(client, "tele/tasmota_AABBCC/LWT", "Online")
    await feed(client, f["state_topic"], f["state"])
    await feed(client, f["tele_topic"], f["tele"])
    fridge = find(jarvis, "switch", "fridge")
    assert fridge.state == "on", fridge
    power = find(jarvis, "sensor", "energy_power")
    assert power.state == "91" and power.attributes["unit_of_measurement"] == "W", power
    # A command goes where Tasmota listens.
    await jarvis.data["entity_objects"][fridge.entity_id].async_turn_off()
    assert client.last_publish("cmnd/tasmota_AABBCC/POWER").payload == "OFF"
    await jarvis.async_stop()


async def test_a_shelly_gen2_status_is_a_switch_and_power_sensors_in_kwh(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    f = fixture("shelly_plus1pm")
    # The first status creates the entities; `online` is retained on a real
    # broker and arrives on subscribe, so here it is fed after.
    await feed(client, f["status_topic"], f["status"])
    await feed(client, f["online_topic"], f["online"])
    await feed(client, f["status_topic"], f["status"])
    relay = find(jarvis, "switch", "shellyplus1pm")
    assert relay.state == "on", relay
    power = find(jarvis, "sensor", "_power")
    assert power.state == "12.3" and power.attributes["unit_of_measurement"] == "W"
    energy = find(jarvis, "sensor", "_energy")
    assert energy.state == "4.52" and energy.attributes["unit_of_measurement"] == "kWh", energy
    await jarvis.data["entity_objects"][relay.entity_id].async_turn_off()
    assert client.last_publish(f["command_topic"]).payload == "off"
    await jarvis.async_stop()


async def test_translators_can_be_switched_off(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True, "translators": False})
    f = fixture("shelly_plus1pm")
    await feed(client, f["status_topic"], f["status"])
    assert not jarvis.states.entity_ids("switch")
    await jarvis.async_stop()


# --- a malicious template ------------------------------------------------------------------


MALICIOUS = [
    "{{ ''.__class__.__mro__[1].__subclasses__() }}",
    "{{ value_json.__class__.__init__.__globals__['os'].system('id') }}",
    "{{ self.__init__.__globals__ }}",
    "{% for c in ''.__class__.__base__.__subclasses__() %}{{ c }}{% endfor %}",
]


@pytest.mark.parametrize("template", MALICIOUS)
def test_a_template_that_reaches_for_python_renders_nothing(template):
    """A retained discovery config is untrusted input; the sandbox holds."""
    rendered = render_value_template(template, json.dumps({"temperature": 21}), default=None)
    assert rendered is None or ("class" not in rendered and "os" not in rendered), rendered


async def test_a_discovered_sensor_with_a_malicious_template_never_exposes_python(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    await feed(client, "homeassistant/sensor/evil/config", {
        "name": "Evil", "unique_id": "evil_1", "state_topic": "evil/state",
        "value_template": MALICIOUS[0],
    })
    await feed(client, "evil/state", json.dumps({"x": 1}))
    for state in jarvis.states.all("sensor"):
        assert "<class" not in str(state.state), state
    await jarvis.async_stop()


# --- the model's tools --------------------------------------------------------------------------


async def house_with_readings(tmp_path):
    from jarvis.integrations import sensors as sensors_integration

    jarvis = Jarvis(tmp_path)
    await jarvis.async_setup({"history": {}})
    # The registry the tools land in: set after the core's own setup, which
    # may install its own, and before the integration that registers.
    registry = ToolRegistry(jarvis)
    jarvis.data["llm_tools"] = registry
    assert await sensors_integration.async_setup(jarvis, {}) is True
    rows = [
        ("sensor.garage_temperature", "12.5", {"friendly_name": "Garage temperature", "unit_of_measurement": "°C", "device_class": "temperature", "area": "Garage"}),
        ("sensor.study_temperature", "20.5", {"friendly_name": "Study temperature", "unit_of_measurement": "°C", "device_class": "temperature", "area": "Study"}),
        ("sensor.fridge_power", "91", {"friendly_name": "Fridge power", "unit_of_measurement": "W", "device_class": "power", "area": "Kitchen"}),
        ("sensor.hall_humidity", "61", {"friendly_name": "Hall humidity", "unit_of_measurement": "%", "device_class": "humidity"}),
        ("sensor.dead", "unavailable", {"friendly_name": "Dead", "device_class": "temperature"}),
    ]
    for entity_id, state, attrs in rows:
        jarvis.states.set(entity_id, state, attrs)
    await asyncio.sleep(0)
    return jarvis, registry


async def run(registry, name: str, **args):
    tool = registry.get(name)
    assert tool is not None, f"{name} is not registered"
    return await tool.handler(args, None)


async def test_the_four_tools_are_registered_read_only_and_direct(tmp_path):
    from jarvis.llm.tools import TIER_DIRECT

    jarvis, registry = await house_with_readings(tmp_path)
    for name in ("sensor_readings", "sensor_compare", "sensor_history", "sensor_summary"):
        tool = registry.get(name)
        assert tool is not None, name
        assert tool.tier == TIER_DIRECT, f"{name} should not need approval to read"
    await jarvis.async_stop()


async def test_readings_filter_by_area_class_and_word_and_skip_the_dead(tmp_path):
    jarvis, registry = await house_with_readings(tmp_path)
    everything = await run(registry, "sensor_readings")
    assert everything["count"] == 4, everything
    assert "12.5 °C" in everything["spoken"]
    garage = await run(registry, "sensor_readings", area="garage")
    assert [r["entity_id"] for r in garage["readings"]] == ["sensor.garage_temperature"]
    assert "Garage" in garage["spoken"] and "12.5" in garage["spoken"]
    power = await run(registry, "sensor_readings", device_class="power")
    assert power["count"] == 1 and power["readings"][0]["unit"] == "W"
    fridge = await run(registry, "sensor_readings", query="fridge")
    assert fridge["count"] == 1
    nothing = await run(registry, "sensor_readings", area="attic")
    assert nothing["count"] == 0 and "no readings" in nothing["spoken"]
    await jarvis.async_stop()


async def test_compare_names_the_coldest_and_the_warmest_room(tmp_path):
    jarvis, registry = await house_with_readings(tmp_path)
    result = await run(registry, "sensor_compare", metric="temperature")
    assert result["lowest"]["entity_id"] == "sensor.garage_temperature"
    assert result["highest"]["entity_id"] == "sensor.study_temperature"
    assert "Garage" in result["spoken"] and "Study" in result["spoken"]
    empty = await run(registry, "sensor_compare", metric="illuminance")
    assert empty["rows"] == [] and "no illuminance" in empty["spoken"]
    await jarvis.async_stop()


async def test_summary_says_the_spread_and_the_draw(tmp_path):
    jarvis, registry = await house_with_readings(tmp_path)
    result = await run(registry, "sensor_summary")
    assert result["count"] == 4
    assert result["by_class"] == {"temperature": 2, "power": 1, "humidity": 1}
    assert "12.5" in result["spoken"] and "20.5" in result["spoken"] and "91" in result["spoken"]
    await jarvis.async_stop()


async def test_history_gives_min_max_mean_over_a_window(tmp_path):
    jarvis, registry = await house_with_readings(tmp_path)
    jarvis.states.set("sensor.garage_temperature", "11.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    jarvis.states.set("sensor.garage_temperature", "14.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await asyncio.sleep(0.05)
    result = await run(registry, "sensor_history", entity_id="sensor.garage_temperature", window="1h")
    assert result["window_s"] == 3600
    stats = result["stats"]
    assert stats.get("count", 0) >= 2, result
    assert float(stats["min"]) <= 11.0 and float(stats["max"]) >= 14.0, stats
    assert "min" in result["spoken"] and "°C" in result["spoken"]
    missing = await run(registry, "sensor_history", entity_id="sensor.nothing", window="24h")
    assert "no history" in missing["spoken"]
    bad = await run(registry, "sensor_history")
    assert "error" in bad
    await jarvis.async_stop()


def test_windows_are_read_as_people_write_them():
    from jarvis.integrations.sensors import _window_seconds

    assert _window_seconds("24h") == 86400
    assert _window_seconds("7d") == 7 * 86400
    assert _window_seconds("30m") == 1800
    assert _window_seconds("90") == 90
    assert _window_seconds(None) == 86400
    assert _window_seconds("a fortnight") == 86400


async def test_the_sensor_tools_are_read_only_for_the_taint_rule(tmp_path):
    """A turn that has read a hostile page may still read a thermometer."""
    jarvis, registry = await house_with_readings(tmp_path)
    for name in ("sensor_readings", "sensor_compare", "sensor_history", "sensor_summary"):
        assert registry.is_read_only(registry.get(name)) is True, name
    await jarvis.async_stop()
