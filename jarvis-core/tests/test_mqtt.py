"""MQTT integration + discovery tests. No broker: a FakeMqttClient is injected."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import mqtt as mqtt_integration  # noqa: E402
from jarvis.integrations.mqtt.client import (  # noqa: E402
    DEFAULT_READY_TIMEOUT,
    FakeMqttClient,
    MqttClientBase,
    NullClient,
    create_client,
    normalize_payload,
    topic_matches,
)
from jarvis.integrations.mqtt.discovery import (  # noqa: E402
    normalize_discovery_payload,
    parse_discovery_topic,
)
from jarvis.integrations.mqtt.entity import render_value_template  # noqa: E402

DISCOVERY_PREFIX = "homeassistant"


async def setup_mqtt(tmp_path, config):
    """Set up the integration against an injected fake client."""
    jarvis = Jarvis(tmp_path)
    client = FakeMqttClient()
    jarvis.data["mqtt"] = client  # injection point
    assert await mqtt_integration.async_setup(jarvis, config) is True
    assert jarvis.data["mqtt"] is client
    return jarvis, client


def state_of(jarvis, entity_id):
    state = jarvis.states.get(entity_id)
    assert state is not None, f"{entity_id} has no state ({jarvis.states.entity_ids()})"
    return state


# --- topic matching / payload plumbing -------------------------------------
def test_topic_matches_wildcards():
    assert topic_matches("a/b/c", "a/b/c")
    assert topic_matches("a/+/c", "a/b/c")
    assert not topic_matches("a/+/c", "a/b/d")
    assert not topic_matches("a/+/c", "a/b/c/d")
    assert topic_matches("a/#", "a/b/c/d")
    assert topic_matches("a/#", "a")
    assert topic_matches("#", "anything/at/all")
    assert not topic_matches("#", "$SYS/broker/uptime")
    assert topic_matches("homeassistant/+/+/config", "homeassistant/switch/x/config")
    assert not topic_matches("homeassistant/+/+/config", "homeassistant/switch/n/x/config")
    assert topic_matches("homeassistant/+/+/+/config", "homeassistant/switch/n/x/config")


def test_normalize_payload():
    assert normalize_payload(b"ON") == "ON"
    assert normalize_payload(None) == ""
    assert normalize_payload(12) == "12"
    assert json.loads(normalize_payload({"a": 1})) == {"a": 1}


async def test_fake_client_dispatch_and_unsubscribe():
    client = FakeMqttClient()
    await client.async_connect()
    seen = []
    unsub = await client.async_subscribe("sensors/+/temp", lambda m: seen.append(m.payload))
    await client.feed("sensors/kitchen/temp", "21")
    await client.feed("sensors/kitchen/humidity", "40")
    assert seen == ["21"]
    unsub()
    await client.feed("sensors/kitchen/temp", "22")
    assert seen == ["21"]
    assert client.subscriptions == []


def test_create_client_falls_back_without_a_backend():
    client = create_client({"broker": "10.0.0.1", "port": 8883})
    assert isinstance(client, MqttClientBase)
    assert client.broker == "10.0.0.1" and client.port == 8883
    assert isinstance(create_client({"backend": "null"}), NullClient)


def test_the_wait_for_a_first_connection_is_short_and_configurable():
    """Setup must not sit on a broker that is not up yet.

    This was ten hardcoded seconds, added to every start where the broker lags
    behind — a compose stack still bringing mosquitto up, a Pi whose broker unit
    orders after this one, and every test that boots the shipped
    configuration.yaml, which is where it cost jarvis-core's suite 150 seconds.

    Nothing depends on the wait: publishes before the link is up are dropped
    with a log line, and the runner reconnects with backoff regardless. It is
    only there so a broker that *is* up — milliseconds, on loopback — is
    connected before setup returns.
    """
    assert create_client({}).ready_timeout == DEFAULT_READY_TIMEOUT
    assert DEFAULT_READY_TIMEOUT <= 3.0, "back to blocking startup on an absent broker"
    assert create_client({"ready_timeout": 0.25}).ready_timeout == 0.25
    # 0 means "do not wait at all", and must not become "wait forever".
    assert create_client({"ready_timeout": 0}).ready_timeout == 0
    # A negative value would make wait_for raise instantly, i.e. not wait, but
    # only by accident. Clamped, so it means what it looks like it means.
    assert create_client({"ready_timeout": -5}).ready_timeout == 0


# --- value templates --------------------------------------------------------
def test_render_value_template():
    payload = json.dumps({"temperature": 21.5, "nested": {"x": 7}})
    assert render_value_template("{{ value_json.temperature }}", payload) == "21.5"
    assert render_value_template("{{ value_json.nested.x }}", payload) == "7"
    assert render_value_template("{{ value }}", "raw") == "raw"
    assert render_value_template(None, "raw") == "raw"
    # missing keys and broken templates fall back to the default, not a crash
    assert render_value_template("{{ value_json.missing }}", payload) is None
    assert render_value_template("{{ value_json.missing }}", payload, default="x") == "x"
    assert render_value_template("{{ value_json.temperature }}", "not json") is None


# --- YAML entities ----------------------------------------------------------
async def test_yaml_switch_publishes_and_tracks_state(tmp_path):
    jarvis, client = await setup_mqtt(
        tmp_path,
        {
            "broker": "127.0.0.1",
            "discovery": False,
            "switch": [
                {
                    "name": "Desk lamp",
                    "state_topic": "stat/desk/POWER",
                    "command_topic": "cmnd/desk/POWER",
                }
            ],
        },
    )
    entity_id = "switch.desk_lamp"
    assert state_of(jarvis, entity_id).state == "unknown"
    entity = jarvis.entity_object(entity_id)

    await entity.async_turn_on()
    published = client.last_publish()
    assert published.topic == "cmnd/desk/POWER"
    assert published.payload == "ON"
    # not optimistic: the device still owns the state
    assert state_of(jarvis, entity_id).state == "unknown"

    await client.feed("stat/desk/POWER", "ON")
    assert state_of(jarvis, entity_id).state == "on"

    await entity.async_turn_off()
    assert client.last_publish().payload == "OFF"
    await client.feed("stat/desk/POWER", "OFF")
    assert state_of(jarvis, entity_id).state == "off"

    await entity.async_toggle()
    assert client.last_publish() == client.last_publish("cmnd/desk/POWER")
    assert client.last_publish().payload == "ON"


async def test_yaml_switch_custom_payloads_and_optimistic(tmp_path):
    jarvis, client = await setup_mqtt(
        tmp_path,
        {
            "discovery": False,
            "switch": [
                {
                    "name": "Pump",
                    "command_topic": "pump/set",
                    "payload_on": "1",
                    "payload_off": "0",
                    "retain": True,
                }
            ],
        },
    )
    entity = jarvis.entity_object("switch.pump")
    await entity.async_turn_on()
    message = client.last_publish()
    assert (message.topic, message.payload, message.retain) == ("pump/set", "1", True)
    # no state_topic -> optimistic
    assert state_of(jarvis, "switch.pump").state == "on"
    await entity.async_toggle()
    assert client.last_publish().payload == "0"
    assert state_of(jarvis, "switch.pump").state == "off"


async def test_sensor_value_template_and_attributes(tmp_path):
    jarvis, client = await setup_mqtt(
        tmp_path,
        {
            "discovery": False,
            "sensor": [
                {
                    "name": "Garden temperature",
                    "state_topic": "sensors/garden",
                    "value_template": "{{ value_json.temperature }}",
                    "unit_of_measurement": "C",
                    "device_class": "temperature",
                    "state_class": "measurement",
                    "json_attributes_topic": "sensors/garden",
                }
            ],
        },
    )
    entity_id = "sensor.garden_temperature"
    await client.feed(
        "sensors/garden", json.dumps({"temperature": 21.5, "humidity": 61, "battery": 88})
    )
    state = state_of(jarvis, entity_id)
    assert state.state == "21.5"
    assert state.attributes["unit_of_measurement"] == "C"
    assert state.attributes["device_class"] == "temperature"
    assert state.attributes["state_class"] == "measurement"
    # json_attributes_topic merged the whole payload in
    assert state.attributes["humidity"] == 61
    assert state.attributes["battery"] == 88

    # a payload the template cannot read leaves the last good value alone
    await client.feed("sensors/garden", "not json")
    assert state_of(jarvis, entity_id).state == "21.5"


async def test_availability_marks_entity_unavailable(tmp_path):
    jarvis, client = await setup_mqtt(
        tmp_path,
        {
            "discovery": False,
            "binary_sensor": [
                {
                    "name": "Front door",
                    "state_topic": "z/door/state",
                    "availability_topic": "z/door/status",
                    "payload_on": "OPEN",
                    "payload_off": "CLOSED",
                    "device_class": "door",
                }
            ],
        },
    )
    entity_id = "binary_sensor.front_door"
    # availability configured but nothing received yet
    assert state_of(jarvis, entity_id).state == "unavailable"

    await client.feed("z/door/status", "online")
    await client.feed("z/door/state", "OPEN")
    assert state_of(jarvis, entity_id).state == "on"

    await client.feed("z/door/status", "offline")
    assert state_of(jarvis, entity_id).state == "unavailable"

    # still tracking underneath: coming back online restores the real state
    await client.feed("z/door/status", "online")
    assert state_of(jarvis, entity_id).state == "on"
    await client.feed("z/door/state", "CLOSED")
    assert state_of(jarvis, entity_id).state == "off"


async def test_light_brightness_and_color_commands(tmp_path):
    jarvis, client = await setup_mqtt(
        tmp_path,
        {
            "discovery": False,
            "light": [
                {
                    "name": "Hall light",
                    "state_topic": "hall/state",
                    "command_topic": "hall/set",
                    "brightness_state_topic": "hall/brightness",
                    "brightness_command_topic": "hall/brightness/set",
                    "rgb_command_topic": "hall/rgb/set",
                    "color_temp_command_topic": "hall/ct/set",
                }
            ],
        },
    )
    entity_id = "light.hall_light"
    entity = jarvis.entity_object(entity_id)

    await entity.async_turn_on(brightness=128)
    assert client.payloads_for("hall/brightness/set") == ["128"]
    assert client.payloads_for("hall/set") == ["ON"]
    assert state_of(jarvis, entity_id).attributes["brightness"] == 128

    client.clear()
    await entity.async_turn_on(rgb_color=(255, 128, 0), color_temp_kelvin=4000)
    assert client.payloads_for("hall/rgb/set") == ["255,128,0"]
    # kelvin is converted to the mireds MQTT lights expect
    assert client.payloads_for("hall/ct/set") == ["250"]

    # brightness reported back by the device
    await client.feed("hall/brightness", "64")
    assert state_of(jarvis, entity_id).attributes["brightness"] == 64
    await client.feed("hall/state", "ON")
    assert state_of(jarvis, entity_id).state == "on"

    client.clear()
    await entity.async_turn_off()
    assert client.payloads_for("hall/set") == ["OFF"]


async def test_light_brightness_scale(tmp_path):
    jarvis, client = await setup_mqtt(
        tmp_path,
        {
            "discovery": False,
            "light": [
                {
                    "name": "Scaled",
                    "command_topic": "s/set",
                    "brightness_command_topic": "s/bri/set",
                    "brightness_state_topic": "s/bri",
                    "brightness_scale": 100,
                }
            ],
        },
    )
    entity = jarvis.entity_object("light.scaled")
    # 0-255 in, device scale out: 128/255 of 100 is 50
    await entity.async_turn_on(brightness=128)
    assert client.payloads_for("s/bri/set") == ["50"]
    await entity.async_turn_on(brightness=255)
    assert client.payloads_for("s/bri/set") == ["50", "100"]
    # and back the other way
    await client.feed("s/bri", "50")
    assert state_of(jarvis, "light.scaled").attributes["brightness"] == 128
    await client.feed("s/bri", "100")
    assert state_of(jarvis, "light.scaled").attributes["brightness"] == 255


async def test_json_schema_light(tmp_path):
    jarvis, client = await setup_mqtt(
        tmp_path,
        {
            "discovery": False,
            "light": [
                {
                    "name": "Bulb",
                    "schema": "json",
                    "state_topic": "zigbee2mqtt/Bulb",
                    "command_topic": "zigbee2mqtt/Bulb/set",
                    "brightness": True,
                }
            ],
        },
    )
    entity = jarvis.entity_object("light.bulb")
    await entity.async_turn_on(brightness=200, color_temp_kelvin=2700)
    payload = json.loads(client.last_publish("zigbee2mqtt/Bulb/set").payload)
    assert payload["state"] == "ON"
    assert payload["brightness"] == 200
    assert payload["color_temp"] == 370

    await client.feed(
        "zigbee2mqtt/Bulb", json.dumps({"state": "ON", "brightness": 100})
    )
    state = state_of(jarvis, "light.bulb")
    assert state.state == "on"
    assert state.attributes["brightness"] == 100

    await entity.async_turn_off()
    assert json.loads(client.last_publish().payload)["state"] == "OFF"


async def test_cover_position(tmp_path):
    jarvis, client = await setup_mqtt(
        tmp_path,
        {
            "discovery": False,
            "cover": [
                {
                    "name": "Blind",
                    "command_topic": "blind/set",
                    "position_topic": "blind/position",
                    "set_position_topic": "blind/position/set",
                }
            ],
        },
    )
    entity_id = "cover.blind"
    entity = jarvis.entity_object(entity_id)

    await entity.async_open_cover()
    assert client.payloads_for("blind/set") == ["OPEN"]
    await entity.async_close_cover()
    assert client.payloads_for("blind/set") == ["OPEN", "CLOSE"]
    await entity.async_stop_cover()
    assert client.payloads_for("blind/set") == ["OPEN", "CLOSE", "STOP"]

    await entity.async_set_cover_position(40)
    assert client.payloads_for("blind/position/set") == ["40"]

    await client.feed("blind/position", "0")
    state = state_of(jarvis, entity_id)
    assert state.state == "closed" and state.attributes["position"] == 0
    await client.feed("blind/position", "75")
    state = state_of(jarvis, entity_id)
    assert state.state == "open" and state.attributes["position"] == 75


async def test_climate_commands(tmp_path):
    jarvis, client = await setup_mqtt(
        tmp_path,
        {
            "discovery": False,
            "climate": [
                {
                    "name": "Boiler",
                    "modes": ["off", "heat"],
                    "fan_modes": ["low", "high"],
                    "mode_command_topic": "boiler/mode/set",
                    "mode_state_topic": "boiler/mode",
                    "temperature_command_topic": "boiler/temp/set",
                    "temperature_state_topic": "boiler/temp",
                    "current_temperature_topic": "boiler/current",
                    "fan_mode_command_topic": "boiler/fan/set",
                }
            ],
        },
    )
    entity_id = "climate.boiler"
    entity = jarvis.entity_object(entity_id)

    await entity.async_set_hvac_mode("heat")
    assert client.payloads_for("boiler/mode/set") == ["heat"]
    assert state_of(jarvis, entity_id).state == "heat"

    await entity.async_set_temperature(21.5)
    assert client.payloads_for("boiler/temp/set") == ["21.5"]
    await entity.async_set_fan_mode("high")
    assert client.payloads_for("boiler/fan/set") == ["high"]

    await client.feed("boiler/current", "19.25")
    assert state_of(jarvis, entity_id).attributes["current_temperature"] == 19.25
    await client.feed("boiler/mode", "off")
    assert state_of(jarvis, entity_id).state == "off"


async def test_button_number_select(tmp_path):
    jarvis, client = await setup_mqtt(
        tmp_path,
        {
            "discovery": False,
            "button": [{"name": "Restart", "command_topic": "dev/cmnd/restart"}],
            "number": [
                {
                    "name": "Volume",
                    "command_topic": "amp/volume/set",
                    "state_topic": "amp/volume",
                    "min": 0,
                    "max": 60,
                    "step": 1,
                }
            ],
            "select": [
                {
                    "name": "Mode",
                    "command_topic": "amp/mode/set",
                    "options": ["stereo", "surround"],
                }
            ],
        },
    )
    await jarvis.entity_object("button.restart").async_press()
    assert client.payloads_for("dev/cmnd/restart") == ["PRESS"]

    number = jarvis.entity_object("number.volume")
    await number.async_set_value(24)
    assert client.payloads_for("amp/volume/set") == ["24"]
    await client.feed("amp/volume", "31")
    state = state_of(jarvis, "number.volume")
    assert state.state == "31" and state.attributes["max"] == 60

    select = jarvis.entity_object("select.mode")
    await select.async_select_option("surround")
    assert client.payloads_for("amp/mode/set") == ["surround"]
    assert state_of(jarvis, "select.mode").state == "surround"
    try:
        await select.async_select_option("mono")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("selecting an unknown option should raise")


# --- discovery --------------------------------------------------------------
FULL_DISCOVERY_TOPIC = f"{DISCOVERY_PREFIX}/binary_sensor/0x0017/occupancy/config"
FULL_DISCOVERY_PAYLOAD = {
    "name": "Living room motion",
    "unique_id": "0x0017880103_occupancy_zigbee2mqtt",
    "state_topic": "zigbee2mqtt/Living room sensor",
    "value_template": "{{ value_json.occupancy }}",
    "payload_on": True,
    "payload_off": False,
    "device_class": "motion",
    "json_attributes_topic": "zigbee2mqtt/Living room sensor",
    "availability": [{"topic": "zigbee2mqtt/bridge/state"}],
    "device": {
        "identifiers": ["zigbee2mqtt_0x0017880103"],
        "name": "Living room sensor",
        "manufacturer": "Aqara",
        "model": "RTCGQ11LM",
        "sw_version": "1.0.1",
    },
}


async def test_discovery_creates_device_and_entity(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    await client.feed(FULL_DISCOVERY_TOPIC, json.dumps(FULL_DISCOVERY_PAYLOAD))

    entity_id = "binary_sensor.living_room_motion"
    assert state_of(jarvis, entity_id).state == "unavailable"

    # device landed in the device registry with its metadata
    device = jarvis.devices.get_by_identifier("zigbee2mqtt_0x0017880103")
    assert device is not None
    assert device.name == "Living room sensor"
    assert device.manufacturer == "Aqara"
    assert device.model == "RTCGQ11LM"
    assert device.sw_version == "1.0.1"

    entry = jarvis.entities.get(entity_id)
    assert entry is not None
    assert entry.platform == "mqtt"
    assert entry.unique_id == "0x0017880103_occupancy_zigbee2mqtt"
    assert entry.device_id == device.id

    await client.feed("zigbee2mqtt/bridge/state", "online")
    await client.feed(
        "zigbee2mqtt/Living room sensor",
        json.dumps({"occupancy": True, "battery": 99, "linkquality": 60}),
    )
    state = state_of(jarvis, entity_id)
    assert state.state == "on"
    assert state.attributes["device_class"] == "motion"
    assert state.attributes["battery"] == 99

    await client.feed(
        "zigbee2mqtt/Living room sensor", json.dumps({"occupancy": False, "battery": 99})
    )
    assert state_of(jarvis, entity_id).state == "off"

    # re-publishing the identical (retained) config must not duplicate anything
    before = len(jarvis.states.entity_ids())
    await client.feed(FULL_DISCOVERY_TOPIC, json.dumps(FULL_DISCOVERY_PAYLOAD))
    assert len(jarvis.states.entity_ids()) == before
    assert state_of(jarvis, entity_id).state == "off"


TASMOTA_TOPIC = f"{DISCOVERY_PREFIX}/switch/2C3AE8_RL_1/config"
TASMOTA_PAYLOAD = {
    "~": "tasmota_2C3AE8/",
    "n": "Tasmota relay",
    "stat_t": "~stat/RESULT",
    "cmd_t": "~cmnd/POWER",
    "avty_t": "~tele/LWT",
    "pl_avail": "Online",
    "pl_not_avail": "Offline",
    "val_tpl": "{{ value_json.POWER }}",
    "pl_off": "OFF",
    "pl_on": "ON",
    "uniq_id": "2C3AE8_RL_1",
    "dev": {
        "ids": ["2C3AE8"],
        "name": "Tasmota plug",
        "mf": "Tasmota",
        "mdl": "Sonoff S20",
        "sw": "9.5.0",
    },
}


async def test_discovery_abbreviated_tasmota_payload(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    await client.feed(TASMOTA_TOPIC, json.dumps(TASMOTA_PAYLOAD))

    entity_id = "switch.tasmota_relay"
    entity = jarvis.entity_object(entity_id)
    assert entity is not None

    # abbreviations expanded and `~` resolved
    assert entity.config["state_topic"] == "tasmota_2C3AE8/stat/RESULT"
    assert entity.config["command_topic"] == "tasmota_2C3AE8/cmnd/POWER"
    assert entity.config["availability_topic"] == "tasmota_2C3AE8/tele/LWT"

    device = jarvis.devices.get_by_identifier("2C3AE8")
    assert device is not None and device.manufacturer == "Tasmota"
    assert device.model == "Sonoff S20"

    assert state_of(jarvis, entity_id).state == "unavailable"
    await client.feed("tasmota_2C3AE8/tele/LWT", "Online")
    await client.feed("tasmota_2C3AE8/stat/RESULT", json.dumps({"POWER": "ON"}))
    assert state_of(jarvis, entity_id).state == "on"

    await entity.async_turn_off()
    message = client.last_publish()
    assert message.topic == "tasmota_2C3AE8/cmnd/POWER"
    assert message.payload == "OFF"


async def test_discovery_empty_payload_removes_entity(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    await client.feed(FULL_DISCOVERY_TOPIC, json.dumps(FULL_DISCOVERY_PAYLOAD))
    entity_id = "binary_sensor.living_room_motion"
    assert jarvis.states.get(entity_id) is not None
    subscriptions_before = len(client.subscriptions)

    await client.feed(FULL_DISCOVERY_TOPIC, "")

    assert jarvis.states.get(entity_id) is None
    assert jarvis.entity_object(entity_id) is None
    assert jarvis.entities.get(entity_id) is None
    # the entity's topic subscriptions were torn down too
    assert len(client.subscriptions) < subscriptions_before
    # and further device messages are ignored rather than resurrecting state
    await client.feed("zigbee2mqtt/Living room sensor", json.dumps({"occupancy": True}))
    assert jarvis.states.get(entity_id) is None


async def test_discovery_light_with_node_id_and_brightness(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    payload = {
        "~": "shellies/shelly1/light/0",
        "name": "Shelly light",
        "uniq_id": "shelly1_light_0",
        "stat_t": "~/status",
        "cmd_t": "~/set",
        "bri_stat_t": "~/brightness",
        "bri_cmd_t": "~/brightness/set",
        "dev": {"ids": ["shelly1"], "name": "Shelly 1"},
    }
    await client.feed(f"{DISCOVERY_PREFIX}/light/shelly1/0/config", json.dumps(payload))

    entity_id = "light.shelly_light"
    entity = jarvis.entity_object(entity_id)
    assert entity is not None
    await entity.async_turn_on(brightness=51)
    assert client.payloads_for("shellies/shelly1/light/0/brightness/set") == ["51"]
    assert client.payloads_for("shellies/shelly1/light/0/set") == ["ON"]


async def test_discovery_device_bundle(tmp_path):
    """The newer single-payload device form (components map)."""
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    payload = {
        "dev": {"ids": ["bundle01"], "name": "Weather station", "mf": "ACME"},
        "o": {"name": "acme2mqtt"},
        "~": "acme/ws1",
        "cmps": {
            "temp": {
                "p": "sensor",
                "name": "Temperature",
                "uniq_id": "bundle01_temp",
                "dev_cla": "temperature",
                "unit_of_meas": "C",
                "stat_t": "~/state",
                "val_tpl": "{{ value_json.temp }}",
            },
            "relay": {
                "p": "switch",
                "name": "Heater",
                "uniq_id": "bundle01_relay",
                "stat_t": "~/relay",
                "cmd_t": "~/relay/set",
            },
        },
    }
    await client.feed(f"{DISCOVERY_PREFIX}/device/bundle01/config", json.dumps(payload))

    await client.feed("acme/ws1/state", json.dumps({"temp": 12.5}))
    assert state_of(jarvis, "sensor.temperature").state == "12.5"

    heater = jarvis.entity_object("switch.heater")
    await heater.async_turn_on()
    assert client.payloads_for("acme/ws1/relay/set") == ["ON"]

    device = jarvis.devices.get_by_identifier("bundle01")
    assert device is not None and device.manufacturer == "ACME"

    # removing the bundle removes every entity it created
    await client.feed(f"{DISCOVERY_PREFIX}/device/bundle01/config", "")
    assert jarvis.states.get("sensor.temperature") is None
    assert jarvis.states.get("switch.heater") is None


async def test_discovery_ignores_junk(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    before = set(jarvis.states.entity_ids())
    await client.feed(f"{DISCOVERY_PREFIX}/sensor/broken/config", "{not json")
    await client.feed(f"{DISCOVERY_PREFIX}/sensor/list/config", "[1, 2]")
    await client.feed(
        f"{DISCOVERY_PREFIX}/vacuum/robot/config", json.dumps({"name": "Robot"})
    )
    await client.feed(
        f"{DISCOVERY_PREFIX}/device_automation/x/config", json.dumps({"atype": "trigger"})
    )
    assert set(jarvis.states.entity_ids()) == before


async def test_discovery_disabled(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": False})
    await client.feed(FULL_DISCOVERY_TOPIC, json.dumps(FULL_DISCOVERY_PAYLOAD))
    assert jarvis.states.get("binary_sensor.living_room_motion") is None
    assert client.subscriptions == []


def test_parse_discovery_topic():
    assert parse_discovery_topic("homeassistant/switch/a/config", "homeassistant") == (
        "switch", None, "a",
    )
    assert parse_discovery_topic("homeassistant/switch/n/a/config", "homeassistant") == (
        "switch", "n", "a",
    )
    assert parse_discovery_topic("elsewhere/switch/a/config", "homeassistant") is None
    assert parse_discovery_topic("homeassistant/switch/a/state", "homeassistant") is None


def test_normalize_discovery_payload_keeps_canonical_keys():
    config = normalize_discovery_payload(
        {"~": "base/", "n": "X", "name": "Explicit", "stat_t": "~state", "avty_t": "avail~"}
    )
    assert config["name"] == "Explicit"  # full key wins over the abbreviation
    assert config["state_topic"] == "base/state"
    assert config["availability_topic"] == "availbase/"
    assert "~" not in config


# --- services ---------------------------------------------------------------
async def test_mqtt_publish_service(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": False})
    assert jarvis.services.has_service("mqtt", "publish")

    await jarvis.async_call_service(
        "mqtt", "publish", {"topic": "jarvis/test", "payload": "hello", "retain": True}
    )
    message = client.last_publish("jarvis/test")
    assert message.payload == "hello"
    assert message.retain is True

    # dict payloads are serialised, templates are rendered
    await jarvis.async_call_service(
        "mqtt", "publish", {"topic": "jarvis/json", "payload": {"a": 1}}
    )
    assert json.loads(client.last_publish("jarvis/json").payload) == {"a": 1}

    await jarvis.async_call_service(
        "mqtt",
        "publish",
        {
            "topic": "jarvis/tpl",
            "payload": '{"v": 5}',
            "payload_template": "{{ value_json.v }}",
        },
    )
    assert client.last_publish("jarvis/tpl").payload == "5"

    raised = False
    try:
        await jarvis.async_call_service("mqtt", "publish", {"payload": "x"})
    except ValueError:
        raised = True
    assert raised, "publishing without a topic should raise"


async def test_mqtt_dump_service(tmp_path):
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": False})

    async def _feeder():
        await asyncio.sleep(0.02)
        await client.feed("dump/a", "one")
        await client.feed("dump/b", "two")
        await client.feed("other/c", "ignored")

    response, _ = await asyncio.gather(
        jarvis.async_call_service(
            "mqtt", "dump", {"topic": "dump/#", "seconds": 0.15}, return_response=True
        ),
        _feeder(),
    )
    assert response["count"] == 2
    assert [m["topic"] for m in response["messages"]] == ["dump/a", "dump/b"]
    dump_file = Path(jarvis.config_dir) / "mqtt_dump.txt"
    assert dump_file.exists()
    assert "dump/a one" in dump_file.read_text()
    # the temporary subscription is gone again
    assert "dump/#" not in client.subscriptions


async def test_birth_message_and_shutdown_will(tmp_path):
    jarvis, client = await setup_mqtt(
        tmp_path,
        {
            "discovery": False,
            "birth_topic": "jarvis/status",
            "birth_payload": "online",
            "will_topic": "jarvis/status",
            "will_payload": "offline",
        },
    )
    assert client.payloads_for("jarvis/status") == ["online"]
    assert client.last_publish("jarvis/status").retain is True

    jarvis.is_running = True
    await jarvis.async_stop()
    assert client.payloads_for("jarvis/status") == ["online", "offline"]
    assert client.connected is False


async def test_setup_rejects_bad_config(tmp_path):
    jarvis = Jarvis(tmp_path)
    jarvis.data["mqtt"] = FakeMqttClient()
    assert await mqtt_integration.async_setup(jarvis, ["not", "a", "mapping"]) is False


async def test_setup_without_config_block(tmp_path):
    jarvis = Jarvis(tmp_path)
    client = FakeMqttClient()
    jarvis.data["mqtt"] = client
    assert await mqtt_integration.async_setup(jarvis, None) is True
    # discovery defaults to on
    assert f"{DISCOVERY_PREFIX}/+/+/config" in client.subscriptions


async def test_client_factory_injection(tmp_path):
    jarvis = Jarvis(tmp_path)
    made = []

    def _factory(config):
        client = FakeMqttClient(broker=config.get("broker", "x"))
        made.append(client)
        return client

    jarvis.data["mqtt_client_factory"] = _factory
    assert await mqtt_integration.async_setup(jarvis, {"broker": "brokerhost"}) is True
    assert made and jarvis.data["mqtt"] is made[0]
    assert made[0].broker == "brokerhost"


# ===========================================================================
# Regression tests for the verify pass. Each one fails against the code as it
# stood before the corresponding fix.
# ===========================================================================
import pytest  # noqa: E402

from jarvis.integrations.mqtt import entity as mqtt_entity  # noqa: E402


class FailingClient(FakeMqttClient):
    """A broker that refuses every publish."""

    async def _backend_publish(self, topic, payload, retain, qos):
        raise ConnectionResetError("broker gone")


class RecordingClient(FakeMqttClient):
    """Records the qos each backend subscribe was issued with."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.backend_subscribes: list[tuple[str, int]] = []

    async def _backend_subscribe(self, topic, qos):
        self.backend_subscribes.append((topic, qos))


async def setup_with_client(tmp_path, config, client):
    jarvis = Jarvis(tmp_path)
    jarvis.data["mqtt"] = client
    assert await mqtt_integration.async_setup(jarvis, config) is True
    return jarvis, client


# --- template sandbox (arbitrary code execution) ----------------------------
def test_value_template_cannot_reach_python_internals():
    """A discovery payload is attacker-reachable input; it must not be able to
    walk out of the template into the interpreter."""
    payload = json.dumps({"v": 1})
    for hostile in (
        "{{ ''.__class__.__mro__[1].__name__ }}",
        "{{ ''.__class__.__mro__[1].__subclasses__()|length }}",
        "{{ value_json.__class__ }}",
        "{{ self.__init__.__globals__ }}",
        "{{ cycler.__init__.__globals__.os.popen('id').read() }}",
    ):
        assert render_value_template(hostile, payload, default="BLOCKED") == "BLOCKED", (
            f"template escaped the sandbox: {hostile}"
        )
    # ...while ordinary templates keep working
    assert render_value_template("{{ value_json.v }}", payload) == "1"


async def test_hostile_discovery_template_cannot_execute_code(tmp_path):
    marker = Path(tmp_path) / "PWNED"
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    evil = (
        "{% for c in ''.__class__.__mro__[1].__subclasses__() %}"
        "{% if c.__name__ == 'catch_warnings' %}"
        "{{ c()._module.__builtins__['__import__']('os').system('touch MARKER') }}"
        "{% endif %}{% endfor %}"
    ).replace("MARKER", str(marker))

    await client.feed(
        f"{DISCOVERY_PREFIX}/sensor/pwn/config",
        json.dumps({"name": "Pwn", "state_topic": "s/pwn", "value_template": evil}),
    )
    await client.feed("s/pwn", "x")

    assert not marker.exists(), "MQTT discovery payload executed arbitrary code"
    assert state_of(jarvis, "sensor.pwn").state == "unknown"


def test_fallback_renderer_refuses_dunder_walking():
    """The jinja2-less fallback path must not become the soft underbelly."""
    rendered = mqtt_entity._fallback_render(
        "{{ value.__class__ }}", {"value": "abc"}
    )
    assert "class" not in rendered.lower()
    assert mqtt_entity._fallback_render("{{ value }}", {"value": "abc"}) == "abc"


def test_template_cache_is_bounded():
    """A hostile broker can mint endless distinct templates."""
    mqtt_entity._TEMPLATE_CACHE.clear()
    for index in range(mqtt_entity._TEMPLATE_CACHE_MAX + 50):
        render_value_template("{{ value }}" + " " * index, "v")
    assert len(mqtt_entity._TEMPLATE_CACHE) <= mqtt_entity._TEMPLATE_CACHE_MAX


# --- publish failures must not be swallowed ---------------------------------
async def test_publish_service_surfaces_broker_failure(tmp_path):
    jarvis, _ = await setup_with_client(
        tmp_path, {"discovery": False}, FailingClient()
    )
    with pytest.raises(RuntimeError):
        await jarvis.async_call_service("mqtt", "publish", {"topic": "t", "payload": "P"})


async def test_failed_publish_does_not_write_optimistic_state(tmp_path):
    """Reporting a device as on when the command never left is worse than an
    error: the UI, automations and the voice layer all believe it."""
    jarvis, client = await setup_with_client(
        tmp_path,
        {
            "discovery": False,
            "switch": [{"name": "Pump", "command_topic": "pump/set"}],
            "light": [{"name": "Lamp", "command_topic": "lamp/set"}],
            "cover": [{"name": "Blind", "command_topic": "blind/set"}],
            "lock": [{"name": "Gate", "command_topic": "gate/set"}],
        },
        FailingClient(),
    )
    await jarvis.entity_object("switch.pump").async_turn_on()
    assert state_of(jarvis, "switch.pump").state == "unknown"

    await jarvis.entity_object("light.lamp").async_turn_on(brightness=200)
    lamp = state_of(jarvis, "light.lamp")
    assert lamp.state == "unknown"
    assert "brightness" not in lamp.attributes

    await jarvis.entity_object("cover.blind").async_open_cover()
    assert state_of(jarvis, "cover.blind").state == "unknown"

    await jarvis.entity_object("lock.gate").async_lock()
    assert state_of(jarvis, "lock.gate").state == "unknown"

    assert client.publish_failures == 4


async def test_successful_publish_still_writes_optimistic_state(tmp_path):
    """The guard above must not silently disable optimistic mode."""
    jarvis, _ = await setup_mqtt(
        tmp_path,
        {
            "discovery": False,
            "switch": [{"name": "Pump", "command_topic": "pump/set"}],
            "lock": [{"name": "Gate", "command_topic": "gate/set"}],
        },
    )
    await jarvis.entity_object("switch.pump").async_turn_on()
    assert state_of(jarvis, "switch.pump").state == "on"
    await jarvis.entity_object("lock.gate").async_lock()
    assert state_of(jarvis, "lock.gate").state == "locked"
    await jarvis.entity_object("lock.gate").async_unlock()
    assert state_of(jarvis, "lock.gate").state == "unlocked"


# --- discovery cannot hijack an entity --------------------------------------
async def test_discovery_cannot_hijack_an_existing_unique_id(tmp_path):
    """unique_id is resolved per-platform, ignoring the domain, so a second
    config quoting someone else's unique_id would inherit their entity_id and
    with it their command topic."""
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    await client.feed(
        f"{DISCOVERY_PREFIX}/lock/front/config",
        json.dumps(
            {
                "name": "Front door",
                "unique_id": "shared-id",
                "command_topic": "real/lock/set",
                "state_topic": "real/lock",
            }
        ),
    )
    assert "lock.front_door" in jarvis.states.entity_ids()

    await client.feed(
        f"{DISCOVERY_PREFIX}/switch/evil/config",
        json.dumps(
            {"name": "Evil", "unique_id": "shared-id", "command_topic": "attacker/set"}
        ),
    )

    discovery = jarvis.data["mqtt_data"].discovery
    assert discovery.discovered_ids == ["lock_front"]
    assert jarvis.states.entity_ids() == ["lock.front_door"]

    lock = jarvis.entity_object("lock.front_door")
    await lock.async_lock()
    assert client.last_publish().topic == "real/lock/set"
    assert client.payloads_for("attacker/set") == []


async def test_discovery_cannot_hijack_a_yaml_entity(tmp_path):
    jarvis, client = await setup_mqtt(
        tmp_path,
        {
            "discovery": True,
            "switch": [
                {
                    "name": "Boiler",
                    "unique_id": "boiler-1",
                    "command_topic": "boiler/set",
                }
            ],
        },
    )
    await client.feed(
        f"{DISCOVERY_PREFIX}/switch/evil/config",
        json.dumps(
            {"name": "Evil", "unique_id": "boiler-1", "command_topic": "attacker/set"}
        ),
    )
    assert jarvis.data["mqtt_data"].discovery.discovered_ids == []
    await jarvis.entity_object("switch.boiler").async_turn_on()
    assert client.last_publish().topic == "boiler/set"


async def test_unique_id_is_released_when_discovery_is_removed(tmp_path):
    """The ownership guard must not permanently poison a unique_id."""
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    config = json.dumps(
        {"name": "Thing", "unique_id": "u1", "state_topic": "t/state"}
    )
    await client.feed(f"{DISCOVERY_PREFIX}/sensor/a/config", config)
    assert "sensor.thing" in jarvis.states.entity_ids()

    await client.feed(f"{DISCOVERY_PREFIX}/sensor/a/config", "")
    assert jarvis.states.entity_ids() == []

    # a different discovery topic may now claim the freed unique_id
    await client.feed(f"{DISCOVERY_PREFIX}/sensor/b/config", config)
    assert jarvis.data["mqtt_data"].discovery.discovered_ids == ["sensor_b"]
    assert "sensor.thing" in jarvis.states.entity_ids()


async def test_reconfiguring_the_same_topic_keeps_working(tmp_path):
    """The hijack guard must leave legitimate re-publishes alone."""
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    base = {"name": "Lamp", "unique_id": "u9", "command_topic": "l/set",
            "state_topic": "l/state"}
    await client.feed(f"{DISCOVERY_PREFIX}/switch/lamp/config", json.dumps(base))
    entity_id = "switch.lamp"
    assert entity_id in jarvis.states.entity_ids()

    await client.feed(
        f"{DISCOVERY_PREFIX}/switch/lamp/config",
        json.dumps({**base, "payload_on": "1", "state_topic": "l/state2"}),
    )
    assert jarvis.states.entity_ids() == [entity_id]
    await jarvis.entity_object(entity_id).async_turn_on()
    assert client.last_publish("l/set").payload == "1"
    await client.feed("l/state2", "1")
    assert state_of(jarvis, entity_id).state == "on"


async def test_device_bundle_component_can_change_platform(tmp_path):
    """A bundle keys components by name, so a re-publish can move one from
    switch to light -- the entity_id has to follow it."""
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    topic = f"{DISCOVERY_PREFIX}/device/box/config"
    device = {"identifiers": ["box1"], "name": "Box"}

    await client.feed(
        topic,
        json.dumps(
            {
                "device": device,
                "origin": {"name": "boxfw"},
                "components": {
                    "main": {
                        "platform": "switch",
                        "unique_id": "box_main",
                        "command_topic": "box/set",
                        "state_topic": "box/state",
                    }
                },
            }
        ),
    )
    assert "switch.box" in jarvis.states.entity_ids()

    await client.feed(
        topic,
        json.dumps(
            {
                "device": device,
                "origin": {"name": "boxfw"},
                "components": {
                    "main": {
                        "platform": "light",
                        "unique_id": "box_main",
                        "command_topic": "box/set",
                        "state_topic": "box/state",
                    }
                },
            }
        ),
    )
    ids = jarvis.states.entity_ids()
    assert "light.box" in ids, ids
    assert "switch.box" not in ids


# --- cover position must not be sent on the OPEN/CLOSE topic ----------------
async def test_cover_without_set_position_topic_refuses(tmp_path):
    jarvis, client = await setup_mqtt(
        tmp_path,
        {"discovery": False, "cover": [{"name": "Blind", "command_topic": "blind/cmd"}]},
    )
    with pytest.raises(ValueError):
        await jarvis.entity_object("cover.blind").async_set_cover_position(50)
    # crucially, nothing bogus went out on the OPEN/CLOSE/STOP topic
    assert client.payloads_for("blind/cmd") == []


# --- client bookkeeping -----------------------------------------------------
async def test_subscription_qos_survives_a_reconnect(tmp_path):
    client = RecordingClient()
    await client.async_connect()
    await client.async_subscribe("a/high", lambda m: None, qos=2)
    await client.async_subscribe("a/low", lambda m: None, qos=0)
    assert ("a/high", 2) in client.backend_subscribes

    client.backend_subscribes.clear()
    await client.async_disconnect()
    await client.async_connect()
    assert sorted(client.backend_subscribes) == [("a/high", 2), ("a/low", 0)]


async def test_second_subscriber_upgrades_the_topic_qos():
    client = RecordingClient()
    await client.async_connect()
    await client.async_subscribe("t", lambda m: None, qos=0)
    await client.async_subscribe("t", lambda m: None, qos=1)
    assert ("t", 1) in client.backend_subscribes


async def test_dump_seconds_is_clamped(tmp_path, monkeypatch):
    """An unbounded `seconds` pins the service call open for the process life."""
    monkeypatch.setattr(mqtt_integration, "MAX_DUMP_SECONDS", 0.05)
    jarvis, _ = await setup_mqtt(tmp_path, {"discovery": False})
    response = await asyncio.wait_for(
        jarvis.async_call_service(
            "mqtt", "dump", {"topic": "#", "seconds": 10**9}, return_response=True
        ),
        timeout=5,
    )
    assert response["count"] == 0


async def test_unique_id_ownership_holds_without_the_registry_check(tmp_path):
    """Two independent layers refuse a hijack: the entity-registry lookup and
    discovery's own unique_id ledger. `entity_objects` is a plain dict shared
    by every platform, so the ledger is what still holds if that bookkeeping
    is rebuilt underneath us."""
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    await client.feed(
        f"{DISCOVERY_PREFIX}/lock/front/config",
        json.dumps(
            {"name": "Front door", "unique_id": "dup", "command_topic": "real/lock/set"}
        ),
    )
    assert "lock.front_door" in jarvis.states.entity_ids()

    # simulate the shared entity_objects map being rebuilt by something else
    jarvis.data["entity_objects"] = {}

    await client.feed(
        f"{DISCOVERY_PREFIX}/switch/evil/config",
        json.dumps({"name": "Evil", "unique_id": "dup", "command_topic": "attacker/set"}),
    )
    assert jarvis.data["mqtt_data"].discovery.discovered_ids == ["lock_front"]
    assert client.payloads_for("attacker/set") == []


async def test_json_attributes_cannot_shadow_core_attributes(tmp_path):
    """`json_attributes_topic` is a device data channel. Letting it rewrite
    friendly_name hands a cheap plug the name the voice layer resolves against."""
    jarvis, client = await setup_mqtt(tmp_path, {"discovery": True})
    await client.feed(
        f"{DISCOVERY_PREFIX}/lock/rogue/config",
        json.dumps(
            {
                "name": "Cheap plug",
                "unique_id": "r1",
                "command_topic": "r/set",
                "json_attributes_topic": "r/attrs",
            }
        ),
    )
    await client.feed(
        "r/attrs",
        json.dumps(
            {
                "friendly_name": "Front Door Lock",
                "device_class": "door",
                "supported_features": 999,
                "icon": "mdi:evil",
                "battery": 87,
            }
        ),
    )
    state = state_of(jarvis, "lock.cheap_plug")
    assert state.attributes["friendly_name"] == "Cheap plug"
    assert state.attributes.get("supported_features") != 999
    assert state.attributes.get("icon") != "mdi:evil"
    assert state.attributes.get("device_class") != "door"
    # genuine device data still lands
    assert state.attributes["battery"] == 87


async def test_availability_all_mode_tolerates_a_repeated_topic(tmp_path):
    jarvis, client = await setup_mqtt(
        tmp_path,
        {
            "discovery": False,
            "sensor": [
                {
                    "name": "S",
                    "state_topic": "s/v",
                    "availability_mode": "all",
                    "availability": [
                        {"topic": "a/1"},
                        {"topic": "a/1", "payload_available": "online"},
                    ],
                }
            ],
        },
    )
    await client.feed("a/1", "online")
    assert state_of(jarvis, "sensor.s").state != "unavailable"
    await client.feed("a/1", "offline")
    assert state_of(jarvis, "sensor.s").state == "unavailable"


async def test_availability_all_mode_still_needs_every_distinct_topic(tmp_path):
    jarvis, client = await setup_mqtt(
        tmp_path,
        {
            "discovery": False,
            "sensor": [
                {
                    "name": "S",
                    "state_topic": "s/v",
                    "availability_mode": "all",
                    "availability": [{"topic": "a/1"}, {"topic": "a/2"}],
                }
            ],
        },
    )
    await client.feed("a/1", "online")
    assert state_of(jarvis, "sensor.s").state == "unavailable"
    await client.feed("a/2", "online")
    assert state_of(jarvis, "sensor.s").state != "unavailable"
    await client.feed("a/2", "offline")
    assert state_of(jarvis, "sensor.s").state == "unavailable"


# --- TLS must not depend on which backend happens to be installed ----------
#
# `tls: true` was honoured by the paho fallback (`client.tls_set()`) and
# silently ignored by aiomqtt, which is the PREFERRED backend. aiomqtt has no
# boolean flag — it takes an ssl.SSLContext — so a kwargs dict that never
# mentions TLS connects in cleartext, carrying the broker username and password
# with it, while the configuration says it is encrypted.

def _aiomqtt_kwargs(monkeypatch, **overrides):
    """The kwargs AiomqttClient would hand to aiomqtt.Client."""
    import sys
    import types

    from jarvis.integrations.mqtt import client as client_mod

    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeWill:
        def __init__(self, **kwargs):
            pass

    fake = types.ModuleType("aiomqtt")
    fake.Client = FakeClient
    fake.Will = FakeWill
    monkeypatch.setitem(sys.modules, "aiomqtt", fake)

    opts = {"broker": "broker.example", "port": 8883, "username": "jarvis",
            "password": "hunter2"}
    opts.update(overrides)
    client_mod.AiomqttClient(**opts)._build_client()
    return captured


def test_tls_true_actually_configures_tls_on_the_aiomqtt_backend(monkeypatch):
    import ssl

    captured = _aiomqtt_kwargs(monkeypatch, tls=True)
    assert "tls_context" in captured, (
        "tls: true was ignored — the connection, and the password in it, "
        "would go out in cleartext"
    )
    ctx = captured["tls_context"]
    assert isinstance(ctx, ssl.SSLContext)
    # A context that does not verify is worse than no TLS: it looks encrypted
    # and authenticates nothing.
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_tls_false_stays_plaintext(monkeypatch):
    assert "tls_context" not in _aiomqtt_kwargs(monkeypatch, tls=False)
