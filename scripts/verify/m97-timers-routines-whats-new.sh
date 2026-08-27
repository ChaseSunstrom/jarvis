#!/usr/bin/env bash
# M97 — Routines read back, what's new. (Timers as entities follow separately.)
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M97" "routines read back, what's new"

check "a routine authored by voice is read back, listed, and an authored tool is on the record at tier 2" python3 -c '
from pathlib import Path
tools = Path("jarvis-core/jarvis/llm/tools.py").read_text()
assert "\"readback\": readback" in tools and "name=\"list_automations\"" in tools
authored = Path("jarvis-core/jarvis/automation/authored.py").read_text()
assert "def describe(config" in authored
common = Path("jarvis-core/jarvis/api/common.py").read_text()
assert "spec[\"tier\"] = 2" in common and "note_capability(" in common
notes = Path("jarvis-core/jarvis/integrations/notifications/__init__.py").read_text()
assert "name=\"whats_new\"" in notes and "async def note_capability" in notes
assert "note_capability" in Path("jarvis-core/jarvis/integrations/mcp/__init__.py").read_text()
assert "note_capability" in Path("jarvis-core/jarvis/integrations/extensions/__init__.py").read_text()
print("readback, list_automations, tier 2, capability moments, whats_new")
'
check "the readback reads like a person would say it" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.automation.authored import describe
cfg = {"alias": "Kitchen at seven", "trigger": [{"platform": "time", "at": "07:00"}],
       "condition": [{"condition": "time", "weekday": ["mon", "tue", "wed", "thu", "fri"]}],
       "action": [{"service": "light.turn_on", "target": {"entity_id": "light.kitchen_lights"}}]}
assert describe(cfg) == "weekdays at 07:00: turn on light.kitchen_lights", describe(cfg)
print(describe(cfg))
'
check_pytest "the automation API suite" 'cd jarvis-core && python3 -m pytest tests/test_automation_api.py -q --timeout=120 --timeout-method=signal'
check_pytest "the create_tool suite" 'cd jarvis-core && python3 -m pytest tests/test_create_tool_handler.py -q --timeout=120 --timeout-method=signal'
check_pytest "the notifications suite (whats_new)" 'cd jarvis-core && python3 -m pytest tests/test_notifications.py -q --timeout=120 --timeout-method=signal'
check "two scenarios, gated on M97" python3 -c '
import yaml
from pathlib import Path
for n in ("routine-by-voice", "tool-authored-and-listed"):
    assert yaml.safe_load(Path(f"testing/live/scenarios/{n}.yaml").read_text())["gated-on"] == "M97"
print("routine-by-voice, tool-authored-and-listed")
'
check_sh "on the house: a routine by voice read back and listed; what is new" \
    'LIVE_ONLY=routine-by-voice,tool-authored-and-listed bash scripts/verify/live_interaction.sh --full 2>&1 | tail -6'

verify_end
