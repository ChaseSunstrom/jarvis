#!/usr/bin/env bash
# M04 — watching a task is watching Jarvis work: live steps, tool calls as
# they happen on ANY task, streamed output, progress, approve/cancel, and a
# browsable timeline — bound by a contract both suites read.
source "$(dirname "$0")/lib.sh"
verify_begin "M04" "task-execution UI"
use_venv
CORE=jarvis-core/jarvis

require_file tests/contracts/task_events.json
for ev in jarvis_task_added jarvis_task_updated jarvis_task_removed \
          jarvis_task_tool_started jarvis_task_tool_finished jarvis_task_output; do
    check "contract names $ev" grep -q "\"$ev\"" tests/contracts/task_events.json
done
check "jarvis-core reads the contract" grep -rlq task_events.json jarvis-core/tests
check "jarvis-web reads the contract" grep -rlq task_events.json jarvis-web/src

check "tasks.py: tool events on any task" grep -qE 'def (tool_started|tool_finished)' "$CORE/tasks.py"
check "tasks.py: streamed output on any task" grep -q jarvis_task_output "$CORE/tasks.py"
check "tasks.py: cooperative cancel every worker can honour" grep -q 'def raise_if_cancelled' "$CORE/tasks.py"
check "code jobs report tool calls live" grep -qE 'tool_started\(' "$CORE/integrations/code/agent.py"
check "code jobs stream check/command output" grep -qE '\.output\(' "$CORE/integrations/code/agent.py"
check "research tasks report tool calls live" grep -qE 'tool_started\(' "$CORE/integrations/research/__init__.py"
check "orchestrator jobs appear in the task registry" grep -qE 'TaskRegistry|tasks\.(async_add|add|create)' "$CORE/integrations/orchestrator/__init__.py"
check "per-task event log is persisted" grep -rqE 'task_events|events\.jsonl' "$CORE/tasks.py" "$CORE/integrations/recorder/__init__.py"

require_file "jarvis-web/src/routes/tasks/[id]/+page.svelte"
require_file jarvis-web/src/lib/components/TaskTimeline.svelte
require_file jarvis-web/src/lib/components/TaskOutput.svelte
check "task detail offers cancel" grep -qE 'jarvis/tasks/cancel|cancelTask' "jarvis-web/src/routes/tasks/[id]/+page.svelte"
check "mock backend streams task output" grep -q jarvis_task_output tests/web/mock-ha.mjs
check "mock backend streams task tool events" grep -q jarvis_task_tool_started tests/web/mock-ha.mjs

require_file jarvis-core/tests/test_task_events.py
check_sh "jarvis-core task tests" \
    'cd jarvis-core && python3 -m pytest tests/test_tasks.py tests/test_task_events.py tests/test_task_events_contract.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
ensure_web_deps
check_sh "web task reducers (vitest)" 'cd jarvis-web && npx vitest run src/lib/tasks.test.ts src/lib/taskEvents.test.ts 2>&1 | tail -3'
require_file jarvis-web/e2e/task-live.spec.ts
ensure_web_build
run_playwright "task UI e2e" e2e/tasks.spec.ts e2e/task-live.spec.ts
verify_end
