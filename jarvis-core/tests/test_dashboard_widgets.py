"""The house widgets' commands (M63): the readings, the sky, a still.

Three reads behind the dashboard's non-graph widgets — `jarvis/sensors/readings`,
`jarvis/sky/summary`, `jarvis/vision/still` — named in
`tests/contracts/dashboard_layout.json`. What each test pins is the honest
answer when the thing is not there: `configured: false`, a `state: unknown`
with its reason, a `denied` with its decision. A widget that drew a blank in
those cases is the failure the four-states rule exists to prevent.

The still is the one with a policy. It is a look at the camera and must be
refused, rate-limited and audited exactly as one; a wall panel that could show
a `never` camera would make the consent setting decorative.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.api import common  # noqa: E402
from jarvis.api.websocket import WebSocketHandler  # noqa: E402
from jarvis.auth import DATA_AUTH, ENV_TOKEN, AuthManager  # noqa: E402
from jarvis.automation.util import DATA_CLOCK  # noqa: E402
from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import sky  # noqa: E402
from jarvis.integrations import sensors as sensors_integration  # noqa: E402
from test_api import FakeWebSocket, text_frame  # noqa: E402
from test_sky import (  # noqa: E402
    ELEVATION,
    EPHEMERIS,
    LATITUDE,
    LONGITUDE,
    FrozenClock,
    seed_cache,
)
from test_vision import FakeStack, make_jarvis as make_vision_house  # noqa: E402

#: Long enough for the worker to answer a command before the next frame lands
#: (a disconnect delivered first is honoured, but nothing is sent after it).
SETTLE = 0.05

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "tests/contracts/dashboard_layout.json").read_text()
)


def test_the_three_commands_the_contract_names_are_registered():
    for command in ("jarvis/sensors/readings", "jarvis/sky/summary", "jarvis/vision/still"):
        assert command in CONTRACT["commands"], f"{command} is not in the contract"
        assert command in WebSocketHandler._HANDLERS, f"{command} is not a websocket command"


# --- readings ----------------------------------------------------------------


def _seed_readings(jarvis: Jarvis) -> None:
    jarvis.states.set(
        "sensor.garage_temperature",
        "12.5",
        {"friendly_name": "Garage temperature", "unit_of_measurement": "°C",
         "device_class": "temperature", "area": "Garage"},
    )
    jarvis.states.set(
        "sensor.fridge_power",
        "91",
        {"friendly_name": "Fridge power", "unit_of_measurement": "W",
         "device_class": "power", "area": "Kitchen"},
    )
    jarvis.states.set("sensor.dead", "unavailable", {"friendly_name": "Dead"})
    # Not a reading: a light has no unit, and the readings widget is not the
    # device list.
    jarvis.states.set("light.hall", "on", {"friendly_name": "Hall"})


def test_readings_are_every_sensor_with_its_room_and_the_dead_ones_flagged(tmp_path):
    jarvis = Jarvis(tmp_path)
    _seed_readings(jarvis)
    payload = common.sensors_readings_payload(jarvis)
    assert payload["count"] == 3
    by_id = {row["entity_id"]: row for row in payload["readings"]}
    assert by_id["sensor.garage_temperature"]["area"] == "Garage"
    assert by_id["sensor.garage_temperature"]["value"] == 12.5
    assert by_id["sensor.garage_temperature"]["unit"] == "°C"
    assert by_id["sensor.fridge_power"]["device_class"] == "power"
    assert by_id["sensor.dead"]["available"] is False, "a dead sensor is kept and flagged, not hidden"
    assert "light.hall" not in by_id
    # No sensors integration was set up: the rows still come, and the flag
    # says new ones cannot arrive over MQTT until it is.
    assert payload["configured"] is False


def test_readings_filter_by_room_the_way_the_tool_does(tmp_path):
    jarvis = Jarvis(tmp_path)
    _seed_readings(jarvis)
    payload = common.sensors_readings_payload(jarvis, area="kitchen")
    assert [row["entity_id"] for row in payload["readings"]] == ["sensor.fridge_power"]
    assert payload["area"] == "kitchen"
    assert common.sensors_readings_payload(jarvis, limit=1)["count"] == 1


async def test_readings_say_the_sensors_integration_is_there_when_it_is(tmp_path):
    jarvis = Jarvis(tmp_path)
    assert await sensors_integration.async_setup(jarvis, {}) is True
    try:
        assert common.sensors_readings_payload(jarvis)["configured"] is True
        assert common.sensors_readings_payload(jarvis)["readings"] == []
    finally:
        jarvis.is_running = True
        await jarvis.async_stop()


async def test_the_readings_command_answers_over_the_socket(tmp_path, monkeypatch):
    """The whole path: auth, the frame, the reply — not only the payload helper."""
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    jarvis = Jarvis(tmp_path)
    manager = AuthManager()
    jarvis.data[DATA_AUTH] = manager
    _info, secret = await manager.create_token("console")
    _seed_readings(jarvis)
    socket = FakeWebSocket(
        [
            text_frame({"type": "auth", "access_token": secret}),
            text_frame({"id": 1, "type": "jarvis/sensors/readings", "area": "garage"}),
            {"type": "websocket.disconnect", "code": 1000},
        ],
        settle=SETTLE,
    )
    await WebSocketHandler(jarvis, socket).run()
    reply = next(frame for frame in socket.sent if frame.get("id") == 1)
    assert reply["success"] is True, reply
    assert [r["entity_id"] for r in reply["result"]["readings"]] == ["sensor.garage_temperature"]


# --- the sky -----------------------------------------------------------------


async def _house_under_the_sky(tmp_path, *, elements: bool, ephemeris: bool) -> Jarvis:
    jarvis = Jarvis(tmp_path)
    jarvis.config = {
        "jarvis": {
            "latitude": LATITUDE, "longitude": LONGITUDE, "elevation": ELEVATION,
            "time_zone": "Europe/London",
        }
    }
    jarvis.data[DATA_CLOCK] = FrozenClock()
    await jarvis.async_start()
    if elements:
        seed_cache(tmp_path / "sky" / "tle")
    assert await sky.async_setup(jarvis, {
        "download": False,
        "tle_cache": "sky/tle",
        "ephemeris": str(EPHEMERIS) if ephemeris else str(tmp_path / "sky" / "absent.bsp"),
    }) is True
    return jarvis


async def test_without_the_sky_integration_the_summary_says_so(tmp_path):
    jarvis = Jarvis(tmp_path)
    summary = await common.async_sky_summary(jarvis)
    assert summary["configured"] is False
    assert summary["pass"] is None and summary["moon"] is None


async def test_the_summary_is_the_next_pass_and_the_moon_from_cached_elements(tmp_path):
    jarvis = await _house_under_the_sky(tmp_path, elements=True, ephemeris=True)
    try:
        summary = await common.async_sky_summary(jarvis)
    finally:
        await jarvis.async_stop()
    assert summary["configured"] is True
    assert summary["satellite"] == "ISS (ZARYA)"
    # The same numbers the entities carry (test_sky pins them against the
    # fixture): a widget and a voice answer must never disagree.
    assert summary["pass"]["state"].startswith("2026-08-27T01:35")
    assert summary["pass"]["max_alt"] == 11 and summary["pass"]["visible"] is False
    assert summary["pass"]["next_visible"].startswith("2026-08-27T04:45")
    assert summary["pass"]["tle_age_hours"] == 12.0
    assert summary["moon"]["state"] == "waxing gibbous"
    assert 97.0 < summary["moon"]["illumination"] < 99.0
    assert summary["now"].startswith("2026-08-26T18:00")


async def test_before_anything_was_fetched_the_summary_says_not_yet_rather_than_guessing(tmp_path):
    """A fresh install, offline: no elements, no ephemeris. Both halves say why."""
    jarvis = await _house_under_the_sky(tmp_path, elements=False, ephemeris=False)
    try:
        summary = await common.async_sky_summary(jarvis)
    finally:
        await jarvis.async_stop()
    assert summary["configured"] is True
    assert summary["pass"]["state"] == "unknown" and summary["pass"]["reason"]
    assert summary["moon"]["state"] == "unknown" and summary["moon"]["reason"]


# --- a still -----------------------------------------------------------------


async def test_without_a_camera_the_still_says_how_one_is_added(tmp_path):
    jarvis = Jarvis(tmp_path)
    result = await common.async_vision_still(jarvis)
    assert result["configured"] is False
    assert result["status"] == "unconfigured"
    assert "image" not in result


async def test_a_still_is_the_frame_as_a_data_url_and_one_audit_row(tmp_path):
    stack = FakeStack()
    jarvis = await make_vision_house(tmp_path, stack)
    try:
        result = await common.async_vision_still(jarvis, camera="Front Door", requester="api:tok")
        assert result["status"] == "ok", result
        assert result["configured"] is True and result["cameras"] == ["Front Door"]
        header, encoded = result["image"].split(",", 1)
        assert header == "data:image/jpeg;base64"
        assert base64.b64decode(encoded) == stack.frame
        assert len(stack.camera_requests) == 1
        [row] = jarvis.data["vision"]["manager"].audit.as_dicts()
        assert row["action"] == "snapshot" and row["outcome"] == "ok"
        assert row["requester"] == "api:tok"
        assert row["reason"] == "a dashboard still"
    finally:
        await jarvis.async_stop()


async def test_a_still_from_a_never_camera_is_refused_before_any_fetch_and_audited(tmp_path):
    stack = FakeStack()
    jarvis = await make_vision_house(tmp_path, stack, consent="never")
    try:
        result = await common.async_vision_still(jarvis, camera="Front Door")
        assert result["status"] == "denied"
        assert result["decision"] == "policy_never"
        assert result["consent"] == "never"
        assert "image" not in result
        assert stack.camera_requests == [], "a refused still must not touch the camera"
        [row] = jarvis.data["vision"]["manager"].audit.as_dicts()
        assert row["outcome"] == "denied" and row["action"] == "snapshot"
    finally:
        await jarvis.async_stop()


async def test_the_only_camera_need_not_be_named(tmp_path):
    """The shipped House says `camera: ""`; on a one-camera house that is the camera."""
    stack = FakeStack()
    jarvis = await make_vision_house(tmp_path, stack)
    try:
        result = await common.async_vision_still(jarvis, camera="")
        assert result["status"] == "ok" and result["camera"] == "Front Door"
    finally:
        await jarvis.async_stop()


async def test_an_unknown_camera_names_the_ones_there_are(tmp_path):
    stack = FakeStack()
    jarvis = await make_vision_house(tmp_path, stack)
    try:
        result = await common.async_vision_still(jarvis, camera="Garage")
        assert result["status"] == "error" and "Front Door" in result["error"]
        assert stack.camera_requests == []
    finally:
        await jarvis.async_stop()


async def test_the_still_command_takes_its_requester_from_the_token_not_the_payload(
    tmp_path, monkeypatch
):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    stack = FakeStack()
    jarvis = await make_vision_house(tmp_path, stack)
    manager = AuthManager()
    jarvis.data[DATA_AUTH] = manager
    info, secret = await manager.create_token("wall-panel")
    socket = FakeWebSocket(
        [
            text_frame({"type": "auth", "access_token": secret}),
            text_frame({"id": 1, "type": "jarvis/vision/still", "requester": "the model"}),
            {"type": "websocket.disconnect", "code": 1000},
        ],
        settle=SETTLE,
    )
    try:
        await WebSocketHandler(jarvis, socket).run()
        reply = next(frame for frame in socket.sent if frame.get("id") == 1)
        assert reply["success"] is True, reply
        assert reply["result"]["image"].startswith("data:image/jpeg;base64,")
        [row] = jarvis.data["vision"]["manager"].audit.as_dicts()
        assert row["requester"] == f"api:{info.id}"
    finally:
        await jarvis.async_stop()
