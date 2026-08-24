#!/usr/bin/env bash
# M21 — agentic automation on the desktop: Jarvis plans and executes a
# multi-step automation against desktop-side capabilities, with a Tier-3
# approval in the middle and a failed step reported, proven end to end with a
# real jarvis-core (harness), a scripted model, and the real desktop agent.
source "$(dirname "$0")/lib.sh"
verify_begin "M21" "agentic automation on the desktop"
use_venv
CORE=jarvis-core/jarvis

check "device_control can run a sequence with state carried between steps" \
    grep -qE 'run_sequence' "$CORE/integrations/device_control/__init__.py"
require_file jarvis-core/tests/test_device_control_sequence.py
check_sh "sequence tests" \
    'cd jarvis-core && python3 -m pytest tests/test_device_control_sequence.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
check_sh "desktop agent exposes >= 21 actions" \
    'cd jarvis-desktop && python3 -c "from jarvis_desktop.actions.builtins import all_actions as a; import sys; n=len(a()); print(n); sys.exit(0 if n >= 21 else 1)"'
require_file jarvis-desktop/tests_e2e/test_agentic_automation.py
check "the e2e exercises a Tier-3 approval" grep -qiE 'approval|CONFIRM' jarvis-desktop/tests_e2e/test_agentic_automation.py
check "the e2e watches the task events the UI shows" grep -qE 'jarvis_task_updated|jarvis_task_tool_started' jarvis-desktop/tests_e2e/test_agentic_automation.py
check_sh "desktop agentic-automation e2e (harness + scripted model + real agent)" \
    'cd jarvis-desktop && timeout 900 python3 -m pytest tests_e2e/test_agentic_automation.py -q --timeout=600 --timeout-method=signal 2>&1 | tail -3'
check "verification claim" grep -qi 'agentic automation' docs/verification.md
verify_end
