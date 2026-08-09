"""Automation engine tests: triggers, conditions, script steps, scenes, helpers.

Nothing here touches the network, a broker or another agent's integration.
A fake `light` domain records every service call so these tests assert the
whole path (trigger -> condition -> action -> service data) on their own.
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.automation.actions import async_execute_script  # noqa: E402
from jarvis.automation.conditions import async_check  # noqa: E402
from jarvis.automation.engine import AutomationManager  # noqa: E402
from jarvis.automation.triggers import async_attach_trigger  # noqa: E402
from jarvis.automation.util import (  # noqa: E402
    get_clock,
    next_time_of_day,
    next_time_pattern,
    parse_duration,
    parse_time,
)
from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import automation as automation_integration  # noqa: E402
from jarvis.integrations import input_helpers as input_integration  # noqa: E402
from jarvis.integrations import scene as scene_integration  # noqa: E402
from jarvis.integrations import script as script_integration  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------
class FakeLight:
    """Stands in for the domains layer: records calls, moves the state."""

    def __init__(self, jarvis):
        self.calls = []
        jarvis.services.register("light", "turn_on", self._turn_on)
        jarvis.services.register("light", "turn_off", self._turn_off)
        self._jarvis = jarvis

    async def _turn_on(self, call):
        self.calls.append(("turn_on", dict(call.data)))
        for entity_id in _targets(call.data):
            attrs = {k: v for k, v in call.data.items() if k != "entity_id"}
            self._jarvis.states.set(entity_id, "on", attrs)

    async def _turn_off(self, call):
        self.calls.append(("turn_off", dict(call.data)))
        for entity_id in _targets(call.data):
            self._jarvis.states.set(entity_id, "off")

    @property
    def actions(self):
        return [name for name, _ in self.calls]

    def data_for(self, index=0):
        return self.calls[index][1]


def _targets(data):
    raw = data.get("entity_id")
    if raw is None:
        return []
    return list(raw) if isinstance(raw, (list, tuple)) else [raw]


class FakeClock:
    """Deterministic clock: sleeping just advances the (fake) wall time."""

    def __init__(self, start):
        self.current = start
        self.slept = []

    def now(self):
        return self.current

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.current = self.current + timedelta(seconds=seconds)
        await asyncio.sleep(0)


@pytest.fixture
def jarvis(tmp_path):
    return Jarvis(tmp_path)


@pytest.fixture
def light(jarvis):
    return FakeLight(jarvis)


async def _setup_automations(jarvis, configs):
    jarvis.config = {"automation": configs}
    await automation_integration.async_setup(jarvis, configs)
    return jarvis.data["automation"]


async def _settle(jarvis):
    """Let listeners *and* the runs they started finish.

    `bus.async_block_till_done` only covers listener tasks; automation and
    script runs are separate tasks, so wait on those too.
    """
    for _ in range(3):
        await jarvis.async_block_till_done()
        manager = jarvis.data.get("automation")
        if manager is not None:
            await manager.async_wait()
        for script in list(jarvis.data.get("script_objects", {}).values()):
            await script.runner.async_wait()
        await asyncio.sleep(0)


TURN_ON_HALL = {"service": "light.turn_on", "target": {"entity_id": "light.hall"}}


# ---------------------------------------------------------------------------
# triggers
# ---------------------------------------------------------------------------
async def test_state_trigger_runs_the_action(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Hall motion",
                "trigger": {
                    "platform": "state",
                    "entity_id": "binary_sensor.motion",
                    "to": "on",
                },
                "action": [TURN_ON_HALL],
            }
        ],
    )

    jarvis.states.set("binary_sensor.motion", "off")
    await _settle(jarvis)
    assert light.calls == []

    jarvis.states.set("binary_sensor.motion", "on")
    await _settle(jarvis)

    assert light.actions == ["turn_on"]
    assert light.data_for()["entity_id"] == "light.hall"
    assert jarvis.states.is_state("light.hall", "on")


async def test_state_trigger_passes_trigger_variables(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Copy brightness",
                "trigger": {"platform": "state", "entity_id": "sensor.dial", "to": "42"},
                "action": [
                    {
                        "service": "light.turn_on",
                        "target": {"entity_id": "light.hall"},
                        "data": {
                            "brightness": "{{ trigger.to_state.state | int }}",
                            "was": "{{ trigger.from_state.state }}",
                        },
                    }
                ],
            }
        ],
    )

    jarvis.states.set("sensor.dial", "idle")
    jarvis.states.set("sensor.dial", "42")
    await _settle(jarvis)

    data = light.data_for()
    assert data["brightness"] == 42
    assert data["was"] == "idle"


async def test_state_trigger_ignores_attribute_only_change_when_filtered(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Filtered",
                "trigger": {"platform": "state", "entity_id": "sensor.dial", "to": "on"},
                "action": [TURN_ON_HALL],
            }
        ],
    )

    jarvis.states.set("sensor.dial", "on", {"a": 1})
    await _settle(jarvis)
    jarvis.states.set("sensor.dial", "on", {"a": 2})  # attribute-only
    await _settle(jarvis)

    assert light.actions == ["turn_on"]


async def test_state_trigger_attribute_filter(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Attribute watch",
                "trigger": {
                    "platform": "state",
                    "entity_id": "light.desk",
                    "attribute": "brightness",
                    "to": 255,
                },
                "action": [TURN_ON_HALL],
            }
        ],
    )

    jarvis.states.set("light.desk", "on", {"brightness": 10})
    await _settle(jarvis)
    assert light.calls == []

    jarvis.states.set("light.desk", "on", {"brightness": 255})
    await _settle(jarvis)
    assert light.actions == ["turn_on"]


async def test_numeric_state_trigger_above_and_below(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Too hot",
                "trigger": {
                    "platform": "numeric_state",
                    "entity_id": "sensor.temp",
                    "above": 25,
                },
                "action": [TURN_ON_HALL],
            },
            {
                "alias": "Too cold",
                "trigger": {
                    "platform": "numeric_state",
                    "entity_id": "sensor.temp",
                    "below": 5,
                },
                "action": [{"service": "light.turn_off", "target": {"entity_id": "light.hall"}}],
            },
        ],
    )

    jarvis.states.set("sensor.temp", "20")
    await _settle(jarvis)
    assert light.calls == []

    jarvis.states.set("sensor.temp", "26")
    await _settle(jarvis)
    assert light.actions == ["turn_on"]

    # Still above: a crossing already happened, so it must not re-fire.
    jarvis.states.set("sensor.temp", "27")
    await _settle(jarvis)
    assert light.actions == ["turn_on"]

    jarvis.states.set("sensor.temp", "2")
    await _settle(jarvis)
    assert light.actions == ["turn_on", "turn_off"]


async def test_numeric_state_trigger_uses_attribute_and_value_template(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Battery low",
                "trigger": {
                    "platform": "numeric_state",
                    "entity_id": "sensor.phone",
                    "attribute": "battery",
                    "below": 20,
                },
                "action": [TURN_ON_HALL],
            }
        ],
    )

    jarvis.states.set("sensor.phone", "ok", {"battery": 55})
    await _settle(jarvis)
    assert light.calls == []

    jarvis.states.set("sensor.phone", "ok", {"battery": 12})
    await _settle(jarvis)
    assert light.actions == ["turn_on"]


async def test_state_trigger_for_duration(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Quiet for a while",
                "trigger": {
                    "platform": "state",
                    "entity_id": "binary_sensor.motion",
                    "to": "off",
                    "for": 0.15,
                },
                "action": [TURN_ON_HALL],
            }
        ],
    )

    jarvis.states.set("binary_sensor.motion", "on")
    jarvis.states.set("binary_sensor.motion", "off")
    await asyncio.sleep(0.05)
    assert light.calls == []  # not held long enough yet

    await asyncio.sleep(0.2)
    assert light.actions == ["turn_on"]


async def test_state_trigger_for_duration_cancelled_by_change(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Held off",
                "trigger": {
                    "platform": "state",
                    "entity_id": "binary_sensor.motion",
                    "to": "off",
                    "for": "00:00:01",
                },
                "action": [TURN_ON_HALL],
            }
        ],
    )

    jarvis.states.set("binary_sensor.motion", "off")
    await asyncio.sleep(0.05)
    jarvis.states.set("binary_sensor.motion", "on")  # breaks the hold
    await asyncio.sleep(0.1)

    assert light.calls == []


async def test_event_trigger(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "On doorbell",
                "trigger": {
                    "platform": "event",
                    "event_type": "doorbell",
                    "event_data": {"button": "front"},
                },
                "action": [TURN_ON_HALL],
            }
        ],
    )

    await jarvis.bus.async_fire("doorbell", {"button": "back"})
    await _settle(jarvis)
    assert light.calls == []

    await jarvis.bus.async_fire("doorbell", {"button": "front"})
    await _settle(jarvis)
    assert light.actions == ["turn_on"]


async def test_template_trigger_fires_on_rising_edge(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Template",
                "trigger": {
                    "platform": "template",
                    "value_template": "{{ states('sensor.temp') | float(0) > 30 }}",
                },
                "action": [TURN_ON_HALL],
            }
        ],
    )

    jarvis.states.set("sensor.temp", "20")
    await _settle(jarvis)
    assert light.calls == []

    jarvis.states.set("sensor.temp", "35")
    await _settle(jarvis)
    assert light.actions == ["turn_on"]

    jarvis.states.set("sensor.temp", "36")  # still true: no second edge
    await _settle(jarvis)
    assert light.actions == ["turn_on"]


async def test_webhook_trigger_registers_a_handler(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Webhook",
                "trigger": {"platform": "webhook", "webhook_id": "abc123"},
                "action": [
                    {
                        "service": "light.turn_on",
                        "target": {"entity_id": "light.hall"},
                        "data": {"brightness": "{{ trigger.json.level }}"},
                    }
                ],
            }
        ],
    )

    handler = jarvis.data["webhooks"]["abc123"]
    await handler({"level": 99})
    await _settle(jarvis)

    assert light.data_for()["brightness"] == 99


async def test_jarvis_start_trigger(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "At boot",
                "trigger": {"platform": "jarvis_start"},
                "action": [TURN_ON_HALL],
            }
        ],
    )

    await jarvis.async_start()
    await _settle(jarvis)
    assert light.actions == ["turn_on"]


async def test_time_trigger_uses_the_injected_clock(jarvis):
    fired = []
    clock = FakeClock(datetime(2024, 1, 1, 6, 59, 59))
    jarvis.data["automation_clock"] = clock

    unsub = await async_attach_trigger(
        jarvis, {"platform": "time", "at": "07:00:00"}, lambda trigger: fired.append(trigger)
    )
    for _ in range(5):
        await asyncio.sleep(0)
    unsub()

    assert fired and fired[0]["platform"] == "time"
    assert clock.slept[0] == pytest.approx(1.0)


def test_the_clock_runs_in_the_configured_time_zone(jarvis):
    """`jarvis: time_zone:` decides when `at: "07:00:00"` means seven o'clock.

    It used to decide nothing. The key was echoed back by `/api/config` and read
    by nobody, so what actually timed every automation was the container's TZ —
    which is why docker-compose.yml carries a note that the two must agree "or
    every time trigger fires at the wrong hour, silently", and why a packaging
    test exists to check the defaults have not drifted.

    Two zones, chosen because neither is ever the runner's: Kathmandu is
    +05:45 and has no DST, so its offset is unmistakable and stable.
    """
    jarvis.config = {"jarvis": {"time_zone": "Asia/Kathmandu"}}
    now = get_clock(jarvis).now()
    assert now.utcoffset() == timedelta(hours=5, minutes=45)

    # And the trigger maths inherits it: `at:` is read in the configured zone,
    # not the process's, because next_time_of_day builds on `now`.
    nxt = next_time_of_day(now, parse_time("07:00:00"))
    assert nxt.hour == 7 and nxt.utcoffset() == timedelta(hours=5, minutes=45)

    jarvis.config = {"jarvis": {"time_zone": "Pacific/Chatham"}}
    assert get_clock(jarvis).now().utcoffset() in (
        timedelta(hours=12, minutes=45),
        timedelta(hours=13, minutes=45),  # daylight saving
    )


def test_an_unknown_time_zone_falls_back_instead_of_failing(jarvis, caplog):
    """A typo in the config must not stop the house working."""
    jarvis.config = {"jarvis": {"time_zone": "Mars/Olympus_Mons"}}
    now = get_clock(jarvis).now()
    assert now.tzinfo is not None  # still aware, just the system's zone
    assert "Mars/Olympus_Mons" in caplog.text

    # No zone configured at all is the same story, without the complaint.
    jarvis.config = {"jarvis": {}}
    assert get_clock(jarvis).now().tzinfo is not None


def test_an_injected_clock_still_beats_the_configured_zone(jarvis):
    """Tests that froze time must not find a real zone underneath them."""
    jarvis.config = {"jarvis": {"time_zone": "Asia/Kathmandu"}}
    frozen = FakeClock(datetime(2024, 1, 1, 6, 59, 59))
    jarvis.data["automation_clock"] = frozen
    assert get_clock(jarvis) is frozen


async def test_time_pattern_trigger_uses_the_injected_clock(jarvis):
    fired = []
    clock = FakeClock(datetime(2024, 1, 1, 10, 4, 55))
    jarvis.data["automation_clock"] = clock

    unsub = await async_attach_trigger(
        jarvis,
        {"platform": "time_pattern", "minutes": "/5"},
        lambda trigger: fired.append(trigger),
    )
    for _ in range(5):
        await asyncio.sleep(0)
    unsub()

    assert fired and fired[0]["platform"] == "time_pattern"
    assert clock.slept[0] == pytest.approx(5.0)  # next :05 boundary


class FakeMqttMessage:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload

    def json(self):
        return json.loads(self.payload)


class FakeMqttClient:
    """Just enough of the MQTT client contract for the trigger to bind."""

    def __init__(self):
        self.subscriptions = {}

    async def async_subscribe(self, topic, callback, qos=0):
        self.subscriptions.setdefault(topic, []).append(callback)

        def _unsub():
            self.subscriptions[topic].remove(callback)

        return _unsub

    async def publish(self, topic, payload):
        for callback in list(self.subscriptions.get(topic, [])):
            await callback(FakeMqttMessage(topic, payload))


async def test_mqtt_trigger(jarvis, light):
    client = FakeMqttClient()
    jarvis.data["mqtt"] = client

    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Button pressed",
                "trigger": {"platform": "mqtt", "topic": "zigbee/button", "payload": "single"},
                "action": [TURN_ON_HALL],
            }
        ],
    )
    assert "zigbee/button" in client.subscriptions

    await client.publish("zigbee/button", "double")
    await _settle(jarvis)
    assert light.calls == []

    await client.publish("zigbee/button", "single")
    await _settle(jarvis)
    assert light.actions == ["turn_on"]


async def test_mqtt_trigger_without_a_client_is_inert(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "No broker",
                "trigger": {"platform": "mqtt", "topic": "zigbee/button"},
                "action": [TURN_ON_HALL],
            }
        ],
    )
    assert jarvis.states.get("automation.no_broker") is not None


def test_time_pattern_maths():
    now = datetime(2024, 1, 1, 10, 3, 30)
    # Unspecified smaller units default to zero -> next 5-minute boundary.
    assert next_time_pattern(now, minutes="/5") == datetime(2024, 1, 1, 10, 5, 0)
    assert next_time_pattern(now, seconds="/15") == datetime(2024, 1, 1, 10, 3, 45)


def test_duration_and_time_parsing():
    assert parse_duration(5) == 5
    assert parse_duration("00:00:05") == 5
    assert parse_duration("00:02") == 120
    assert parse_duration({"minutes": 2, "seconds": 30}) == 150
    assert parse_time("07:30") == parse_time("07:30:00")


# ---------------------------------------------------------------------------
# conditions
# ---------------------------------------------------------------------------
async def test_condition_blocks_execution(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Only when dark",
                "trigger": {
                    "platform": "state",
                    "entity_id": "binary_sensor.motion",
                    "to": "on",
                },
                "condition": [
                    {"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"}
                ],
                "action": [TURN_ON_HALL],
            }
        ],
    )

    jarvis.states.set("sun.sun", "above_horizon")
    jarvis.states.set("binary_sensor.motion", "on")
    await _settle(jarvis)
    assert light.calls == []

    jarvis.states.set("sun.sun", "below_horizon")
    jarvis.states.set("binary_sensor.motion", "off")
    jarvis.states.set("binary_sensor.motion", "on")
    await _settle(jarvis)
    assert light.actions == ["turn_on"]


async def test_condition_shapes(jarvis):
    jarvis.states.set("sensor.temp", "22")
    jarvis.states.set("input_boolean.guest", "on")

    assert await async_check(
        jarvis, {"condition": "numeric_state", "entity_id": "sensor.temp", "above": 20}
    )
    assert not await async_check(
        jarvis, {"condition": "numeric_state", "entity_id": "sensor.temp", "below": 20}
    )
    assert await async_check(
        jarvis, {"condition": "template", "value_template": "{{ states('sensor.temp') == '22' }}"}
    )
    assert await async_check(
        jarvis,
        {
            "condition": "or",
            "conditions": [
                {"condition": "state", "entity_id": "sensor.temp", "state": "nope"},
                {"condition": "state", "entity_id": "input_boolean.guest", "state": "on"},
            ],
        },
    )
    assert await async_check(
        jarvis,
        {
            "condition": "not",
            "conditions": [
                {"condition": "state", "entity_id": "input_boolean.guest", "state": "off"}
            ],
        },
    )
    # A bare list is an implicit AND.
    assert await async_check(
        jarvis,
        [
            {"condition": "state", "entity_id": "input_boolean.guest", "state": "on"},
            {"condition": "numeric_state", "entity_id": "sensor.temp", "above": 10},
        ],
    )
    # Shorthand without an explicit `condition:` key.
    assert await async_check(jarvis, {"entity_id": "input_boolean.guest", "state": "on"})


async def test_time_condition_with_injected_clock(jarvis):
    jarvis.data["automation_clock"] = FakeClock(datetime(2024, 1, 3, 22, 30))  # Wednesday
    assert await async_check(jarvis, {"condition": "time", "after": "22:00"})
    assert not await async_check(jarvis, {"condition": "time", "before": "22:00"})
    assert await async_check(
        jarvis, {"condition": "time", "after": "21:00", "before": "23:00", "weekday": ["wed"]}
    )
    assert not await async_check(
        jarvis, {"condition": "time", "after": "21:00", "weekday": ["mon"]}
    )
    # Window that wraps past midnight.
    assert await async_check(jarvis, {"condition": "time", "after": "20:00", "before": "06:00"})


async def test_trigger_id_condition(jarvis):
    variables = {"trigger": {"platform": "state", "id": "motion"}}
    assert await async_check(jarvis, {"condition": "trigger", "id": "motion"}, variables)
    assert not await async_check(jarvis, {"condition": "trigger", "id": "other"}, variables)


# ---------------------------------------------------------------------------
# script executor
# ---------------------------------------------------------------------------
async def test_choose_picks_the_right_branch(jarvis, light):
    jarvis.states.set("input_select.mode", "night")
    sequence = [
        {
            "choose": [
                {
                    "conditions": [
                        {"condition": "state", "entity_id": "input_select.mode", "state": "day"}
                    ],
                    "sequence": [
                        {
                            "service": "light.turn_on",
                            "target": {"entity_id": "light.day"},
                        }
                    ],
                },
                {
                    "conditions": [
                        {
                            "condition": "state",
                            "entity_id": "input_select.mode",
                            "state": "night",
                        }
                    ],
                    "sequence": [
                        {
                            "service": "light.turn_on",
                            "target": {"entity_id": "light.night"},
                        }
                    ],
                },
            ],
            "default": [
                {"service": "light.turn_on", "target": {"entity_id": "light.fallback"}}
            ],
        }
    ]

    await async_execute_script(jarvis, sequence, {})
    assert light.data_for()["entity_id"] == "light.night"

    jarvis.states.set("input_select.mode", "weird")
    await async_execute_script(jarvis, sequence, {})
    assert light.data_for(1)["entity_id"] == "light.fallback"


async def test_if_then_else(jarvis, light):
    jarvis.states.set("binary_sensor.home", "off")
    sequence = [
        {
            "if": [{"condition": "state", "entity_id": "binary_sensor.home", "state": "on"}],
            "then": [{"service": "light.turn_on", "target": {"entity_id": "light.a"}}],
            "else": [{"service": "light.turn_on", "target": {"entity_id": "light.b"}}],
        }
    ]
    await async_execute_script(jarvis, sequence, {})
    assert light.data_for()["entity_id"] == "light.b"


async def test_repeat_count_and_for_each(jarvis, light):
    await async_execute_script(
        jarvis,
        [
            {
                "repeat": {
                    "count": 3,
                    "sequence": [
                        {
                            "service": "light.turn_on",
                            "target": {"entity_id": "light.hall"},
                            "data": {"brightness": "{{ repeat.index }}"},
                        }
                    ],
                }
            }
        ],
        {},
    )
    assert light.actions == ["turn_on"] * 3
    assert [call[1]["brightness"] for call in light.calls] == [1, 2, 3]

    light.calls.clear()
    await async_execute_script(
        jarvis,
        [
            {
                "repeat": {
                    "for_each": ["light.a", "light.b"],
                    "sequence": [
                        {
                            "service": "light.turn_on",
                            "target": {"entity_id": "{{ repeat.item }}"},
                        }
                    ],
                }
            }
        ],
        {},
    )
    assert [call[1]["entity_id"] for call in light.calls] == ["light.a", "light.b"]


async def test_repeat_until(jarvis, light):
    await async_execute_script(
        jarvis,
        [
            {"variables": {"counter": 0}},
            {
                "repeat": {
                    "sequence": [
                        {"variables": {"counter": "{{ counter + 1 }}"}},
                        {"service": "light.turn_on", "target": {"entity_id": "light.hall"}},
                    ],
                    "until": ["{{ counter >= 3 }}"],
                }
            },
        ],
        {},
    )
    assert len(light.calls) == 3


async def test_delay_step(jarvis, light):
    started = asyncio.get_running_loop().time()
    await async_execute_script(
        jarvis,
        [{"delay": "00:00:00"}, {"delay": 0.05}, TURN_ON_HALL],
        {},
    )
    assert light.actions == ["turn_on"]
    assert asyncio.get_running_loop().time() - started >= 0.05


async def test_wait_template_completes_and_times_out(jarvis, light):
    jarvis.states.set("binary_sensor.door", "closed")

    async def _open_later():
        await asyncio.sleep(0.05)
        jarvis.states.set("binary_sensor.door", "open")

    task = asyncio.create_task(_open_later())
    await async_execute_script(
        jarvis,
        [
            {
                "wait_template": "{{ is_state('binary_sensor.door', 'open') }}",
                "timeout": 2,
            },
            TURN_ON_HALL,
        ],
        {},
    )
    await task
    assert light.actions == ["turn_on"]

    # Timeout with continue_on_timeout: false must stop the script.
    light.calls.clear()
    await async_execute_script(
        jarvis,
        [
            {
                "wait_template": "{{ is_state('binary_sensor.door', 'closed') }}",
                "timeout": 0.05,
                "continue_on_timeout": False,
            },
            TURN_ON_HALL,
        ],
        {},
    )
    assert light.calls == []

    # Same timeout, but allowed to continue.
    await async_execute_script(
        jarvis,
        [
            {
                "wait_template": "{{ is_state('binary_sensor.door', 'closed') }}",
                "timeout": 0.05,
            },
            TURN_ON_HALL,
        ],
        {},
    )
    assert light.actions == ["turn_on"]


async def test_wait_for_trigger(jarvis, light):
    async def _fire_later():
        await asyncio.sleep(0.05)
        jarvis.states.set("binary_sensor.button", "on")

    task = asyncio.create_task(_fire_later())
    await async_execute_script(
        jarvis,
        [
            {
                "wait_for_trigger": [
                    {"platform": "state", "entity_id": "binary_sensor.button", "to": "on"}
                ],
                "timeout": 2,
            },
            TURN_ON_HALL,
        ],
        {},
    )
    await task
    assert light.actions == ["turn_on"]


async def test_parallel_and_event_steps(jarvis, light):
    seen = []
    jarvis.bus.listen("script_done", lambda event: seen.append(event.data))

    await async_execute_script(
        jarvis,
        [
            {
                "parallel": [
                    {"service": "light.turn_on", "target": {"entity_id": "light.a"}},
                    {
                        "sequence": [
                            {"service": "light.turn_on", "target": {"entity_id": "light.b"}}
                        ]
                    },
                ]
            },
            {"event": "script_done", "event_data": {"ok": True}},
        ],
        {},
    )

    assert sorted(call[1]["entity_id"] for call in light.calls) == ["light.a", "light.b"]
    assert seen == [{"ok": True}]


async def test_condition_step_stops_the_script(jarvis, light):
    jarvis.states.set("input_boolean.guest", "off")
    await async_execute_script(
        jarvis,
        [
            {"condition": "state", "entity_id": "input_boolean.guest", "state": "on"},
            TURN_ON_HALL,
        ],
        {},
    )
    assert light.calls == []


async def test_stop_returns_a_response(jarvis):
    response = await async_execute_script(
        jarvis,
        [
            {"variables": {"result": {"answer": 42}}},
            {"stop": "done", "response_variable": "result"},
            {"service": "light.turn_on"},  # never reached
        ],
        {},
    )
    assert response == {"answer": 42}


async def test_service_response_variable(jarvis):
    async def _lookup(call):
        return {"value": call.get("key", "").upper()}

    jarvis.services.register("demo", "lookup", _lookup, supports_response=True)

    response = await async_execute_script(
        jarvis,
        [
            {"service": "demo.lookup", "data": {"key": "abc"}, "response_variable": "found"},
            {"stop": "ok", "response_variable": "found"},
        ],
        {},
    )
    assert response == {"value": "ABC"}


# ---------------------------------------------------------------------------
# automation entity + modes
# ---------------------------------------------------------------------------
async def test_automation_entity_state_and_last_triggered(jarvis, light):
    manager = await _setup_automations(
        jarvis,
        [
            {
                "id": "hall_motion",
                "alias": "Hall Motion",
                "trigger": {
                    "platform": "state",
                    "entity_id": "binary_sensor.motion",
                    "to": "on",
                },
                "action": [TURN_ON_HALL],
            }
        ],
    )

    state = jarvis.states.get("automation.hall_motion")
    assert state is not None
    assert state.state == "on"
    # Entity drops None attributes, so "never triggered" shows up as absence.
    assert "last_triggered" not in state.attributes
    assert state.attributes["friendly_name"] == "Hall Motion"
    assert state.attributes["mode"] == "single"

    jarvis.states.set("binary_sensor.motion", "on")
    await _settle(jarvis)

    assert jarvis.states.get("automation.hall_motion").attributes["last_triggered"] is not None
    assert light.actions == ["turn_on"]

    # turn_off disables it without removing the entity
    await jarvis.async_call_service(
        "automation", "turn_off", {"entity_id": "automation.hall_motion"}
    )
    assert jarvis.states.is_state("automation.hall_motion", "off")

    light.calls.clear()
    jarvis.states.set("binary_sensor.motion", "off")
    jarvis.states.set("binary_sensor.motion", "on")
    await _settle(jarvis)
    assert light.calls == []

    await jarvis.async_call_service(
        "automation", "turn_on", {"entity_id": "automation.hall_motion"}
    )
    assert jarvis.states.is_state("automation.hall_motion", "on")
    assert manager.get("automation.hall_motion").enabled


async def test_automation_triggered_event(jarvis, light):
    events = []
    jarvis.bus.listen("automation_triggered", lambda event: events.append(event.data))

    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Noisy",
                "trigger": {"platform": "event", "event_type": "ping"},
                "action": [TURN_ON_HALL],
            }
        ],
    )
    await jarvis.bus.async_fire("ping", {})
    await _settle(jarvis)

    assert events and events[0]["name"] == "Noisy"
    assert events[0]["entity_id"] == "automation.noisy"


async def test_mode_single_prevents_overlap(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Slow",
                "mode": "single",
                "trigger": {"platform": "event", "event_type": "go"},
                "action": [{"delay": 0.2}, TURN_ON_HALL],
            }
        ],
    )

    await jarvis.bus.async_fire("go", {})
    await asyncio.sleep(0)  # let the first run start and hit the delay
    await jarvis.bus.async_fire("go", {})
    await asyncio.sleep(0)

    assert jarvis.states.get("automation.slow").attributes["current"] == 1
    await asyncio.sleep(0.3)
    assert light.actions == ["turn_on"]  # the second trigger was dropped


async def test_mode_restart_cancels_the_previous_run(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Restarting",
                "mode": "restart",
                "trigger": {"platform": "event", "event_type": "go"},
                "action": [
                    {"delay": 0.15},
                    {
                        "service": "light.turn_on",
                        "target": {"entity_id": "light.hall"},
                        "data": {"brightness": "{{ trigger.event_data.level }}"},
                    },
                ],
            }
        ],
    )

    await jarvis.bus.async_fire("go", {"level": 1})
    await asyncio.sleep(0.02)
    await jarvis.bus.async_fire("go", {"level": 2})
    await asyncio.sleep(0.3)

    assert len(light.calls) == 1
    assert light.data_for()["brightness"] == 2


async def test_mode_parallel_runs_both(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Parallel",
                "mode": "parallel",
                "max": 5,
                "trigger": {"platform": "event", "event_type": "go"},
                "action": [{"delay": 0.05}, TURN_ON_HALL],
            }
        ],
    )

    await jarvis.bus.async_fire("go", {})
    await jarvis.bus.async_fire("go", {})
    await asyncio.sleep(0.2)
    assert light.actions == ["turn_on", "turn_on"]


async def test_mode_queued_runs_in_order(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Queued",
                "mode": "queued",
                "max": 5,
                "trigger": {"platform": "event", "event_type": "go"},
                "action": [
                    {"delay": 0.05},
                    {
                        "service": "light.turn_on",
                        "target": {"entity_id": "light.hall"},
                        "data": {"brightness": "{{ trigger.event_data.level }}"},
                    },
                ],
            }
        ],
    )

    await jarvis.bus.async_fire("go", {"level": 1})
    await asyncio.sleep(0)
    await jarvis.bus.async_fire("go", {"level": 2})
    assert jarvis.states.get("automation.queued").attributes["current"] == 2

    await asyncio.sleep(0.3)
    assert [call[1]["brightness"] for call in light.calls] == [1, 2]


async def test_automation_variables_block(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "With variables",
                "variables": {"target": "light.study"},
                "trigger": {"platform": "event", "event_type": "go"},
                "action": [
                    {"service": "light.turn_on", "target": {"entity_id": "{{ target }}"}}
                ],
            }
        ],
    )

    await jarvis.bus.async_fire("go", {})
    await _settle(jarvis)
    assert light.data_for()["entity_id"] == "light.study"


async def test_trigger_id_reaches_the_conditions(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Ids",
                "trigger": [
                    {"platform": "event", "event_type": "go", "id": "wanted"},
                    {"platform": "event", "event_type": "nope", "id": "ignored"},
                ],
                "condition": [{"condition": "trigger", "id": "wanted"}],
                "action": [TURN_ON_HALL],
            }
        ],
    )

    await jarvis.bus.async_fire("nope", {})
    await _settle(jarvis)
    assert light.calls == []

    await jarvis.bus.async_fire("go", {})
    await _settle(jarvis)
    assert light.actions == ["turn_on"]


async def test_automation_trigger_service_skips_conditions(jarvis, light):
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Guarded",
                "trigger": {"platform": "event", "event_type": "never"},
                "condition": [{"condition": "template", "value_template": "{{ false }}"}],
                "action": [TURN_ON_HALL],
            }
        ],
    )

    await jarvis.async_call_service(
        "automation", "trigger", {"entity_id": "automation.guarded"}
    )
    assert light.actions == ["turn_on"]

    light.calls.clear()
    await jarvis.async_call_service(
        "automation",
        "trigger",
        {"entity_id": "automation.guarded", "skip_condition": False},
    )
    await _settle(jarvis)
    assert light.calls == []


# ---------------------------------------------------------------------------
# scripts
# ---------------------------------------------------------------------------
async def test_script_with_fields_returns_a_stop_response(jarvis, light):
    await script_integration.async_setup(
        jarvis,
        {
            "set_room": {
                "alias": "Set room",
                "description": "Turn on a room's light and report back.",
                "fields": {
                    "room": {"description": "Room to light up", "required": True},
                    "level": {"description": "Brightness 0-255", "example": 128},
                },
                "sequence": [
                    {
                        "service": "light.turn_on",
                        "target": {"entity_id": "light.{{ room }}"},
                        "data": {"brightness": "{{ level }}"},
                    },
                    {"variables": {"result": {"lit": "{{ room }}", "ok": True}}},
                    {"stop": "done", "response_variable": "result"},
                ],
            }
        },
    )

    assert jarvis.services.has_service("script", "set_room")
    assert jarvis.services.services["script"]["set_room"].supports_response is True
    metadata = jarvis.data["scripts"]["set_room"]
    assert metadata["fields"]["room"]["required"] is True
    assert metadata["description"].startswith("Turn on a room")
    assert metadata["entity_id"] == "script.set_room"

    response = await jarvis.async_call_service(
        "script", "set_room", {"room": "kitchen", "level": 128}, return_response=True
    )

    assert response == {"lit": "kitchen", "ok": True}
    assert light.data_for()["entity_id"] == "light.kitchen"
    assert light.data_for()["brightness"] == 128

    state = jarvis.states.get("script.set_room")
    assert state.state == "off"  # finished
    assert state.attributes["last_triggered"] is not None
    assert state.attributes["friendly_name"] == "Set room"


async def test_script_turn_on_service_and_mode_single(jarvis, light):
    await script_integration.async_setup(
        jarvis,
        {
            "slow": {
                "mode": "single",
                "sequence": [{"delay": 0.15}, TURN_ON_HALL],
            }
        },
    )

    await jarvis.async_call_service("script", "turn_on", {"entity_id": "script.slow"})
    await asyncio.sleep(0)
    assert jarvis.states.is_state("script.slow", "on")

    await jarvis.async_call_service("script", "turn_on", {"entity_id": "script.slow"})
    await asyncio.sleep(0)
    assert jarvis.states.get("script.slow").attributes["current"] == 1

    await asyncio.sleep(0.25)
    assert light.actions == ["turn_on"]
    assert jarvis.states.is_state("script.slow", "off")


async def test_automation_can_call_a_script(jarvis, light):
    await script_integration.async_setup(
        jarvis,
        {"lights_on": {"sequence": [TURN_ON_HALL]}},
    )
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Via script",
                "trigger": {"platform": "event", "event_type": "go"},
                "action": [{"service": "script.lights_on"}],
            }
        ],
    )

    await jarvis.bus.async_fire("go", {})
    await _settle(jarvis)
    assert light.actions == ["turn_on"]


# ---------------------------------------------------------------------------
# scenes
# ---------------------------------------------------------------------------
async def test_scene_applies_states(jarvis, light):
    await scene_integration.async_setup(
        jarvis,
        [
            {
                "name": "Movie time",
                "entities": {
                    "light.living_room": {"state": "on", "brightness": 40},
                    "light.hall": "off",
                    "sensor.mood": "cinematic",
                },
            }
        ],
    )

    assert jarvis.states.get("scene.movie_time").state == "unknown"

    await jarvis.async_call_service(
        "scene", "turn_on", {"entity_id": "scene.movie_time"}
    )

    assert light.calls[0][0] == "turn_on"
    assert light.calls[0][1] == {"entity_id": "light.living_room", "brightness": 40}
    assert light.calls[1] == ("turn_off", {"entity_id": "light.hall"})
    assert jarvis.states.is_state("light.living_room", "on")
    # No sensor domain services: the scene writes that state directly.
    assert jarvis.states.is_state("sensor.mood", "cinematic")
    assert jarvis.states.get("scene.movie_time").state != "unknown"


async def test_scene_apply_service(jarvis, light):
    await scene_integration.async_setup(jarvis, [])
    await jarvis.async_call_service(
        "scene", "apply", {"entities": {"light.desk": {"state": "on", "brightness": 12}}}
    )
    assert light.data_for() == {"entity_id": "light.desk", "brightness": 12}


async def test_scene_activated_from_a_script(jarvis, light):
    await scene_integration.async_setup(
        jarvis, [{"name": "Night", "entities": {"light.hall": "off"}}]
    )
    await async_execute_script(jarvis, [{"scene": "scene.night"}], {})
    assert light.actions == ["turn_off"]


# ---------------------------------------------------------------------------
# input helpers
# ---------------------------------------------------------------------------
async def test_input_boolean_turn_on_persists(jarvis):
    jarvis.config = {"input_boolean": {"guest_mode": {"name": "Guest mode", "initial": "off"}}}
    await input_integration.async_setup(jarvis, None)

    assert jarvis.states.is_state("input_boolean.guest_mode", "off")

    await jarvis.async_call_service(
        "input_boolean", "turn_on", {"entity_id": "input_boolean.guest_mode"}
    )
    state = jarvis.states.get("input_boolean.guest_mode")
    assert state.state == "on"
    assert state.attributes["friendly_name"] == "Guest mode"

    stored = json.loads((jarvis.config_dir / ".storage" / "input_helpers.json").read_text())
    assert stored["data"]["input_boolean"]["guest_mode"] == "on"

    # A fresh instance over the same config dir restores the saved value.
    reborn = Jarvis(jarvis.config_dir)
    reborn.config = jarvis.config
    await input_integration.async_setup(reborn, None)
    assert reborn.states.is_state("input_boolean.guest_mode", "on")


async def test_input_number_select_and_text(jarvis):
    jarvis.config = {
        "input_number": {"volume": {"min": 0, "max": 100, "step": 5, "initial": 30}},
        "input_select": {"house_mode": {"options": ["home", "away", "night"]}},
        "input_text": {"note": {"initial": "hi", "max": 10}},
    }
    await input_integration.async_setup(jarvis, None)

    assert jarvis.states.get("input_number.volume").state == "30"
    await jarvis.async_call_service(
        "input_number", "set_value", {"entity_id": "input_number.volume", "value": 250}
    )
    assert jarvis.states.get("input_number.volume").state == "100"  # clamped
    await jarvis.async_call_service(
        "input_number", "decrement", {"entity_id": "input_number.volume"}
    )
    assert jarvis.states.get("input_number.volume").state == "95"

    assert jarvis.states.is_state("input_select.house_mode", "home")
    await jarvis.async_call_service(
        "input_select",
        "select_option",
        {"entity_id": "input_select.house_mode", "option": "night"},
    )
    assert jarvis.states.is_state("input_select.house_mode", "night")
    await jarvis.async_call_service(
        "input_select",
        "select_option",
        {"entity_id": "input_select.house_mode", "option": "bogus"},
    )
    assert jarvis.states.is_state("input_select.house_mode", "night")  # rejected

    await jarvis.async_call_service(
        "input_text", "set_value", {"entity_id": "input_text.note", "value": "hello"}
    )
    assert jarvis.states.is_state("input_text.note", "hello")
    await jarvis.async_call_service(
        "input_text",
        "set_value",
        {"entity_id": "input_text.note", "value": "far too long to fit"},
    )
    assert jarvis.states.is_state("input_text.note", "hello")  # rejected


async def test_input_datetime_set(jarvis):
    jarvis.config = {
        "input_datetime": {"alarm": {"has_date": False, "has_time": True, "initial": "07:00:00"}}
    }
    await input_integration.async_setup(jarvis, None)

    assert jarvis.states.is_state("input_datetime.alarm", "07:00:00")
    await jarvis.async_call_service(
        "input_datetime", "set_datetime", {"entity_id": "input_datetime.alarm", "time": "06:30"}
    )
    assert jarvis.states.is_state("input_datetime.alarm", "06:30:00")


async def test_automation_bootstraps_input_helpers(jarvis, light):
    """`input_boolean:` is not an integration key, so automation loads it."""
    jarvis.config = {
        "input_boolean": {"guest_mode": {}},
        "automation": [
            {
                "alias": "Guest lights",
                "trigger": {
                    "platform": "state",
                    "entity_id": "input_boolean.guest_mode",
                    "to": "on",
                },
                "action": [TURN_ON_HALL],
            }
        ],
    }
    await automation_integration.async_setup(jarvis, jarvis.config["automation"])

    assert jarvis.states.is_state("input_boolean.guest_mode", "off")
    await jarvis.async_call_service(
        "input_boolean", "turn_on", {"entity_id": "input_boolean.guest_mode"}
    )
    await _settle(jarvis)
    assert light.actions == ["turn_on"]


# ---------------------------------------------------------------------------
# end to end through the integration loader
# ---------------------------------------------------------------------------
async def test_full_yaml_setup_through_the_loader(jarvis):
    """The loader path: automation is core, and it bootstraps input helpers."""
    await jarvis.async_setup(
        {
            "input_boolean": {"guest_mode": {"name": "Guest mode"}},
            "scene": [{"name": "Away", "entities": {"light.hall": "off"}}],
            "script": {"welcome": {"sequence": [TURN_ON_HALL]}},
            "automation": [
                {
                    "alias": "Guest arrives",
                    "trigger": {
                        "platform": "state",
                        "entity_id": "input_boolean.guest_mode",
                        "to": "on",
                    },
                    "action": [{"service": "script.welcome"}],
                }
            ],
        }
    )

    assert jarvis.states.get("automation.guest_arrives") is not None
    assert jarvis.states.get("script.welcome") is not None
    assert jarvis.states.get("scene.away") is not None
    assert jarvis.states.is_state("input_boolean.guest_mode", "off")

    # The real domains layer owns light.turn_on after setup; take it over so
    # this test stays independent of which light platforms exist.
    light = FakeLight(jarvis)

    await jarvis.async_call_service(
        "input_boolean", "turn_on", {"entity_id": "input_boolean.guest_mode"}
    )
    await _settle(jarvis)
    assert light.actions == ["turn_on"]

    await jarvis.async_call_service("scene", "turn_on", {"entity_id": "scene.away"})
    assert light.actions == ["turn_on", "turn_off"]


# ---------------------------------------------------------------------------
# manager housekeeping
# ---------------------------------------------------------------------------
async def test_reload_replaces_automations(jarvis, light):
    manager = await _setup_automations(
        jarvis,
        [
            {
                "id": "one",
                "alias": "One",
                "trigger": {"platform": "event", "event_type": "go"},
                "action": [TURN_ON_HALL],
            }
        ],
    )
    assert isinstance(manager, AutomationManager)

    await manager.async_reload(
        [
            {
                "id": "two",
                "alias": "Two",
                "trigger": {"platform": "event", "event_type": "go"},
                "action": [{"service": "light.turn_off", "target": {"entity_id": "light.hall"}}],
            }
        ]
    )

    assert jarvis.states.get("automation.one") is None
    assert jarvis.states.get("automation.two") is not None

    await jarvis.bus.async_fire("go", {})
    await _settle(jarvis)
    assert light.actions == ["turn_off"]  # only the new automation ran


# ---------------------------------------------------------------------------
# regression tests (verify pass)
#
# Each of these fails against the implementation as originally written.
# ---------------------------------------------------------------------------
async def test_stopping_a_script_releases_its_run_slot(jarvis, light):
    """turn_off immediately after turn_on must not wedge `single` mode.

    Nothing awaits between two service calls, so the run task is cancelled
    before the loop ever ran its first step. Releasing the run slot from the
    coroutine's `finally` leaked one slot per cancel — `current` stayed at 1,
    the entity stayed `on`, and `single` mode never admitted a run again.
    """
    await script_integration.async_setup(
        jarvis, {"slow": {"mode": "single", "sequence": [{"delay": 60}, TURN_ON_HALL]}}
    )
    script = jarvis.data["script_objects"]["script.slow"]

    await jarvis.async_call_service("script", "turn_on", {"entity_id": "script.slow"})
    await jarvis.async_call_service("script", "turn_off", {"entity_id": "script.slow"})
    await _settle(jarvis)

    assert script.current == 0
    assert jarvis.states.get("script.slow").state == "off"

    # ...and the script is runnable again.
    await jarvis.async_call_service(
        "script", "turn_on", {"entity_id": "script.slow"}
    )
    await asyncio.sleep(0)
    assert script.current == 1
    script.stop()
    await _settle(jarvis)


async def test_restart_mode_run_counter_settles_back_to_one(jarvis, light):
    """Hammering a `restart` script must not inflate the run counter."""
    await script_integration.async_setup(
        jarvis, {"r": {"mode": "restart", "sequence": [{"delay": 60}]}}
    )
    script = jarvis.data["script_objects"]["script.r"]

    for _ in range(20):
        script.async_start()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert script.current == 1
    script.stop()
    await _settle(jarvis)
    assert script.current == 0
    assert jarvis.states.get("script.r").state == "off"


async def test_for_timer_survives_a_repeat_write(jarvis, light):
    """An attribute-only write must not cancel a running `for:` countdown.

    A motion sensor that reports its battery while the state sits at "off"
    used to reset (in fact cancel) the timer, so `for: 5 minutes` never fired.
    """
    fired = []

    async def _fire(trigger, context=None):
        fired.append(trigger)

    await async_attach_trigger(
        jarvis,
        {
            "platform": "state",
            "entity_id": "binary_sensor.motion",
            "to": "off",
            "for": 0.05,
        },
        _fire,
    )

    jarvis.states.set("binary_sensor.motion", "on")
    await jarvis.async_block_till_done()
    jarvis.states.set("binary_sensor.motion", "off")
    await jarvis.async_block_till_done()
    # same state, new attribute — the countdown must keep running
    jarvis.states.set("binary_sensor.motion", "off", {"battery": 80})
    await jarvis.async_block_till_done()

    await asyncio.sleep(0.15)
    assert len(fired) == 1

    # and a real departure still cancels it
    fired.clear()
    jarvis.states.set("binary_sensor.motion", "on")
    await jarvis.async_block_till_done()
    jarvis.states.set("binary_sensor.motion", "off")
    await jarvis.async_block_till_done()
    jarvis.states.set("binary_sensor.motion", "on")
    await jarvis.async_block_till_done()
    await asyncio.sleep(0.15)
    assert fired == []


async def test_detaching_a_trigger_kills_its_pending_for_timer(jarvis, light):
    """A detached trigger must never fire.

    Rescheduling a `for:` timer left the *replacement* task unreachable (the
    cancelled predecessor's `finally` popped the new task's key), so
    `cancel_all()` on detach missed it and a removed automation still ran.
    """
    fired = []

    async def _fire(trigger, context=None):
        fired.append(trigger)

    unsub = await async_attach_trigger(
        jarvis, {"platform": "state", "entity_id": "sensor.t", "for": 0.05}, _fire
    )

    jarvis.states.set("sensor.t", "1")
    await jarvis.async_block_till_done()
    await asyncio.sleep(0)
    jarvis.states.set("sensor.t", "2")  # reschedules the countdown
    await jarvis.async_block_till_done()
    await asyncio.sleep(0)  # let the cancelled predecessor unwind

    unsub()
    await asyncio.sleep(0.15)
    assert fired == []


async def test_reloading_an_automation_kills_its_pending_for_timer(jarvis, light):
    """The same leak, seen through the manager (a reloaded automation ran)."""
    manager = await _setup_automations(
        jarvis,
        [
            {
                "alias": "Late",
                "trigger": {
                    "platform": "state",
                    "entity_id": "sensor.probe",
                    "for": 0.05,
                },
                "action": [TURN_ON_HALL],
            }
        ],
    )

    jarvis.states.set("sensor.probe", "1")
    await jarvis.async_block_till_done()
    await asyncio.sleep(0)
    jarvis.states.set("sensor.probe", "2")
    await jarvis.async_block_till_done()
    await asyncio.sleep(0)

    await manager.async_remove_all()
    await asyncio.sleep(0.15)
    await _settle(jarvis)
    assert light.calls == []


async def test_script_services_need_an_explicit_target(jarvis, light):
    """`script.turn_on` with no entity_id must not run every script."""
    await script_integration.async_setup(
        jarvis,
        {
            "a": {"sequence": [TURN_ON_HALL]},
            "b": {"sequence": [TURN_ON_HALL]},
        },
    )

    await jarvis.async_call_service("script", "turn_on", {})
    await _settle(jarvis)
    assert light.calls == []

    await jarvis.async_call_service("script", "turn_on", {"entity_id": ""})
    await _settle(jarvis)
    assert light.calls == []

    # "all" is still an explicit, working opt-in
    await jarvis.async_call_service("script", "turn_on", {"entity_id": "all"})
    await _settle(jarvis)
    assert len(light.calls) == 2


async def test_automation_services_need_an_explicit_target(jarvis, light):
    """`automation.trigger`/`turn_off` with no target must not hit the house."""
    manager = await _setup_automations(
        jarvis,
        [
            {
                "alias": "One",
                "trigger": {"platform": "event", "event_type": "go"},
                "action": [TURN_ON_HALL],
            },
            {
                "alias": "Two",
                "trigger": {"platform": "event", "event_type": "go"},
                "action": [TURN_ON_HALL],
            },
        ],
    )

    await jarvis.async_call_service("automation", "trigger", {})
    await _settle(jarvis)
    assert light.calls == []

    await jarvis.async_call_service("automation", "turn_off", {})
    await _settle(jarvis)
    assert all(a.enabled for a in manager.all())

    await jarvis.async_call_service("automation", "turn_off", {"entity_id": "all"})
    assert not any(a.enabled for a in manager.all())


async def test_parallel_cancels_siblings_when_one_branch_fails(jarvis, light):
    """A failing branch must not leave its siblings running detached."""

    async def _boom(call):
        raise RuntimeError("boom")

    jarvis.services.register("t", "boom", _boom)

    sequence = [
        {
            "parallel": [
                {"sequence": [{"service": "t.boom"}]},
                {"sequence": [{"delay": 0.05}, TURN_ON_HALL]},
            ]
        }
    ]

    with pytest.raises(RuntimeError):
        await async_execute_script(jarvis, sequence)

    await asyncio.sleep(0.15)
    assert light.calls == []  # the sibling was cancelled, not orphaned


async def test_quoted_initial_state_off_keeps_the_automation_disabled(jarvis, light):
    """`initial_state: "off"` is a string, and every non-empty string is truthy."""
    manager = await _setup_automations(
        jarvis,
        [
            {
                "alias": "Quiet",
                "initial_state": "off",
                "trigger": {"platform": "event", "event_type": "go"},
                "action": [TURN_ON_HALL],
            }
        ],
    )

    automation = manager.get("automation.quiet")
    assert automation is not None and automation.enabled is False
    assert jarvis.states.get("automation.quiet").state == "off"

    await jarvis.bus.async_fire("go", {})
    await _settle(jarvis)
    assert light.calls == []


async def test_skip_condition_false_as_a_string_is_honoured(jarvis, light):
    """`skip_condition: "false"` must not skip the conditions."""
    await _setup_automations(
        jarvis,
        [
            {
                "alias": "Guarded",
                "trigger": {"platform": "event", "event_type": "never"},
                "condition": [{"condition": "template", "value_template": "{{ false }}"}],
                "action": [TURN_ON_HALL],
            }
        ],
    )

    await jarvis.async_call_service(
        "automation",
        "trigger",
        {"entity_id": "automation.guarded", "skip_condition": "false"},
    )
    await _settle(jarvis)
    assert light.calls == []


async def test_an_unknown_mode_is_reported_as_the_mode_that_runs(jarvis, light):
    manager = await _setup_automations(
        jarvis,
        [
            {
                "alias": "Typo",
                "mode": "paralel",
                "trigger": {"platform": "event", "event_type": "go"},
                "action": [TURN_ON_HALL],
            }
        ],
    )
    automation = manager.get("automation.typo")
    assert automation.mode == "single"
    assert jarvis.states.get("automation.typo").attributes["mode"] == "single"


async def test_scene_attributes_cannot_retarget_the_service_call(jarvis, light):
    """An `entity_id` attribute in a scene entry must not redirect the call.

    Light/media groups expose `entity_id` as an attribute, so a scene built
    from a state dump used to actuate the group's members instead of the
    entity the scene actually named.
    """
    await scene_integration.async_setup(
        jarvis,
        [
            {
                "name": "Dumped",
                "entities": {
                    "light.hall": {
                        "state": "on",
                        "brightness": 40,
                        "entity_id": ["light.somewhere_else"],
                        "friendly_name": "Hall",
                    }
                },
            }
        ],
    )

    await jarvis.async_call_service("scene", "turn_on", {"entity_id": "scene.dumped"})
    await _settle(jarvis)

    assert light.actions == ["turn_on"]
    data = light.data_for()
    assert data["entity_id"] == "light.hall"
    assert data["brightness"] == 40
    assert "friendly_name" not in data


async def test_repeat_while_stays_cancellable_with_a_body_that_never_awaits(jarvis):
    """A template-only `repeat: while` must not monopolise the event loop.

    Nothing in `condition: template` + an empty sequence ever suspends, so the
    loop used to burn all 5000 iterations of the safety cap in a single
    uninterrupted stretch: the run was finished (and uncancellable) before any
    other task got a turn.
    """
    sequence = [
        {
            "repeat": {
                "while": [{"condition": "template", "value_template": "{{ true }}"}],
                "sequence": [],
            }
        }
    ]
    runner = asyncio.ensure_future(async_execute_script(jarvis, sequence))
    for _ in range(3):
        await asyncio.sleep(0)

    assert not runner.done()  # still yielding, a few iterations in
    runner.cancel()
    await asyncio.gather(runner, return_exceptions=True)


def test_collect_domains_reports_what_a_sequence_touches():
    from jarvis.automation.actions import DOMAIN_UNKNOWN, collect_domains

    assert collect_domains([{"service": "light.turn_on"}]) == {"light"}
    assert collect_domains(
        [
            {
                "choose": [
                    {
                        "conditions": [],
                        "sequence": [{"service": "lock.unlock"}],
                    }
                ],
                "default": [{"action": "notify.mobile"}],
            }
        ]
    ) == {"lock", "notify"}
    assert collect_domains(
        [{"repeat": {"count": 2, "sequence": [{"service": "cover.open_cover"}]}}]
    ) == {"cover"}
    assert collect_domains([{"parallel": [{"service": "lock.lock"}]}]) == {"lock"}
    assert collect_domains([{"scene": "scene.night"}]) == {"scene"}
    # unknowable targets fail open so callers can fail closed
    assert DOMAIN_UNKNOWN in collect_domains([{"service": "{{ svc }}"}])
    assert DOMAIN_UNKNOWN in collect_domains(
        [{"service": "light.turn_on", "target": {"area_id": "kitchen"}}]
    )


async def test_script_and_scene_publish_the_domains_they_touch(jarvis, light):
    await script_integration.async_setup(
        jarvis,
        {"open_up": {"sequence": [{"service": "lock.unlock",
                                   "target": {"entity_id": "lock.front"}}]}},
    )
    assert jarvis.data["scripts"]["open_up"]["domains"] == ["lock"]

    await scene_integration.async_setup(
        jarvis, [{"name": "Come home", "entities": {"lock.front": "unlocked"}}]
    )
    assert jarvis.data["scenes"]["scene.come_home"].domains == ["lock"]


async def test_time_condition_fails_closed_on_a_bound_it_cannot_read(jarvis):
    """An unreadable window must not pass at every hour of the day.

    `after`/`before` that failed to parse used to fall through as "no bound",
    so `after: sunset` (or a typo, or an unset input_datetime) silently
    matched around the clock.
    """
    assert await async_check(jarvis, {"condition": "time", "after": "not a time"}) is False
    assert await async_check(jarvis, {"condition": "time", "before": "25:99"}) is False
    # sun not set up yet
    assert await async_check(jarvis, {"condition": "time", "after": "sunset"}) is False
    # no bounds at all is still an unconditional pass
    assert await async_check(jarvis, {"condition": "time"}) is True


async def test_time_condition_understands_solar_bounds(jarvis):
    from jarvis.integrations import sun as sun_integration

    jarvis.config = {"jarvis": {"latitude": 51.5, "longitude": -0.12}}
    await sun_integration.async_setup(jarvis, {})

    now = datetime.now().astimezone()
    sunset = jarvis.data["sun"].next("sunset", now).astimezone(now.tzinfo).time()
    after_sunset = now.time() >= sunset

    assert (
        await async_check(jarvis, {"condition": "time", "after": "sunset"})
        is after_sunset
    )
    assert (
        await async_check(jarvis, {"condition": "time", "before": "sunset"})
        is not after_sunset
    )
    # offsets shift the bound
    class _FrozenClock:
        def __init__(self, when):
            self._when = when

        def now(self):
            return self._when

        async def sleep(self, seconds):
            pass

    just_before = now.replace(
        hour=sunset.hour, minute=sunset.minute, second=sunset.second
    ) - timedelta(minutes=15)
    jarvis.data["automation_clock"] = _FrozenClock(just_before)
    assert await async_check(jarvis, {"condition": "time", "after": "sunset"}) is False
    assert (
        await async_check(jarvis, {"condition": "time", "after": "sunset - 00:30"})
        is True
    )


async def test_reload_services_reread_the_config_directory(jarvis, light):
    """`automation.reload` / `script.reload` / `scene.reload` end to end.

    Only `AutomationManager.async_reload` was covered before; the services
    themselves (which re-read the config dir off the event loop) were not.
    """
    config_file = Path(jarvis.config_dir) / "configuration.yaml"
    config_file.write_text(
        "automation:\n"
        "  - alias: One\n"
        "    trigger: {platform: event, event_type: go}\n"
        "    action:\n"
        "      - service: light.turn_on\n"
        "        target: {entity_id: light.hall}\n"
        "script:\n"
        "  a:\n"
        "    sequence:\n"
        "      - service: light.turn_on\n"
        "        target: {entity_id: light.a}\n"
        "scene:\n"
        "  - name: Away\n"
        "    entities:\n"
        '      light.hall: "off"\n'
    )
    from jarvis.config import load_config

    jarvis.config = load_config(jarvis.config_dir)
    await automation_integration.async_setup(jarvis, jarvis.config.get("automation"))
    await script_integration.async_setup(jarvis, jarvis.config.get("script"))
    await scene_integration.async_setup(jarvis, jarvis.config.get("scene"))

    assert jarvis.states.get("automation.one") is not None
    assert jarvis.services.has_service("script", "a")
    assert jarvis.states.get("scene.away") is not None

    config_file.write_text(
        "automation:\n"
        "  - alias: Two\n"
        "    trigger: {platform: event, event_type: go}\n"
        "    action:\n"
        "      - service: light.turn_off\n"
        "        target: {entity_id: light.hall}\n"
        "script:\n"
        "  b:\n"
        "    sequence:\n"
        "      - service: light.turn_on\n"
        "        target: {entity_id: light.b}\n"
        "scene:\n"
        "  - name: Home\n"
        "    entities:\n"
        '      light.hall: "on"\n'
    )

    await jarvis.async_call_service("automation", "reload", {})
    await jarvis.async_call_service("script", "reload", {})
    await jarvis.async_call_service("scene", "reload", {})
    await _settle(jarvis)

    assert jarvis.states.get("automation.one") is None
    assert jarvis.states.get("automation.two") is not None
    assert not jarvis.services.has_service("script", "a")
    assert jarvis.services.has_service("script", "b")
    assert sorted(jarvis.data["scripts"]) == ["b"]
    assert jarvis.states.get("scene.away") is None
    assert jarvis.states.get("scene.home") is not None

    light.calls.clear()
    await jarvis.bus.async_fire("go", {})
    await _settle(jarvis)
    assert light.actions == ["turn_off"]  # only the reloaded automation ran
