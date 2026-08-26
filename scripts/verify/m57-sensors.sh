#!/usr/bin/env bash
# M57 — Any sensor.
#
# Home Assistant MQTT discovery already turned Zigbee2MQTT, ESPHome, rtl_433
# and friends into entities; what was missing was the edges: button presses
# and doorbells arrive as `event` and were dropped, the bridges re-announce
# on `homeassistant/status` and we never said "online" there, a radio hears
# the whole street, imperial readings sat beside metric ones, Tasmota and
# Shelly speak their own dialects, and the model had no tool to read a
# reading. Unit tests and fixtures only — the live scenario is the
# integrator's line, from the main checkout, never from a worktree.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M57" "any sensor"

MQTT=jarvis-core/jarvis/integrations/mqtt
require_file $MQTT/discovery.py
require_file $MQTT/entity.py
require_file $MQTT/translators.py
require_file jarvis-core/tests/test_mqtt_sensors.py
require_file testing/live/scenarios/sensors-discovered.yaml

check "event and device_tracker are components, not ignored" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.integrations.mqtt.discovery import IGNORED_COMPONENTS
from jarvis.integrations.mqtt.entity import ENTITY_CLASSES
assert "event" not in IGNORED_COMPONENTS and "event" in ENTITY_CLASSES
assert "device_tracker" in ENTITY_CLASSES
print("event and device_tracker are entities")
'
check "the birth is published where the bridges listen" grep -q '/status", DEFAULT_BIRTH_PAYLOAD' $MQTT/__init__.py
check "an allowlist keeps the street out of the house" grep -q "discovery_allow_ids" $MQTT/__init__.py
check "readings are canonical: one unit per device class at ingest" grep -q "def canonicalise" $MQTT/entity.py
check "Tasmota's own discovery and Shelly Gen2 are translated" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.integrations.mqtt import translators
assert callable(translators.tasmota_configs) and callable(translators.shelly_configs)
print("two translators")
'
check "the docstring no longer claims Tasmota publishes HA discovery" bash -c "! grep -qE 'Tasmota.*(publish|speaks).*(HA|Home Assistant).*discovery' $MQTT/__init__.py || grep -q 'tasmota/discovery' $MQTT/__init__.py"
check "fixtures from five sources" bash -c 'n=$(ls jarvis-core/tests/fixtures/mqtt_discovery/*.json | wc -l); test "$n" -ge 5 && echo "$n fixtures"'
check "the model has tools to read, compare and summarise readings" python3 -c '
from pathlib import Path
src = Path("jarvis-core/jarvis/integrations/sensors/__init__.py").read_text()
for name in ("sensor_readings", "sensor_compare", "sensor_history", "sensor_summary"):
    assert f"name=\"{name}\"" in src, f"no {name} tool"
print("four tools")
'
check_sh "the sensor tests: components, birth, allowlist, units, translators, tools, a malicious template" \
    'cd jarvis-core && python3 -m pytest tests/test_mqtt_sensors.py tests/test_mqtt.py -q --timeout=120 2>&1 | tail -2'
check_sh "packaging still agrees (the new config keys are read)" \
    'cd jarvis-core && python3 -m pytest tests/test_packaging.py -q --timeout=120 -k "config or shipped or example" 2>&1 | tail -2'
check "sensors and the new mqtt keys are switched on in the deployed config" python3 -c '
from pathlib import Path
text = Path("jarvis-core/config/configuration.yaml").read_text()
assert "\nsensors:\n" in text, "no sensors: block — the integration never loaded"
assert "canonical_units" in text and "discovery_allow_ids" in text, "the mqtt keys are not in the deployed config"
print("switched on")
'
check "the live scenario parses and publishes its own sensor" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.scenario import load_all
s = [x for x in load_all() if x.name == "sensors-discovered"][0]
assert s.capability == "sensors" and s.gated_on == "M57"
assert any(t.do.get("mqtt_publish") for t in s.turns), "no mqtt_publish do: action"
print("parses; publishes")
'
check "the rig can publish MQTT for a scenario" grep -q "mqtt_publish" testing/live/runner.py
check "ruff is clean" bash -c "cd jarvis-core && python3 -m ruff check jarvis/integrations/mqtt jarvis/integrations/sensors tests/test_mqtt_sensors.py && cd .. && python3 -m ruff check testing/live"

verify_end
