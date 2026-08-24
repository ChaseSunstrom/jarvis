#!/usr/bin/env bash
# M12 — hooks: event-driven triggers for the wake word, task lifecycle
# (start/complete/fail), schedules and inbound webhooks — each a named
# trigger platform with an example and a test, not a recipe of generic events.
source "$(dirname "$0")/lib.sh"
verify_begin "M12" "hooks: wake word, task lifecycle, schedules, inbound webhooks"
use_venv
CORE=jarvis-core/jarvis
TRIG="$CORE/automation/triggers.py"

check "trigger platform: wake_word" grep -qE '"wake_word"' "$TRIG"
check "trigger platform: task (started/completed/failed)" grep -qE '"task"' "$TRIG"
check "task lifecycle events are distinct" grep -qE 'jarvis_task_(started|completed|failed)' "$CORE/tasks.py"
check "event triggers can match nested event_data" grep -qiE 'nested|dotted|path' "$TRIG"
check "trigger platform: webhook (kept)" grep -qE '"webhook"' "$TRIG"
check "trigger platform: time_pattern / time (kept)" grep -qE '"time_pattern"' "$TRIG"
check "inbound webhooks can require auth" grep -q 'webhook_require_auth' "$CORE/api/rest.py"
require_file jarvis-core/docs/hooks.md
for word in wake_word task webhook schedule; do
    check "docs/hooks.md documents: $word" grep -q "$word" jarvis-core/docs/hooks.md
done
require_file jarvis-core/config/examples/hooks.yaml
for word in wake_word task webhook; do
    check "examples/hooks.yaml shows: $word" grep -q "platform: $word" jarvis-core/config/examples/hooks.yaml
done
require_file jarvis-core/tests/test_hooks.py
for t in wake_word task_started task_completed task_failed webhook schedule; do
    check "test_hooks.py covers: $t" grep -qE "def test_[a-z_]*$t" jarvis-core/tests/test_hooks.py
done
check_sh "hooks + automation tests" \
    'cd jarvis-core && python3 -m pytest tests/test_hooks.py tests/test_automation.py tests/test_automation_api.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
verify_end
