"""Tests for the YAML-driven and HTTP integrations.

Covers: the shared template helper, `template` entities, `rest`,
`command_line`, `hue`, `wled` and the `demo` integration. No network, no
broker, no hardware — HTTP is faked with httpx.MockTransport and the only
subprocesses are `echo`/`true`.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.helpers import template as tpl  # noqa: E402
from jarvis.integrations.command_line import (  # noqa: E402
    CommandFailed,
    async_run_command,
)
from jarvis.integrations.command_line import async_setup as command_line_setup  # noqa: E402
from jarvis.integrations.demo import async_setup as demo_setup  # noqa: E402
from jarvis.integrations.domains import async_setup as domains_setup  # noqa: E402
from jarvis.integrations.hue import HueBridge, _normalise_v2_light  # noqa: E402
from jarvis.integrations.hue import async_setup as hue_setup  # noqa: E402
from jarvis.integrations.hue import create_client as hue_client  # noqa: E402
from jarvis.integrations.hue import kelvin_to_mirek, rgb_to_xy  # noqa: E402
from jarvis.integrations.rest import async_setup as rest_setup  # noqa: E402
from jarvis.integrations.rest import create_client as rest_client  # noqa: E402
from jarvis.integrations.template import async_setup as template_setup  # noqa: E402
from jarvis.integrations.wled import async_setup as wled_setup  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def make_jarvis(tmp_path: Path, with_domains: bool = False) -> Jarvis:
    jarvis = Jarvis(tmp_path)
    await jarvis.areas.load()
    await jarvis.devices.load()
    await jarvis.entities.load()
    if with_domains:
        await domains_setup(jarvis, None)
    return jarvis


async def shutdown(jarvis: Jarvis) -> None:
    """Cancel polling tasks / close clients so the loop ends clean."""
    jarvis.is_running = True
    await jarvis.async_stop()


# ===========================================================================
# helpers/template.py
# ===========================================================================
async def test_template_states_helpers(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    jarvis.states.set("sensor.outside", "21.5", {"unit_of_measurement": "°C"})
    jarvis.states.set("light.bed", "on", {"brightness": 200, "friendly_name": "Bed Light"})

    assert tpl.render(jarvis, "{{ states('sensor.outside') }}") == "21.5"
    assert tpl.render(jarvis, "{{ states.sensor.outside.state }}") == "21.5"
    assert tpl.render(jarvis, "{{ is_state('light.bed', 'on') }}") == "True"
    assert tpl.render(jarvis, "{{ is_state('light.bed', 'off') }}") == "False"
    assert tpl.render(jarvis, "{{ state_attr('light.bed', 'brightness') }}") == "200"
    assert tpl.render(jarvis, "{{ state_attr('light.bed', 'nope') }}") == "None"
    # unknown entities degrade instead of exploding
    assert tpl.render(jarvis, "{{ states('sensor.ghost') }}") == "unknown"
    assert tpl.render(jarvis, "{{ is_state('sensor.ghost', 'on') }}") == "False"
    # filter forms work too (HA supports both)
    assert tpl.render(jarvis, "{{ 'sensor.outside' | states }}") == "21.5"
    assert tpl.render(jarvis, "{{ 'light.bed' | state_attr('brightness') }}") == "200"
    assert tpl.render(jarvis, "{{ 'light.bed' | is_state('on') }}") == "True"


async def test_template_filters_and_functions(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    jarvis.states.set("sensor.a", "10")
    jarvis.states.set("sensor.b", "20")

    avg = "{{ ((states('sensor.a')|float + states('sensor.b')|float) / 2) | round(1) }}"
    assert tpl.render(jarvis, avg) == "15.0"
    assert tpl.render(jarvis, "{{ 'abc' | float(0) }}") == "0"  # default returned as given
    assert tpl.render(jarvis, "{{ '7' | int }}") == "7"
    assert tpl.render(jarvis, "{{ min(5, 2, 9) }} {{ max([1, 8, 3]) }}") == "2 8"
    assert tpl.render(jarvis, "{{ now().year > 2000 }}") == "True"
    assert tpl.render(jarvis, "{{ utcnow().tzinfo is not none }}") == "True"
    assert tpl.render(jarvis, "{{ as_timestamp('1970-01-01T00:00:10+00:00') }}") == "10.0"
    assert tpl.render(jarvis, "{{ 'Kitchen Light!' | slugify }}") == "kitchen_light"
    assert tpl.render(jarvis, "{{ '{\"a\": 1}' | from_json | to_json }}") == '{"a": 1}'


async def test_template_now_uses_the_configured_time_zone(tmp_path):
    """`{{ now() }}` and `at: "07:00:00"` must mean the same clock.

    A condition like `{{ now().hour >= 22 }}` guarding an automation that a
    time trigger fires is the common shape, and the two reading different zones
    is worse than either being wrong on its own: the automation fires and the
    condition then refuses it, for six months of the year.

    The template environment is module-global and cannot know which Jarvis is
    rendering, so `now` is rebound per render. Kathmandu is +05:45 with no DST,
    which no CI runner is ever set to.
    """
    jarvis = await make_jarvis(tmp_path)
    jarvis.config = {"jarvis": {"time_zone": "Asia/Kathmandu"}}

    offset = tpl.render(jarvis, "{{ now().utcoffset().total_seconds() }}")
    assert float(offset) == 5 * 3600 + 45 * 60

    # utcnow is untouched: it is UTC by definition, not by configuration.
    assert tpl.render(jarvis, "{{ utcnow().utcoffset().total_seconds() }}") == "0.0"

    # And the same clock the triggers use, not a second one that agrees today.
    from jarvis.automation.util import get_clock  # noqa: PLC0415

    assert tpl.render(jarvis, "{{ now().tzinfo }}") == str(get_clock(jarvis).now().tzinfo)


async def test_template_value_and_value_json(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    payload = json.dumps({"main": {"temp": 7.5}, "name": "Home"})
    assert tpl.render(jarvis, "{{ value_json.main.temp }}", {"value": payload}) == "7.5"
    assert tpl.render(jarvis, "{{ value }}", {"value": "raw-text"}) == "raw-text"
    # non-JSON payloads leave value_json empty rather than raising
    assert tpl.render(jarvis, "{{ value_json }}", {"value": "not json"}) == "None"


async def test_template_errors_and_render_complex(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    jarvis.states.set("sensor.a", "3")

    with pytest.raises(tpl.TemplateError):
        tpl.render(jarvis, "{{ unclosed(  }}")
    with pytest.raises(tpl.TemplateError):
        tpl.render(jarvis, "{{ 'abc' | float }}")
    assert tpl.render_safe(jarvis, "{{ 'abc' | float }}", default="fallback") == "fallback"

    complex_value = {
        "plain": "no templates here",
        "number": "{{ 1 + 2 }}",
        "nested": {"list": ["{{ states('sensor.a') }}", 5]},
        "untouched": 42,
    }
    result = tpl.render_complex(jarvis, complex_value)
    assert result["plain"] == "no templates here"
    assert result["number"] == 3  # parsed to a native int
    assert result["nested"]["list"] == [3, 5]
    assert result["untouched"] == 42

    assert tpl.is_template("{{ x }}") and not tpl.is_template("x")
    assert tpl.result_as_boolean("True") and tpl.result_as_boolean("on")
    assert not tpl.result_as_boolean("off") and not tpl.result_as_boolean("")
    assert tpl.extract_entities("{{ states('light.a') }}{{ states('sensor.b') }}") == {
        "light.a",
        "sensor.b",
    }


def test_template_helper_public_api_is_stable():
    """Other integrations import these two — the signatures are a contract."""
    import inspect

    assert list(inspect.signature(tpl.render).parameters) == ["jarvis", "tpl", "variables"]
    assert list(inspect.signature(tpl.render_complex).parameters) == [
        "jarvis",
        "value",
        "variables",
    ]


# ===========================================================================
# template integration
# ===========================================================================
async def test_template_entities_render_and_track_state(tmp_path):
    jarvis = await make_jarvis(tmp_path, with_domains=True)
    jarvis.states.set("sensor.raw", "10")

    await template_setup(
        jarvis,
        [
            {
                "sensor": [
                    {
                        "name": "Temp Doubled",
                        "state": "{{ states('sensor.raw') | float(0) * 2 }}",
                        "unit_of_measurement": "°C",
                        "device_class": "temperature",
                        "attributes": {"source": "sensor.raw", "half": "{{ 1 + 1 }}"},
                    }
                ],
                "binary_sensor": [
                    {
                        "name": "Too Hot",
                        "state": "{{ states('sensor.raw') | float(0) > 30 }}",
                        "device_class": "heat",
                    }
                ],
            }
        ],
    )

    doubled = jarvis.states.get("sensor.temp_doubled")
    assert doubled.state == "20.0"
    assert doubled.attributes["unit_of_measurement"] == "°C"
    assert doubled.attributes["device_class"] == "temperature"
    assert doubled.attributes["source"] == "sensor.raw"
    assert doubled.attributes["half"] == 2
    assert jarvis.states.get("binary_sensor.too_hot").state == "off"

    # a change to a referenced entity re-renders both template entities
    jarvis.states.set("sensor.raw", "40")
    assert jarvis.states.get("sensor.temp_doubled").state == "80.0"
    assert jarvis.states.get("binary_sensor.too_hot").state == "on"

    jarvis.states.set("sensor.raw", "0")
    assert jarvis.states.get("sensor.temp_doubled").state == "0.0"
    assert jarvis.states.get("binary_sensor.too_hot").state == "off"
    await shutdown(jarvis)


async def test_template_switch_runs_actions(tmp_path):
    jarvis = await make_jarvis(tmp_path, with_domains=True)
    jarvis.states.set("light.study", "off")

    await template_setup(
        jarvis,
        [
            {
                "switch": [
                    {
                        "name": "Study Proxy",
                        "state": "{{ is_state('light.study', 'on') }}",
                        "turn_on": {
                            "service": "light.turn_on",
                            "data": {"entity_id": "light.study", "brightness": "{{ 100 + 20 }}"},
                        },
                        "turn_off": {
                            "service": "light.turn_off",
                            "data": {"entity_id": "light.study"},
                        },
                    }
                ]
            }
        ],
    )

    assert jarvis.states.get("switch.study_proxy").state == "off"

    result = await jarvis.async_call_service(
        "switch", "turn_on", {"entity_id": "switch.study_proxy"}
    )
    assert result["failed"] == {}
    assert jarvis.states.get("light.study").state == "on"
    assert jarvis.states.get("light.study").attributes["brightness"] == 120
    assert jarvis.states.get("switch.study_proxy").state == "on"

    await jarvis.async_call_service("switch", "turn_off", {"entity_id": "switch.study_proxy"})
    assert jarvis.states.get("light.study").state == "off"
    assert jarvis.states.get("switch.study_proxy").state == "off"
    await shutdown(jarvis)


async def test_template_entity_unavailable_on_bad_template(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    await template_setup(
        jarvis,
        {"sensor": [{"name": "Broken", "state": "{{ 'abc' | float }}"}]},
    )
    assert jarvis.states.get("sensor.broken").state == "unavailable"
    await shutdown(jarvis)


# ===========================================================================
# rest
# ===========================================================================
def _rest_transport(recorder: list[httpx.Request]) -> httpx.MockTransport:
    pump_state = {"on": False}

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        if request.url.path == "/status":
            return httpx.Response(
                200,
                json={"power": 1234, "voltage": 230, "current": 5.4, "grid": "up"},
            )
        if request.url.path == "/pump":
            if request.method == "GET":
                return httpx.Response(200, json=dict(pump_state))
            pump_state["on"] = json.loads(request.content.decode())["on"]
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"error": "nope"})

    return httpx.MockTransport(handler)


async def test_rest_sensor_binary_sensor_and_switch(tmp_path):
    jarvis = await make_jarvis(tmp_path, with_domains=True)
    requests: list[httpx.Request] = []
    jarvis.data["rest"] = {"transport": _rest_transport(requests)}

    await rest_setup(
        jarvis,
        [
            {
                "resource": "http://inverter.test/status",
                "scan_interval": 60,
                "headers": {"X-Token": "abc"},
                "sensor": [
                    {
                        "name": "Solar Power",
                        "value_template": "{{ value_json.power }}",
                        "unit_of_measurement": "W",
                        "device_class": "power",
                        "json_attributes": ["voltage", "current"],
                    }
                ],
                "binary_sensor": [
                    {"name": "Grid Online", "value_template": "{{ value_json.grid == 'up' }}"}
                ],
                "switch": [
                    {
                        "name": "Garden Pump",
                        "resource": "http://inverter.test/pump",
                        "body_on": '{"on": true}',
                        "body_off": '{"on": false}',
                        "is_on_template": "{{ value_json.on }}",
                    }
                ],
            }
        ],
    )

    power = jarvis.states.get("sensor.solar_power")
    assert power.state == "1234"
    assert power.attributes["unit_of_measurement"] == "W"
    assert power.attributes["voltage"] == 230
    assert power.attributes["current"] == 5.4
    assert jarvis.states.get("binary_sensor.grid_online").state == "on"
    assert jarvis.states.get("switch.garden_pump").state == "off"

    # the block's resource is fetched once and shared by both entities
    status_calls = [r for r in requests if r.url.path == "/status"]
    assert len(status_calls) == 1
    assert status_calls[0].headers["x-token"] == "abc"

    # switching posts the configured body and the readback follows it
    await jarvis.async_call_service("switch", "turn_on", {"entity_id": "switch.garden_pump"})
    posted = [r for r in requests if r.url.path == "/pump" and r.method == "POST"]
    assert len(posted) == 1
    assert json.loads(posted[0].content.decode()) == {"on": True}
    assert jarvis.states.get("switch.garden_pump").state == "on"

    await jarvis.async_call_service("switch", "turn_off", {"entity_id": "switch.garden_pump"})
    assert json.loads(
        [r for r in requests if r.url.path == "/pump" and r.method == "POST"][-1].content.decode()
    ) == {"on": False}
    assert jarvis.states.get("switch.garden_pump").state == "off"
    await shutdown(jarvis)


async def test_rest_sensor_repolls_and_survives_errors(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    payload = {"power": 100}
    fail = {"now": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if fail["now"]:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=payload)

    jarvis.data["rest"] = {"transport": httpx.MockTransport(handler)}
    await rest_setup(
        jarvis,
        {
            "resource": "http://inverter.test/status",
            "sensor": [{"name": "Watts", "value_template": "{{ value_json.power }}"}],
        },
    )
    assert jarvis.states.get("sensor.watts").state == "100"

    payload["power"] = 250
    entity = jarvis.entity_object("sensor.watts")
    await entity.async_update_state()
    assert jarvis.states.get("sensor.watts").state == "250"

    fail["now"] = True
    await entity.async_update_state()
    assert jarvis.states.get("sensor.watts").state == "unavailable"
    await shutdown(jarvis)


async def test_rest_polls_on_its_scan_interval(tmp_path):
    """should_poll + EntityPlatform.scan_interval actually drive re-fetches."""
    jarvis = await make_jarvis(tmp_path)
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return httpx.Response(200, json={"power": hits["n"]})

    jarvis.data["rest"] = {"transport": httpx.MockTransport(handler)}
    await rest_setup(
        jarvis,
        [
            {
                "resource": "http://ticker.test/s",
                "scan_interval": 0.05,
                "sensor": [{"name": "Ticker", "value_template": "{{ value_json.power }}"}],
            }
        ],
    )
    assert jarvis.states.get("sensor.ticker").state == "1"

    await asyncio.sleep(0.2)
    polled = int(jarvis.states.get("sensor.ticker").state)
    assert polled >= 2, f"expected repeat polls, saw {polled}"
    await shutdown(jarvis)


# ===========================================================================
# command_line
# ===========================================================================
async def test_command_line_run_command_and_timeout():
    code, output = await async_run_command("echo hello world", timeout=10)
    assert code == 0
    assert output == "hello world"

    code, _ = await async_run_command("exit 3", timeout=10)
    assert code == 3

    with pytest.raises(CommandFailed):
        await async_run_command("sleep 5", timeout=0.15)


async def test_command_line_entities(tmp_path):
    jarvis = await make_jarvis(tmp_path, with_domains=True)
    await command_line_setup(
        jarvis,
        [
            {
                "sensor": {
                    "name": "Answer",
                    "command": "echo 42",
                    "unit_of_measurement": "units",
                    "value_template": "{{ value | int }}",
                }
            },
            {
                "sensor": {
                    "name": "Json Reading",
                    "command": 'echo \'{"temp": 19, "hum": 61}\'',
                    "value_template": "{{ value_json.temp }}",
                    "json_attributes": ["hum"],
                }
            },
            {"binary_sensor": {"name": "Service Up", "command": "echo running"}},
            {
                "switch": {
                    "name": "Shell Switch",
                    "command_on": "true",
                    "command_off": "true",
                    "command_state": "echo on",
                    "value_template": "{{ value == 'on' }}",
                }
            },
        ],
    )

    answer = jarvis.states.get("sensor.answer")
    assert answer.state == "42"
    assert answer.attributes["unit_of_measurement"] == "units"

    reading = jarvis.states.get("sensor.json_reading")
    assert reading.state == "19"
    assert reading.attributes["hum"] == 61

    assert jarvis.states.get("binary_sensor.service_up").state == "on"
    assert jarvis.states.get("switch.shell_switch").state == "on"

    await jarvis.async_call_service("switch", "turn_off", {"entity_id": "switch.shell_switch"})
    assert jarvis.states.get("switch.shell_switch").state == "off"
    await shutdown(jarvis)


async def test_command_line_failing_command_is_unavailable(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    await command_line_setup(
        jarvis, [{"sensor": {"name": "Nope", "command": "exit 1"}}]
    )
    assert jarvis.states.get("sensor.nope").state == "unavailable"
    await shutdown(jarvis)


# ===========================================================================
# hue
# ===========================================================================
def _hue_v2_transport(recorder: list[tuple[str, str, dict]]) -> httpx.MockTransport:
    light = {
        "id": "l-1",
        "metadata": {"name": "Desk Lamp", "archetype": "desk_lamp"},
        "on": {"on": False},
        "dimming": {"brightness": 40.0},
        "color_temperature": {"mirek": 300},
        "product_data": {"product_name": "Hue color lamp"},
    }
    group = {"id": "g-1", "on": {"on": True}, "dimming": {"brightness": 80.0}}
    room = {
        "id": "r-1",
        "metadata": {"name": "Office"},
        "services": [{"rid": "g-1", "rtype": "grouped_light"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content.decode()) if request.content else {}
        recorder.append((request.method, path, body))
        if request.method == "GET":
            if path == "/clip/v2/resource/light":
                return httpx.Response(200, json={"errors": [], "data": [light]})
            if path == "/clip/v2/resource/room":
                return httpx.Response(200, json={"errors": [], "data": [room]})
            if path == "/clip/v2/resource/grouped_light":
                return httpx.Response(200, json={"errors": [], "data": [group]})
        if request.method == "PUT":
            return httpx.Response(200, json={"errors": [], "data": [{"rid": "l-1"}]})
        return httpx.Response(404, json={"errors": ["unknown"]})

    return httpx.MockTransport(handler)


async def test_hue_v2_discovers_lights_and_groups(tmp_path):
    jarvis = await make_jarvis(tmp_path, with_domains=True)
    calls: list[tuple[str, str, dict]] = []
    jarvis.data["hue"] = {"transport": _hue_v2_transport(calls)}

    await hue_setup(jarvis, {"host": "bridge.test", "api_key": "KEY", "version": 2})

    lamp = jarvis.states.get("light.desk_lamp")
    assert lamp is not None and lamp.state == "off"
    office = jarvis.states.get("light.office")
    assert office is not None and office.state == "on"
    assert office.attributes["brightness"] == 204  # 80% of 255

    # turn on with brightness + colour temperature
    result = await jarvis.async_call_service(
        "light",
        "turn_on",
        {"entity_id": "light.desk_lamp", "brightness": 128, "color_temp_kelvin": 4000},
    )
    assert result["failed"] == {}
    puts = [c for c in calls if c[0] == "PUT"]
    assert len(puts) == 1
    assert puts[0][1] == "/clip/v2/resource/light/l-1"
    assert puts[0][2] == {
        "on": {"on": True},
        "dimming": {"brightness": 50.2},
        "color_temperature": {"mirek": kelvin_to_mirek(4000)},
    }
    state = jarvis.states.get("light.desk_lamp")
    assert state.state == "on"
    assert state.attributes["brightness"] == 128
    assert state.attributes["color_temp_kelvin"] == 4000

    # rgb goes out as CIE xy
    await jarvis.async_call_service(
        "light", "turn_on", {"entity_id": "light.desk_lamp", "rgb_color": [255, 0, 0]}
    )
    body = [c for c in calls if c[0] == "PUT"][-1][2]
    x, y = rgb_to_xy(255, 0, 0)
    assert body["color"] == {"xy": {"x": x, "y": y}}

    await jarvis.async_call_service("light", "turn_off", {"entity_id": "light.office"})
    off_call = [c for c in calls if c[0] == "PUT"][-1]
    assert off_call[1] == "/clip/v2/resource/grouped_light/g-1"
    assert off_call[2] == {"on": {"on": False}}
    assert jarvis.states.get("light.office").state == "off"
    await shutdown(jarvis)


async def test_hue_v1_fallback(tmp_path):
    jarvis = await make_jarvis(tmp_path, with_domains=True)
    calls: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content.decode()) if request.content else {}
        calls.append((request.method, path, body))
        if path.startswith("/clip/v2/"):
            return httpx.Response(404, json={"errors": ["not found"]})
        if path == "/api/KEY/lights":
            return httpx.Response(
                200,
                json={
                    "1": {
                        "name": "Hall Light",
                        "modelid": "LCT001",
                        "state": {"on": True, "bri": 254, "ct": 300, "reachable": True},
                    }
                },
            )
        if path == "/api/KEY/groups":
            return httpx.Response(200, json={})
        if path == "/api/KEY/lights/1/state":
            return httpx.Response(200, json=[{"success": {}}])
        return httpx.Response(404, json=[])

    jarvis.data["hue"] = {"transport": httpx.MockTransport(handler)}
    await hue_setup(jarvis, {"host": "old-bridge.test", "api_key": "KEY"})

    hall = jarvis.states.get("light.hall_light")
    assert hall is not None and hall.state == "on"
    assert hall.attributes["brightness"] == 255  # 254/254 of the v1 range

    await jarvis.async_call_service(
        "light", "turn_on", {"entity_id": "light.hall_light", "brightness": 128}
    )
    put = [c for c in calls if c[0] == "PUT"][-1]
    assert put[1] == "/api/KEY/lights/1/state"
    assert put[2]["on"] is True
    assert put[2]["bri"] == 127
    await shutdown(jarvis)


# ===========================================================================
# wled
# ===========================================================================
def _wled_transport(recorder: list[tuple[str, dict]]) -> httpx.MockTransport:
    state = {
        "on": False,
        "bri": 128,
        "seg": [{"id": 0, "col": [[255, 0, 0], [0, 0, 0], [0, 0, 0]], "fx": 0}],
    }
    info = {"name": "Desk Strip", "ver": "0.14.0", "mac": "aabbccddeeff", "arch": "esp32"}
    effects = ["Solid", "Blink", "Rainbow"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/json":
            recorder.append(("GET", {}))
            return httpx.Response(
                200,
                json={
                    "state": state,
                    "info": info,
                    "effects": effects,
                    "palettes": ["Default"],
                },
            )
        if request.method == "POST" and request.url.path == "/json/state":
            body = json.loads(request.content.decode())
            recorder.append(("POST", body))
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


async def test_wled_light_and_effect_select(tmp_path):
    jarvis = await make_jarvis(tmp_path, with_domains=True)
    calls: list[tuple[str, dict]] = []
    jarvis.data["wled"] = {"transport": _wled_transport(calls)}

    await wled_setup(jarvis, [{"host": "wled.test"}])

    light = jarvis.states.get("light.desk_strip")
    assert light is not None and light.state == "off"
    assert light.attributes["effect_list"] == ["Solid", "Blink", "Rainbow"]
    assert jarvis.states.get("select.desk_strip_effect").state == "Solid"

    await jarvis.async_call_service(
        "light",
        "turn_on",
        {"entity_id": "light.desk_strip", "brightness": 200, "rgb_color": [0, 255, 0]},
    )
    body = [c for c in calls if c[0] == "POST"][-1][1]
    assert body == {"on": True, "bri": 200, "seg": [{"id": 0, "col": [[0, 255, 0]]}]}

    light = jarvis.states.get("light.desk_strip")
    assert light.state == "on"
    assert light.attributes["brightness"] == 200
    assert light.attributes["rgb_color"] == [0, 255, 0]

    # effects are driven through the companion select entity
    await jarvis.async_call_service(
        "select", "select_option", {"entity_id": "select.desk_strip_effect", "option": "Rainbow"}
    )
    assert [c for c in calls if c[0] == "POST"][-1][1] == {"seg": [{"id": 0, "fx": 2}]}
    assert jarvis.states.get("select.desk_strip_effect").state == "Rainbow"
    assert jarvis.states.get("light.desk_strip").attributes["effect"] == "Rainbow"

    await jarvis.async_call_service("light", "turn_off", {"entity_id": "light.desk_strip"})
    assert [c for c in calls if c[0] == "POST"][-1][1] == {"on": False}
    assert jarvis.states.get("light.desk_strip").state == "off"
    await shutdown(jarvis)


async def test_wled_colour_temperature_is_converted_to_rgb(tmp_path):
    jarvis = await make_jarvis(tmp_path, with_domains=True)
    calls: list[tuple[str, dict]] = []
    jarvis.data["wled"] = {"transport": _wled_transport(calls)}
    await wled_setup(jarvis, {"host": "wled.test", "name": "Strip"})

    await jarvis.async_call_service(
        "light", "turn_on", {"entity_id": "light.strip", "color_temp_kelvin": 6500}
    )
    body = [c for c in calls if c[0] == "POST"][-1][1]
    colour = body["seg"][0]["col"][0]
    assert len(colour) == 3 and all(0 <= c <= 255 for c in colour)
    assert body["on"] is True
    await shutdown(jarvis)


# ===========================================================================
# demo
# ===========================================================================
async def test_demo_creates_entities_in_areas(tmp_path):
    jarvis = await make_jarvis(tmp_path, with_domains=True)
    await demo_setup(jarvis, {})

    for entity_id in (
        "light.ceiling_lights",
        "light.bed_light",
        "switch.decorative_lights",
        "sensor.outside_temperature",
        "binary_sensor.basement_motion",
        "climate.thermostat",
        "cover.living_room_window",
        "media_player.living_room_speaker",
        "fan.living_room_fan",
        "lock.front_door_lock",
        "number.target_humidity",
        "select.light_scene",
        "button.push_button",
        "vacuum.robot_vacuum",
    ):
        assert jarvis.states.get(entity_id) is not None, entity_id

    living_room = jarvis.areas.get_by_name("Living Room")
    assert living_room is not None
    assert jarvis.area_for_entity("light.ceiling_lights") == living_room.id

    # area targeting resolves through the registry
    result = await jarvis.async_call_service(
        "light", "turn_off", {"area_id": "Living Room"}
    )
    assert "light.ceiling_lights" in result["changed"]
    assert jarvis.states.get("light.ceiling_lights").state == "off"
    await shutdown(jarvis)


async def test_demo_entities_answer_the_method_contract(tmp_path):
    jarvis = await make_jarvis(tmp_path, with_domains=True)
    await demo_setup(jarvis, {"create_areas": False})

    async def call(domain, service, data):
        result = await jarvis.async_call_service(domain, service, data)
        assert result["failed"] == {}, result["failed"]
        return result

    await call("light", "turn_on", {"entity_id": "light.bed_light", "brightness": 55})
    bed = jarvis.states.get("light.bed_light")
    assert bed.state == "on" and bed.attributes["brightness"] == 55

    await call("light", "turn_on", {"entity_id": "light.bed_light", "rgb_color": [10, 20, 30]})
    assert jarvis.states.get("light.bed_light").attributes["rgb_color"] == [10, 20, 30]

    await call("light", "toggle", {"entity_id": "light.bed_light"})
    assert jarvis.states.get("light.bed_light").state == "off"

    await call("switch", "turn_off", {"entity_id": "switch.decorative_lights"})
    assert jarvis.states.get("switch.decorative_lights").state == "off"

    await call("fan", "turn_on", {"entity_id": "fan.living_room_fan", "percentage": 66})
    assert jarvis.states.get("fan.living_room_fan").attributes["percentage"] == 66

    await call("cover", "set_cover_position", {"entity_id": "cover.garage_door", "position": 30})
    garage = jarvis.states.get("cover.garage_door")
    assert garage.state == "open" and garage.attributes["current_position"] == 30
    await call("cover", "close_cover", {"entity_id": "cover.garage_door"})
    assert jarvis.states.get("cover.garage_door").state == "closed"

    await call("climate", "set_temperature", {"entity_id": "climate.thermostat", "temperature": 22.5})
    await call("climate", "set_hvac_mode", {"entity_id": "climate.thermostat", "hvac_mode": "cool"})
    await call("climate", "set_fan_mode", {"entity_id": "climate.thermostat", "fan_mode": "high"})
    thermostat = jarvis.states.get("climate.thermostat")
    assert thermostat.state == "cool"
    assert thermostat.attributes["temperature"] == 22.5
    assert thermostat.attributes["fan_mode"] == "high"

    await call("lock", "unlock", {"entity_id": "lock.front_door_lock"})
    assert jarvis.states.get("lock.front_door_lock").state == "unlocked"

    await call("media_player", "media_play", {"entity_id": "media_player.living_room_speaker"})
    assert jarvis.states.get("media_player.living_room_speaker").state == "playing"
    await call(
        "media_player",
        "volume_set",
        {"entity_id": "media_player.living_room_speaker", "volume_level": 0.8},
    )
    speaker = jarvis.states.get("media_player.living_room_speaker")
    assert speaker.attributes["volume_level"] == 0.8
    first_title = speaker.attributes["media_title"]
    await call("media_player", "media_next_track", {"entity_id": "media_player.living_room_speaker"})
    assert jarvis.states.get("media_player.living_room_speaker").attributes[
        "media_title"
    ] != first_title
    await call(
        "media_player",
        "play_media",
        {
            "entity_id": "media_player.living_room_speaker",
            "media_type": "music",
            "media_id": "http://stream.test/x.mp3",
        },
    )
    assert jarvis.states.get("media_player.living_room_speaker").attributes[
        "media_content_id"
    ] == "http://stream.test/x.mp3"
    await call("media_player", "media_pause", {"entity_id": "media_player.living_room_speaker"})
    assert jarvis.states.get("media_player.living_room_speaker").state == "paused"

    await call("number", "set_value", {"entity_id": "number.target_humidity", "value": 63})
    assert jarvis.states.get("number.target_humidity").state == "63.0"

    await call("select", "select_option", {"entity_id": "select.light_scene", "option": "Movie"})
    assert jarvis.states.get("select.light_scene").state == "Movie"

    await call("text", "set_value", {"entity_id": "text.doorbell_message", "value": "Hello"})
    assert jarvis.states.get("text.doorbell_message").state == "Hello"

    await call("button", "press", {"entity_id": "button.push_button"})
    assert jarvis.states.get("button.push_button").state == "pressed_1"

    await call("vacuum", "start", {"entity_id": "vacuum.robot_vacuum"})
    assert jarvis.states.get("vacuum.robot_vacuum").state == "cleaning"
    await call("vacuum", "return_to_base", {"entity_id": "vacuum.robot_vacuum"})
    assert jarvis.states.get("vacuum.robot_vacuum").state == "returning"
    await shutdown(jarvis)


async def test_demo_setup_with_bare_yaml_key(tmp_path):
    """`demo:` with no options is the zero-config entry point."""
    jarvis = await make_jarvis(tmp_path)
    assert await demo_setup(jarvis, None) is True
    assert jarvis.states.get("light.ceiling_lights").state == "on"
    assert len(jarvis.states.all()) >= 14
    await shutdown(jarvis)


async def test_demo_unsupported_action_reports_clearly(tmp_path):
    jarvis = await make_jarvis(tmp_path, with_domains=True)
    await demo_setup(jarvis, {"create_areas": False})

    # a sensor is not a light: the domain layer refuses it by name
    result = await jarvis.async_call_service(
        "light", "turn_on", {"entity_id": "sensor.outside_temperature"}
    )
    assert result["changed"] == []
    assert "sensor.outside_temperature" in result["failed"]
    await shutdown(jarvis)


# ===========================================================================
# regression tests for defects found in verification
# ===========================================================================
async def test_command_timeout_actually_stops_the_command():
    """`command_timeout` must bound wall-clock time, not just report on it.

    Regression: the old implementation cancelled `communicate()` via
    `wait_for`, which leaves the subprocess pipe transports half-torn-down;
    the following `process.wait()` then blocked until the command exited on
    its own. `async_run_command("sleep 30", timeout=0.3)` never returned, so
    one slow command wedged its EntityPlatform poll loop permanently. The
    old test used `sleep 5`, which always eventually returned — it could not
    tell a real timeout from waiting the command out.
    """
    started = time.monotonic()
    with pytest.raises(CommandFailed):
        await async_run_command("sleep 30", timeout=0.3)
    elapsed = time.monotonic() - started
    assert elapsed < 5, f"timeout took {elapsed:.1f}s; the command was not stopped"


async def test_command_timeout_kills_forked_children():
    """A shell that backgrounds a child must not keep the call alive.

    `process.kill()` only signals the shell. The backgrounded `sleep` inherits
    stdout, so the pipe stays open and output collection never finishes;
    killing the whole process group is what actually ends it.
    """
    started = time.monotonic()
    with pytest.raises(CommandFailed):
        await async_run_command("sleep 30 & sleep 30", timeout=0.3)
    elapsed = time.monotonic() - started
    assert elapsed < 5, f"orphaned child held the call open for {elapsed:.1f}s"


async def test_command_timeout_does_not_swallow_fast_commands():
    """The kill path must not cost anything on the happy path."""
    code, output = await async_run_command("echo quick", timeout=10)
    assert (code, output) == (0, "quick")


async def test_template_environment_is_sandboxed(tmp_path):
    """Templates must not be able to reach the Python runtime.

    Regression: the shared environment was a plain `jinja2.Environment`, so
    `{{ cycler.__init__.__globals__.os.popen('...').read() }}` executed shell
    commands and `{{ ''.__class__.__mro__ }}` walked to every loaded class.
    """
    jarvis = await make_jarvis(tmp_path)

    # Reaching the interpreter must be refused outright.
    for attack in (
        "{{ ''.__class__.__mro__[1].__subclasses__() | length }}",
        "{{ cycler.__init__.__globals__.os.popen('echo pwned').read() }}",
        "{{ ''.__class__.__base__.__subclasses__() }}",
    ):
        with pytest.raises(tpl.TemplateError):
            tpl.render(jarvis, attack)

    # Anything else that pokes at dunders must at minimum leak nothing.
    for probe in (
        "{{ ''.__class__ }}",
        "{{ namespace.__init__.__globals__ }}",
        "{{ lipsum.__globals__ }}",
        "{{ ''.__class__.__mro__ }}",
    ):
        assert tpl.render(jarvis, probe) == "", f"{probe} leaked runtime internals"

    # The environment is the *immutable* sandbox: templates cannot mutate the
    # objects they are handed either.
    assert tpl.render(jarvis, "{{ [].append }}") == ""

    # ...while ordinary attribute access still works.
    jarvis.states.set("light.bed", "on", {"brightness": 200})
    assert tpl.render(jarvis, "{{ states.light.bed.state }}") == "on"
    assert tpl.render(jarvis, "{{ value_json.a.b }}", {"value": '{"a": {"b": 1}}'}) == "1"


async def test_rest_verify_ssl_is_per_block(tmp_path):
    """One block opting out of TLS checks must not disarm the others.

    Regression: `verify_ssl` was folded across every block with `all(...)` and
    a single shared client, so `verify_ssl: false` on a lab endpoint silently
    turned certificate verification off for every other REST endpoint too.
    """
    import ssl

    jarvis = await make_jarvis(tmp_path)
    strict = rest_client(jarvis, verify_ssl=True)
    lax = rest_client(jarvis, verify_ssl=False)
    try:
        assert strict is not lax, "strict and lax blocks shared one client"
        # asking twice for the same setting reuses the connection pool
        assert rest_client(jarvis, verify_ssl=True) is strict

        def ssl_context(client):
            context = getattr(getattr(client._transport, "_pool", None), "_ssl_context", None)
            assert context is not None, "could not read httpx's SSL context"
            return context

        assert ssl_context(strict).verify_mode == ssl.CERT_REQUIRED
        assert ssl_context(strict).check_hostname is True
        assert ssl_context(lax).verify_mode == ssl.CERT_NONE
    finally:
        await strict.aclose()
        await lax.aclose()


async def test_rest_block_siblings_go_unavailable_together(tmp_path):
    """Entities riding a shared fetch must not keep serving stale values.

    Regression: only the first entity of a block polls; when its fetch failed
    the siblings kept publishing the last good payload and stayed `available`
    forever, because nothing ever told them the endpoint had gone away.
    """
    jarvis = await make_jarvis(tmp_path)
    fail = {"now": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if fail["now"]:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"power": 100, "grid": "up"})

    jarvis.data["rest"] = {"transport": httpx.MockTransport(handler)}
    await rest_setup(
        jarvis,
        {
            "resource": "http://x.test/s",
            "sensor": [{"name": "Shared Power", "value_template": "{{ value_json.power }}"}],
            "binary_sensor": [
                {"name": "Shared Grid", "value_template": "{{ value_json.grid == 'up' }}"}
            ],
        },
    )
    assert jarvis.states.get("sensor.shared_power").state == "100"
    assert jarvis.states.get("binary_sensor.shared_grid").state == "on"

    fail["now"] = True
    await jarvis.entity_object("sensor.shared_power").async_update_state()
    assert jarvis.states.get("sensor.shared_power").state == "unavailable"
    assert jarvis.states.get("binary_sensor.shared_grid").state == "unavailable"

    # and they recover together
    fail["now"] = False
    await jarvis.entity_object("sensor.shared_power").async_update_state()
    assert jarvis.states.get("sensor.shared_power").state == "100"
    assert jarvis.states.get("binary_sensor.shared_grid").state == "on"
    await shutdown(jarvis)


async def test_rest_uses_each_blocks_own_timeout(tmp_path):
    """Regression: every request used max(timeout) across all blocks."""
    jarvis = await make_jarvis(tmp_path)
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.host] = request.extensions.get("timeout")
        return httpx.Response(200, json={"v": 1})

    jarvis.data["rest"] = {"transport": httpx.MockTransport(handler)}
    await rest_setup(
        jarvis,
        [
            {"resource": "http://slow.test/a", "timeout": 90, "sensor": [{"name": "Slow One"}]},
            {"resource": "http://fast.test/b", "timeout": 1, "sensor": [{"name": "Fast One"}]},
        ],
    )
    assert seen["slow.test"]["read"] == 90.0
    assert seen["fast.test"]["read"] == 1.0, "the fast block inherited another block's timeout"
    await shutdown(jarvis)


async def test_hue_bridge_failure_marks_every_light_unavailable(tmp_path):
    """Regression: only the polling light noticed the bridge was gone."""
    jarvis = await make_jarvis(tmp_path, with_domains=True)
    fail = {"now": False}
    lights = {
        "data": [
            {"id": "l1", "metadata": {"name": "Alpha Lamp"}, "on": {"on": True}},
            {"id": "l2", "metadata": {"name": "Beta Lamp"}, "on": {"on": True}},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if fail["now"]:
            return httpx.Response(500, json={})
        if request.url.path == "/clip/v2/resource/light":
            return httpx.Response(200, json=lights)
        return httpx.Response(200, json={"data": []})

    jarvis.data["hue"] = {"transport": httpx.MockTransport(handler)}
    await hue_setup(jarvis, {"host": "b.test", "api_key": "K", "version": 2})
    assert jarvis.states.get("light.alpha_lamp").state == "on"
    assert jarvis.states.get("light.beta_lamp").state == "on"

    fail["now"] = True
    await jarvis.entity_object("light.alpha_lamp").async_update_state()
    assert jarvis.states.get("light.alpha_lamp").state == "unavailable"
    assert jarvis.states.get("light.beta_lamp").state == "unavailable"
    await shutdown(jarvis)


async def test_hue_transient_v2_failure_does_not_pin_v1(tmp_path):
    """A flaky probe must not downgrade a v2 bridge for the whole session.

    Regression: `async_detect_version` cached `version = 1` on any v2 error,
    including a timeout, so a bridge that blipped once spoke the legacy API
    until Jarvis restarted.
    """
    jarvis = await make_jarvis(tmp_path)
    v2_down = {"yes": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/clip/v2/"):
            if v2_down["yes"]:
                raise httpx.ConnectTimeout("transient", request=request)
            return httpx.Response(200, json={"data": [
                {"id": "l1", "metadata": {"name": "Recovered"}, "on": {"on": True}}
            ]})
        return httpx.Response(404, json=[])

    jarvis.data["hue"] = {"transport": httpx.MockTransport(handler)}
    client = hue_client(jarvis)
    bridge = HueBridge(jarvis, client, "b.test", "K", include_groups=False)
    try:
        with pytest.raises(Exception):
            await bridge.async_fetch()  # v2 blip, then v1 404s too
        assert bridge.version is None, "a failed probe must not pin an API version"

        v2_down["yes"] = False
        devices = await bridge.async_fetch()
        assert bridge.version == 2
        assert "light:l1" in devices
    finally:
        await client.aclose()
    await shutdown(jarvis)


def test_hue_v2_light_without_product_data():
    """Regression: `product_data: null` raised AttributeError mid-fetch,
    taking every other light on the bridge down with it."""
    device = _normalise_v2_light(
        {"id": "x", "metadata": {"name": "L", "archetype": "bulb"},
         "on": {"on": True}, "product_data": None}
    )
    assert device["name"] == "L"
    assert device["model"] == "bulb"


async def test_wled_controller_failure_takes_the_effect_select_down(tmp_path):
    """Regression: only the light polls, so the select stayed 'live' forever."""
    jarvis = await make_jarvis(tmp_path, with_domains=True)
    calls: list[tuple[str, dict]] = []
    inner = _wled_transport(calls)
    fail = {"now": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if fail["now"]:
            return httpx.Response(503, json={})
        return inner.handler(request)

    jarvis.data["wled"] = {"transport": httpx.MockTransport(handler)}
    await wled_setup(jarvis, [{"host": "wled.test"}])
    assert jarvis.states.get("select.desk_strip_effect").state == "Solid"

    fail["now"] = True
    await jarvis.entity_object("light.desk_strip").async_update_state()
    assert jarvis.states.get("light.desk_strip").state == "unavailable"
    assert jarvis.states.get("select.desk_strip_effect").state == "unavailable"
    await shutdown(jarvis)


async def test_template_entities_converge_across_dependencies(tmp_path):
    """A template entity reading another template entity must keep up.

    Regression: `render_all` made one ordered pass and suppressed re-renders
    triggered by template entities' own writes, so an entity declared before
    the one it depends on was stuck at its old value permanently.
    """
    jarvis = await make_jarvis(tmp_path)
    jarvis.states.set("sensor.raw", "5")
    await template_setup(
        jarvis,
        [
            {
                "sensor": [
                    # deliberately declared BEFORE the entity it reads
                    {"name": "Z Downstream", "state": "{{ states('sensor.a_upstream') }}"},
                    {"name": "A Upstream", "state": "{{ states('sensor.raw') }}"},
                ]
            }
        ],
    )
    assert jarvis.states.get("sensor.a_upstream").state == "5"
    assert jarvis.states.get("sensor.z_downstream").state == "5"

    jarvis.states.set("sensor.raw", "99")
    assert jarvis.states.get("sensor.a_upstream").state == "99"
    assert jarvis.states.get("sensor.z_downstream").state == "99"
    await shutdown(jarvis)


async def test_template_cycle_does_not_hang(tmp_path):
    """Two entities defined in terms of each other must not spin forever."""
    jarvis = await make_jarvis(tmp_path)
    await template_setup(
        jarvis,
        [
            {
                "sensor": [
                    {"name": "Ping", "state": "{{ states('sensor.pong') | int(0) + 1 }}"},
                    {"name": "Pong", "state": "{{ states('sensor.ping') | int(0) + 1 }}"},
                ]
            }
        ],
    )
    jarvis.states.set("sensor.trigger", "1")
    assert jarvis.states.get("sensor.ping") is not None
    assert jarvis.states.get("sensor.pong") is not None
    await shutdown(jarvis)


# ===========================================================================
# templates see integration entities (end-to-end)
# ===========================================================================
async def test_template_sensor_reads_demo_entity(tmp_path):
    jarvis = await make_jarvis(tmp_path, with_domains=True)
    await demo_setup(jarvis, {"create_areas": False})
    await template_setup(
        jarvis,
        [
            {
                "sensor": [
                    {
                        "name": "Feels Like",
                        "state": (
                            "{{ (states('sensor.outside_temperature') | float(0)"
                            " - states('sensor.outside_humidity') | float(0) / 10) | round(1) }}"
                        ),
                        "unit_of_measurement": "°C",
                    }
                ],
                "binary_sensor": [
                    {"name": "Ceiling On", "state": "{{ is_state('light.ceiling_lights', 'on') }}"}
                ],
            }
        ],
    )

    assert jarvis.states.get("sensor.feels_like").state == "10.2"
    assert jarvis.states.get("binary_sensor.ceiling_on").state == "on"

    await jarvis.async_call_service("light", "turn_off", {"entity_id": "light.ceiling_lights"})
    assert jarvis.states.get("binary_sensor.ceiling_on").state == "off"

    jarvis.entity_object("sensor.outside_temperature").set_value(20.0)
    assert jarvis.states.get("sensor.feels_like").state == "14.6"
    await shutdown(jarvis)
