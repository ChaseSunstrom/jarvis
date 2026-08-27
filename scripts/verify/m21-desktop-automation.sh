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
check_pytest "sequence tests" 'cd jarvis-core && python3 -m pytest tests/test_device_control_sequence.py -q --timeout=120 --timeout-method=signal'
check_sh "desktop agent exposes >= 21 actions" \
    'cd jarvis-desktop && python3 -c "from jarvis_desktop.actions.builtins import all_actions as a; import sys; n=len(a()); print(n); sys.exit(0 if n >= 21 else 1)"'
require_file jarvis-desktop/tests_e2e/test_agentic_automation.py
# What "exercises a Tier-3 approval" means, asked as behaviour rather than as a
# word: the suite sets a verdict, and asserts against the prompts the agent
# actually raised. A grep for "approval" passed on a comment for a while.
check "the e2e exercises a Tier-3 approval" python3 -c '
from pathlib import Path
text = Path("jarvis-desktop/tests_e2e/test_agentic_automation.py").read_text()
assert "set_consent(\"denied\")" in text, "no refused step"
assert "set_consent(\"approved\")" in text, "no approved step"
assert "control.prompts()" in text, "nothing asserts a human was asked"
'
check "the e2e watches the task events the UI shows" grep -qE 'jarvis_task_updated|jarvis_task_tool_started' jarvis-desktop/tests_e2e/test_agentic_automation.py
check_pytest "desktop agentic-automation e2e (harness + scripted model + real agent)" 'cd jarvis-desktop && timeout 900 python3 -m pytest tests_e2e/test_agentic_automation.py -q --timeout=600 --timeout-method=signal'
check "verification claim" grep -qi 'agentic automation' docs/verification.md
# No live scenarios of its own — this milestone does not add a capability
# anybody talks to. What it must not do is break the ones that exist, so a
# named smoke subset runs: house-light-on, chat-context-retention, lock-needs-a-human.
check_sh "the live smoke scenarios still pass" \
    'LIVE_ONLY=house-light-on,chat-context-retention,lock-needs-a-human bash scripts/verify/live_interaction.sh --implemented-only 2>&1 | tail -4'
verify_end
