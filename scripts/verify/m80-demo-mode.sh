#!/usr/bin/env bash
# M80 — Demo mode is a setting.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M80" "demo mode is a setting"

check "the demo integration has a switch and a way down" bash -c 'grep -q "async def async_remove_all" jarvis-core/jarvis/integrations/demo/__init__.py && grep -q "options.get(\"enabled\", True)" jarvis-core/jarvis/integrations/demo/__init__.py'
check "the registry has demo.enabled as a switch on Settings › House, applied live" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.settings import SETTINGS_BY_KEY
s = SETTINGS_BY_KEY["demo.enabled"]; assert s.type == "boolean" and s.group == "House" and s.apply == "live" and s.apply_hook; print(s.label, "-", s.note[:60])
'
check "the console plan and the mock carry the row" bash -c "grep -q \"key: 'demo.enabled'\" jarvis-web/src/lib/sections/settingsPlan.ts && grep -q \"key: 'demo.enabled'\" tests/web/mock-ha.mjs"
check "the model can name it: 'demo mode' resolves to the one setting" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.settings import resolve_setting
s = resolve_setting("demo mode"); assert s is not None and s.key == "demo.enabled", s; print("demo mode ->", s.key)
'
check_sh "the demo-mode suite: off removes the house live, on brings it back, off at boot clears stale entries" \
    'cd jarvis-core && python3 -m pytest tests/test_demo_mode.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -1'
check_sh "the settings suites" 'cd jarvis-core && python3 -m pytest tests/test_settings.py tests/test_settings_api.py tests/test_settings_tool.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -1'

verify_end
